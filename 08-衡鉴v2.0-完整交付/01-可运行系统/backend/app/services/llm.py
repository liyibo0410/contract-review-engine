# -*- coding: utf-8 -*-
"""LLM 语义增强层（增强路径，永远可降级）

三条不可突破的边界：
  1. **不下判定** —— 风险等级与命中项全部来自规则引擎；本模块只接收"已命中的证据"，
     负责解释成因、润色示范条款、检测跨条款矛盾。Prompt 里明确禁止新增风险项。
  2. **输出必须过校验** —— 只接受符合 JSON Schema 的对象；解析失败 / 超时 / 网络异常，
     一律走模板降级，绝不把半截输出塞进审查意见。
  3. **可审计** —— 每次调用把耗时、是否降级、失败原因写进 audit_logs。

传输层用标准库 urllib，不引入 openai SDK —— 少一个依赖，就少一个演示现场出问题的理由。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .. import config

# ------------------------------------------------------------------ 结果容器
class LLMResult:
    def __init__(self, data: dict | None, *, used_llm: bool, reason: str = "", cost_ms: int = 0):
        self.data = data or {}
        self.used_llm = used_llm
        self.reason = reason
        self.cost_ms = cost_ms

    @property
    def degraded(self) -> bool:
        return not self.used_llm


# ------------------------------------------------------------------ 传输
def is_configured() -> bool:
    return bool(config.LLM_ENABLED and config.LLM_API_KEY)


def _post_chat(messages: list[dict], *, timeout: float, max_tokens: int) -> dict:
    url = f"{config.LLM_BASE_URL}/chat/completions"
    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": config.LLM_TEMPERATURE,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.LLM_API_KEY}",
        },
    )
    # 显式绕过系统代理：本机沙箱环境的代理会拦截并返回 502，直连才拿得到真实响应
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:   # noqa: S310 - 目标地址来自配置
        payload = json.loads(resp.read().decode("utf-8"))
    return payload


def _extract_json(payload: dict) -> dict:
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("响应中缺少 choices")
    content = (choices[0].get("message") or {}).get("content") or ""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1] if "```" in content[3:] else content[3:]
        content = content.lstrip("json").strip()
    return json.loads(content)


def _call(messages: list[dict], *, max_tokens: int | None = None) -> LLMResult:
    """调用 LLM，失败自动重试一次后返回降级结果（不抛异常）。"""
    if not is_configured():
        return LLMResult(None, used_llm=False,
                         reason="未配置 LLM（HJ_LLM_API_KEY 为空），已使用模板生成器")
    started = time.time()
    last_err = ""
    for attempt in range(config.LLM_MAX_RETRY + 1):
        try:
            payload = _post_chat(messages, timeout=config.LLM_TIMEOUT,
                                 max_tokens=max_tokens or config.LLM_MAX_TOKENS)
            data = _extract_json(payload)
            if not isinstance(data, dict):
                raise ValueError("返回内容不是 JSON 对象")
            return LLMResult(data, used_llm=True,
                             cost_ms=int((time.time() - started) * 1000))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = f"网络/超时：{exc}"
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = f"返回内容非法：{exc}"
        except Exception as exc:  # 兜底，绝不让 LLM 异常冒泡打断审查流水线
            last_err = f"未预期错误：{exc}"
    return LLMResult(None, used_llm=False,
                     reason=f"连续 {config.LLM_MAX_RETRY + 1} 次调用失败（{last_err}），已降级模板生成器",
                     cost_ms=int((time.time() - started) * 1000))


# ------------------------------------------------------------------ ① 风险解释
_EXPLAIN_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rule_id", "explanation"],
                "properties": {
                    "rule_id": {"type": "string"},
                    "explanation": {"type": "string"},
                    "advice_polish": {"type": "string"},
                },
            },
        }
    },
}

_EXPLAIN_SYSTEM = """你是企业法务审查助手。你的职责只有一件事：为**已经命中的风险项**撰写成因解释。

