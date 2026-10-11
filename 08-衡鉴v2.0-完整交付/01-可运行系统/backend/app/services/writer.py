# -*- coding: utf-8 -*-
"""审查意见组装器

把"机器可读的命中清单"翻译成"人能读的审查意见"，并计算合同画像六维评分。
输出是格式化 Markdown —— 既能直接展示，也能直接写回审批系统评论区。

意见结构（与项目要求的报告格式一一对应）：
    一、合同基本信息
    二、综合审查结论
    三、风险清单明细（按等级排序，每条含证据定位 / 成因 / 依据 / 推荐条款）
    四、审批关注要点
    五、回写与留痕信息
"""
from __future__ import annotations

from datetime import datetime

from .. import config
from . import llm

META_LABELS = [
    ("contract_name", "合同名称"), ("contract_code", "合同编号"),
    ("party_甲方", "甲方名称"), ("party_乙方", "乙方名称"),
    ("party_供方", "供方名称"), ("party_需方", "需方名称"),
    ("party_委托方", "委托方名称"), ("party_受托方", "受托方名称"),
    ("uscc", "统一社会信用代码"), ("amount", "合同金额"), ("currency", "币种"),
    ("perform_period", "履行期限"), ("sign_date", "签订日期"),
    ("perform_start", "生效日期"), ("perform_end", "到期日期"),
]


# ------------------------------------------------------------------ 统计
def count_levels(findings: list[dict]) -> dict:
    stats = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        stats[f["level"]] = stats.get(f["level"], 0) + 1
    return stats


def overall_level(stats: dict) -> str:
    if stats.get("high", 0):
        return "high"
    if stats.get("medium", 0):
        return "medium"
    if stats.get("low", 0):
        return "low"
    return "none"


# ------------------------------------------------------------------ 合同画像
PROFILE_DIMS = [
    ("completeness", "条款完整度"),
    ("balance", "权责对等度"),
    ("payment_safety", "付款安全性"),
    ("ip_protection", "知识产权保护"),
    ("dispute_friendliness", "争议解决友好度"),
    ("text_quality", "合规文本质量"),
]

_REQUIRED_TYPES = ["subject", "subject_matter", "payment", "delivery",
                   "liability", "confidentiality", "dispute", "validity"]


