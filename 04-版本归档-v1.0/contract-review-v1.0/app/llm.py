# -*- coding: utf-8 -*-
"""大模型接入层（可选增强，零依赖实现）

边界很明确（对齐架构图）：**大模型不参与风险判定**。
  * 风险识别：确定性规则引擎（app/review.py）
  * 大模型职责：把已确定的命中结果组织成更自然的中文审查摘要与评论表达
  * 未配置 API Key 时自动降级为确定性模板，功能不缺失，只是文风更「工程化」

调用方式：OpenAI 兼容的 /chat/completions 接口，仅用标准库 urllib。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _opener():
    # 显式禁用代理：避免宿主环境的 http_proxy 把内网/本机请求劫持到代理端口
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def available(cfg) -> bool:
    llm = (cfg or {}).get("llm") or {}
    if not llm.get("enabled"):
        return False
    if not llm.get("base_url"):
        return False
    return bool(os.environ.get(llm.get("api_key_env", "OPENAI_API_KEY")))


def chat(cfg, system_prompt: str, user_prompt: str):
    llm = cfg["llm"]
    key = os.environ.get(llm.get("api_key_env", "OPENAI_API_KEY"), "")
    url = llm["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    payload = {
        "model": llm.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % key},
        method="POST",
    )
    try:
        with _opener().open(req, timeout=llm.get("timeout", 30)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"]["content"] or "").strip(), ""
    except urllib.error.HTTPError as exc:
        return "", "HTTP %s: %s" % (exc.code, exc.read()[:200])
    except Exception as exc:                                 # noqa: BLE001
        return "", str(exc)


def polish_summary(cfg, rule_summary: str, case: dict, hits: list):
    """让大模型润色摘要；失败则原样返回规则摘要（保证永不阻断主流程）。"""
    if not available(cfg):
        return rule_summary, "rule-template"
    sys_p = ("你是企业法务合同审查助手。只做中文表达润色，不得新增、删减或改变任何风险结论、"
             "风险等级、金额、条款编号与法律依据。输出 120-220 字，分点陈述，不用 Markdown 标题。")
    user_p = json.dumps({
        "合同标题": case.get("title"),
        "业务类型": case.get("business_type"),
        "综合风险等级": case.get("risk_level"),
        "结论": case.get("conclusion"),
        "命中风险": [{"名称": h.get("rule_name"), "等级": h.get("risk_level"),
                      "条款类型": h.get("clause_type")} for h in hits],
        "规则摘要原文": rule_summary,
    }, ensure_ascii=False, indent=2)
    text, err = chat(cfg, sys_p, user_p)
    if text:
        return text, "llm"
    return rule_summary, "rule-template(llm-failed:%s)" % (err or "unknown")