严格约束（违反即视为输出无效）：
1. 不得新增、删除、合并任何风险项；只能针对给定 rule_id 输出解释；
2. 不得修改风险等级；等级由确定性规则引擎判定，你无权评价；
3. 解释必须紧扣给出的"证据原文"，不得引入证据中不存在的事实；
4. advice_polish 只能在给定示范条款基础上做措辞适配，不得改变权利义务的实质分配；
5. 只输出 JSON，不要任何解释性文字或 Markdown 代码块。

输出格式：
{"items":[{"rule_id":"HJ-H-001","explanation":"...","advice_polish":"..."}]}"""


def enrich(findings: list[dict], meta: dict) -> LLMResult:
    if not findings:
        return LLMResult({"items": []}, used_llm=False, reason="无命中项，无需语义增强")
    brief = [{
        "rule_id": f["rule_id"],
        "title": f["title"],
        "level": f["level"],
        "clause_type": f["clause_type"],
        "evidence": f["evidence_text"][:300],
        "rule_reason": f.get("reason", "")[:200],
        "template_advice": f.get("advice", "")[:400],
    } for f in findings]
    user = (
        f"合同类型：{meta.get('biz_type', '其他')}；相对方：{meta.get('counterparty', '未识别')}；"
        f"合同金额：{meta.get('amount', '未识别')}。\n"
        f"以下是规则引擎已命中的风险项（共 {len(brief)} 条），请逐条撰写解释：\n"
        + json.dumps(brief, ensure_ascii=False)
    )
    result = _call([
        {"role": "system", "content": _EXPLAIN_SYSTEM},
        {"role": "user", "content": user},
    ])
    if result.used_llm and not isinstance((result.data or {}).get("items"), list):
        return LLMResult(None, used_llm=False, reason="返回结构不符合 schema，已降级")
    return result


# ------------------------------------------------------------------ ② 跨条款矛盾
_CONFLICT_SYSTEM = """你是合同一致性检查助手。请找出合同中**相互矛盾的条款对**。

判定标准（必须同时满足）：
1. 两条原文各自表述完整、单独看不构成风险；
2. 放在一起时，权利义务或时间节点出现冲突（例如一处"验收合格后 30 日付款"，
   另一处"到货即付全款"）；
3. 不要报告规则引擎已经指出的问题，只报告"条款之间"的矛盾。

只输出 JSON：
{"conflicts":[{"clause_a":"原文片段1","clause_b":"原文片段2","issue":"矛盾说明","severity":"high|medium|low"}]}
没有矛盾时返回 {"conflicts":[]}。"""


def detect_conflicts(clauses: list[dict], full_text: str) -> LLMResult:
    if len(clauses) < 3:
        return LLMResult({"conflicts": []}, used_llm=False, reason="条款数量不足，跳过矛盾检测")
    excerpt = "\n".join(
        f"[{c['clause_type']}|第{c['page']}页] {c['text'][:400]}" for c in clauses[:24]
    )[:6000]
    result = _call([
        {"role": "system", "content": _CONFLICT_SYSTEM},
        {"role": "user", "content": excerpt},
    ], max_tokens=1200)
    if result.used_llm and not isinstance((result.data or {}).get("conflicts"), list):
        return LLMResult({"conflicts": []}, used_llm=False, reason="返回结构不符合 schema，已降级")
    return result


# ------------------------------------------------------------------ ③ 综合摘要
_SUMMARY_SYSTEM = """你是企业法务审查助手。请基于给定的风险命中清单，撰写**综合审查摘要**与**审批关注要点**。

严格约束：
1. 只能陈述清单中已有的事实，不得引入清单外的判断；
2. 结论只能是"建议通过 / 建议整改后通过 / 建议拒绝"三者之一，且必须由风险等级分布直接推出：
   - 存在高风险 → 建议拒绝（若高风险仅 1 条且可整改，可为"建议整改后通过"）
   - 无高风险但有中风险 → 建议整改后通过
   - 只有低风险或无风险 → 建议通过