def build_profile(clauses: list[dict], findings: list[dict]) -> dict:
    """六维评分（0–100）。分数是"可解释的"：由可数的条款覆盖与命中项推出，不是拍脑袋。"""
    present = {c["clause_type"] for c in clauses}
    covered = sum(1 for t in _REQUIRED_TYPES if t in present)
    completeness = round(100 * covered / len(_REQUIRED_TYPES))

    by_type: dict[str, list[dict]] = {}
    for f in findings:
        by_type.setdefault(f["clause_type"], []).append(f)

    def penalty(clause_type: str) -> int:
        items = by_type.get(clause_type, [])
        p = 0
        for it in items:
            p += {"high": 34, "medium": 16, "low": 6}.get(it["level"], 0)
        return min(p, 100)

    balance = max(0, 100 - penalty("liability") - penalty("confidentiality") // 2)
    payment_safety = max(0, 100 - penalty("payment"))
    ip_protection = 100 if "ip" in present else 35
    ip_protection = max(0, ip_protection - penalty("ip"))
    dispute_friendliness = 100 if "dispute" in present else 30
    dispute_friendliness = max(0, dispute_friendliness - penalty("dispute"))
    text_quality = max(0, 100 - penalty("other") - penalty("validity") // 2 - penalty("delivery") // 2)

    scores = {
        "completeness": completeness,
        "balance": balance,
        "payment_safety": payment_safety,
        "ip_protection": ip_protection,
        "dispute_friendliness": dispute_friendliness,
        "text_quality": text_quality,
    }
    return {
        "scores": scores,
        "dimensions": [{"key": k, "label": lbl, "score": scores[k]} for k, lbl in PROFILE_DIMS],
        "overall": round(sum(scores.values()) / len(scores)),
    }


# ------------------------------------------------------------------ 摘要
def build_summary(stats: dict, findings: list[dict], meta: dict,
                  *, rule_count: int = 0) -> dict:
    """优先走 LLM；LLM 不可用/超时/返回非法时自动降级为模板生成器。"""
    stats = dict(stats)
    stats["rule_count"] = rule_count
    result = llm.compose_summary(stats, findings, meta)
    if result.used_llm:
        data = result.data
        return {
            "risk_summary": data.get("risk_summary", ""),
            "conclusion": data.get("conclusion", ""),
            "focus_points": data.get("focus_points", []),
            "generated_by": "llm",
            "degrade_reason": "",
            "cost_ms": result.cost_ms,
        }
    tpl = llm.template_summary(stats, findings, meta)
    return {
        "risk_summary": tpl["risk_summary"],
        "conclusion": tpl["conclusion"],
        "focus_points": tpl["focus_points"],
        "generated_by": "template",
        "degrade_reason": result.reason,
        "cost_ms": result.cost_ms,
    }


# ------------------------------------------------------------------ Markdown
def render_markdown(contract: dict, meta: dict, clauses: list[dict], findings: list[dict],
                    summary: dict, profile: dict, *, reviewer_note: str = "",
                    rule_version: str = "", writeback: dict | None = None) -> str:
    stats = count_levels(findings)
    level = overall_level(stats)
    L: list[str] = []

    L.append(f"# 合同审查意见书 · {contract.get('name', '')}")
    L.append("")
    L.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ "
             f"规则版本：{rule_version or 'unknown'} ｜ "
             f"生成方式：{'大模型语义增强' if summary.get('generated_by') == 'llm' else '规则模板生成'}")
    L.append("")

    # 一、合同基本信息
    L.append("## 一、合同基本信息")
    L.append("")
    L.append("| 字段 | 内容 | 原文定位 |")
    L.append("| --- | --- | --- |")
    L.append(f"| 审查任务编号 | {contract.get('code', '')} | — |")
    L.append(f"| 申请部门 | {contract.get('dept', '') or '—'} | — |")
    L.append(f"| 申请人 | {contract.get('applicant', '') or '—'} | — |")
    L.append(f"| 业务类型 | {contract.get('biz_type', '') or '—'} | — |")
    L.append(f"| 综合风险等级 | **{config.RISK_LABEL.get(level, level)}** | — |")
    for key, label in META_LABELS:
        item = next((i for i in meta.get("items", []) if i["key"] == key), None)
        if item:
            loc = f"第 {item['page']} 页" if item.get("page") else "—"
            L.append(f"| {label} | {item['value']} | {loc} |")
    L.append("")

    # 二、综合审查结论
    L.append("## 二、综合审查结论")
    L.append("")
    L.append(f"- **总风险等级**：{config.RISK_LABEL.get(level, level)}")
    L.append(f"- **审查结论**：{summary.get('conclusion', '')}")
    L.append(f"- **风险分布**：高风险 {stats.get('high', 0)} 条 ｜ "
             f"中风险 {stats.get('medium', 0)} 条 ｜ 低风险 {stats.get('low', 0)} 条")
    L.append(f"- **合同画像综合分**：{profile.get('overall', 0)} / 100")
    L.append("")
    L.append(summary.get("risk_summary", ""))
    L.append("")

    dims = profile.get("dimensions") or []
    if dims:
        L.append("| 画像维度 | 评分 |")
        L.append("| --- | --- |")
        for d in dims:
            bar = "█" * max(1, round(d["score"] / 10))
            L.append(f"| {d['label']} | {d['score']} {bar} |")
        L.append("")

    # 三、风险清单明细
    L.append("## 三、风险清单明细")
    L.append("")
    if not findings:
        L.append("未命中任何风险项。")
        L.append("")
    for idx, f in enumerate(findings, 1):
        L.append(f"### {idx}. [{config.RISK_LABEL.get(f['level'], f['level'])}] {f['title']}")
        L.append("")
        L.append(f"- **规则编号**：{f['rule_id']}（{f.get('judge_mode', 'rule')} 判定）")
        L.append(f"- **条款类型**：{config.CLAUSE_LABEL.get(f['clause_type'], f['clause_type'])}")
        L.append(f"- **原文定位**：第 {f['page']} 页 ｜ 段落 #{f['para_index']} ｜ "
                 f"字符区间 [{f['char_start']}, {f['char_end']})")
        L.append(f"- **证据原文**：")
        L.append("")
        L.append(f"  > {f['evidence_text'].replace(chr(10), ' ')}")
        L.append("")
        L.append(f"- **风险成因**：{f.get('explanation') or f.get('reason', '')}")
        if f.get("legal_basis"):
            L.append(f"- **法律依据**：{f['legal_basis']}")
        if f.get("advice"):
            L.append(f"- **推荐修改条款**（来源：{'大模型润色' if f.get('advice_source') == 'llm' else '标准条款库'}）：")
            L.append("")
            for line in f["advice"].split("\n"):
                L.append(f"  > {line}")
            L.append("")
        if f.get("adopted"):
            L.append(f"- **采纳状态**：✅ 已采纳")
            if f.get("adopted_text"):
                L.append(f"- **采纳后文本**：{f['adopted_text']}")
        else:
            L.append(f"- **采纳状态**：⏳ 待法务确认")
        L.append("")

    # 四、审批关注要点
    L.append("## 四、审批关注要点")
    L.append("")
    for p in summary.get("focus_points", []):
        L.append(f"- {p}")
    if not summary.get("focus_points"):
        L.append("- 无")
    L.append("")

    # 五、回写与留痕
    L.append("## 五、回写与留痕")
    L.append("")
    if writeback:
        wb_status = {"not_written": "未回写", "writing": "回写中",
                     "success": "回写成功", "failed": "回写失败"}.get(writeback.get("status"), "未回写")
        L.append(f"- **回写状态**：{wb_status}" +
                 ("（预览模式 DRY_RUN，未真正写入审批系统）" if writeback.get("dry_run") else ""))
        if writeback.get("platform_comment_id"):
            L.append(f"- **审批系统评论 ID**：{writeback['platform_comment_id']}")
        if writeback.get("error"):
            L.append(f"- **回写错误**：{writeback['error']}")
    else:
        L.append("- **回写状态**：未回写")
    if reviewer_note:
        L.append(f"- **法务批注**：{reviewer_note}")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*本意见书由衡鉴合同智能审查系统自动生成。系统仅出具审查意见与修改建议，"
             "不改变审批结论；所有结论均可追溯至规则编号、法条依据与原文坐标。*")
    return "\n".join(L)