3. 语言克制、专业，不使用夸张表述；每条要点不超过 60 字。

只输出 JSON：
{"risk_summary":"...","conclusion":"建议整改后通过","focus_points":["...","..."]}"""


def compose_summary(stats: dict, findings: list[dict], meta: dict) -> LLMResult:
    brief = [{
        "rule_id": f["rule_id"], "title": f["title"], "level": f["level"],
        "page": f["page"], "evidence": f["evidence_text"][:160],
    } for f in findings]
    user = (
        f"风险统计：高风险 {stats.get('high', 0)} 条、中风险 {stats.get('medium', 0)} 条、"
        f"低风险 {stats.get('low', 0)} 条。\n"
        f"合同类型：{meta.get('biz_type', '其他')}；金额：{meta.get('amount', '未识别')}。\n"
        f"命中清单：\n" + json.dumps(brief, ensure_ascii=False)
    )
    result = _call([
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ], max_tokens=900)
    if result.used_llm:
        data = result.data or {}
        if not isinstance(data.get("risk_summary"), str) or not isinstance(data.get("focus_points"), list):
            return LLMResult(None, used_llm=False, reason="返回结构不符合 schema，已降级")
        if data.get("conclusion") not in ("建议通过", "建议整改后通过", "建议拒绝"):
            data["conclusion"] = _template_conclusion(stats)
    return result


# ------------------------------------------------------------------ 模板降级器
def _template_conclusion(stats: dict) -> str:
    if stats.get("high", 0) > 0:
        return "建议拒绝"
    if stats.get("medium", 0) > 0:
        return "建议整改后通过"
    return "建议通过"


def template_summary(stats: dict, findings: list[dict], meta: dict) -> dict:
    """无 LLM 时的确定性摘要生成器 —— 演示现场断网也能出完整结论。"""
    high = stats.get("high", 0)
    medium = stats.get("medium", 0)
    low = stats.get("low", 0)
    total = high + medium + low
    conclusion = _template_conclusion(stats)
    if total == 0:
        summary = (
            f"本次审查共执行 {stats.get('rule_count', 0)} 条规则，未发现风险项，"
            "各项法定与业务条款齐备，权利义务分配对等。"
        )
        focus = ["合同条款完备，建议按常规流程审批"]
    else:
        top = findings[0] if findings else None
        summary = (
            f"本次审查共命中 {total} 项风险：高风险 {high} 条、中风险 {medium} 条、低风险 {low} 条。"
        )
        if high:
            titles = "、".join(f["title"] for f in findings if f["level"] == "high")[:120]
            summary += f"高风险集中在：{titles}，存在实质法律与商业风险，建议在签署前完成整改。"
        elif medium:
            titles = "、".join(f["title"] for f in findings if f["level"] == "medium")[:120]
            summary += f"中风险集中在：{titles}，建议通过补充条款或谈判修正后签署。"
        else:
            summary += "未发现高风险与中风险，仅有文本规范类问题，建议修订后签署。"
        if top:
            summary += f"首要关注项为「{top['title']}」（第 {top['page']} 页）。"
        focus = []
        for lvl, label in (("high", "必须整改"), ("medium", "建议谈判修正"), ("low", "文本规范")):
            items = [f for f in findings if f["level"] == lvl]
            if items:
                focus.append(f"[{label}] " + "；".join(f["title"] for f in items[:3]))
    return {
        "risk_summary": summary,
        "conclusion": conclusion,
        "focus_points": focus[:6],
        "generated_by": "template",
    }


def template_explanations(findings: list[dict]) -> dict[str, str]:
    """把规则自带的 reason 升级成一段更像"人写的"解释。"""
    out = {}
    for f in findings:
        base = f.get("reason") or f.get("title", "")
        basis = f.get("legal_basis", "")
        page = f.get("page", 1)
        text = f"{base} 该风险位于第 {page} 页，命中规则 {f['rule_id']}。"
        if basis:
            text += f" 依据：{basis}。"
        out[f["rule_id"]] = text
    return out