def render_writeback_comment(contract: dict, summary: dict, findings: list[dict],
                             *, reviewer_note: str = "", operator: str = "") -> str:
    """写回审批系统评论区的精简版文本（Markdown）。"""
    stats = count_levels(findings)
    level = overall_level(stats)
    L = [f"**【衡鉴·合同智能审查意见】{contract.get('name', '')}**", ""]
    L.append(f"- 审查任务：`{contract.get('code', '')}`")
    L.append(f"- 综合风险等级：**{config.RISK_LABEL.get(level, level)}**"
             f"（高 {stats.get('high', 0)} / 中 {stats.get('medium', 0)} / 低 {stats.get('low', 0)}）")
    L.append(f"- 审查结论：**{summary.get('conclusion', '')}**")
    L.append("")
    L.append(summary.get("risk_summary", ""))
    L.append("")
    if summary.get("focus_points"):
        L.append("**需重点关注事项**")
        for p in summary["focus_points"]:
            L.append(f"- {p}")
        L.append("")
    highs = [f for f in findings if f["level"] == "high"]
    if highs:
        L.append("**高风险明细（须整改后签署）**")
        for f in highs:
            L.append(f"- [{f['rule_id']}] {f['title']}（第 {f['page']} 页）")
            if f.get("advice"):
                L.append(f"  - 建议修改为：{f['advice'][:160]}…")
        L.append("")
    if reviewer_note:
        L.append(f"**法务补充意见**：{reviewer_note}")
        L.append("")
    L.append(f"*意见生成人：{operator or config.OWNER_NAME} ｜ 系统：{config.PROJECT_CODE} "
             f"{config.PROJECT_VERSION} ｜ 本意见不构成审批结论*")
    return "\n".join(L)
