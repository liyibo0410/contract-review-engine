# -*- coding: utf-8 -*-
"""审查意见生成 + 报告导出（Markdown / HTML / PDF）

PDF 通道：调用本机已安装的 Edge / Chrome 无头模式 `--print-to-pdf`，
不需要任何 Python 第三方库；若浏览器不可用则降级为「返回排版好的 HTML，
由前端浏览器打印为 PDF」，保证导出能力始终可用。
"""
from __future__ import annotations

import html as html_mod
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import review as review_mod
from . import store

RISK_LABEL = store.RISK_LABEL
CONCLUSION_LABEL = review_mod.CONCLUSION_LABEL
WB_LABEL = store.WB_LABEL

_RISK_CLASS = {"high": "risk-high", "medium": "risk-medium", "low": "risk-low",
               "none": "risk-none"}


# ------------------------------------------------------------------ 审查摘要
def build_summary(case, doc, hits, coverage):
    highs = [h for h in hits if h["risk_level"] == "high"]
    mediums = [h for h in hits if h["risk_level"] == "medium"]
    lows = [h for h in hits if h["risk_level"] == "low"]
    level = case.get("risk_level") or store.R_NONE

    if not hits:
        headline = "未发现规则库覆盖范围内的合规与商业风险，建议按正常流程审批。"
    elif highs:
        headline = ("命中 %d 项高风险、%d 项中风险、%d 项低风险，综合风险等级判定为【高风险】，"
                    "建议审批人暂停通过并要求整改后再行送审。" % (len(highs), len(mediums), len(lows)))
    elif mediums:
        headline = ("命中 %d 项中风险、%d 项低风险，综合风险等级判定为【中风险】，"
                    "建议按审查意见修改后再行签署。" % (len(mediums), len(lows)))
    else:
        headline = "命中 %d 项低风险，综合风险等级判定为【低风险】，可在补充规范用语的条件下通过。" % len(lows)

    points = []
    for h in highs:
        points.append("高风险 · %s（%s）：%s" % (h["rule_name"], h["clause_type"],
                                            _first_sentence(h["reason"])))
    for h in mediums:
        points.append("中风险 · %s（%s）：%s" % (h["rule_name"], h["clause_type"],
                                            _first_sentence(h["reason"])))
    for h in lows[:3]:
        points.append("低风险 · %s（%s）：%s" % (h["rule_name"], h["clause_type"],
                                            _first_sentence(h["reason"])))

    attention = []
    if highs:
        attention.append("高风险条款必须在合同签署前完成修改，否则不建议进入用印流程。")
    if "知识产权" in [h["clause_type"] for h in hits]:
        attention.append("知识产权归属直接影响后续自主开发与供应商替换，请业务与研发共同确认。")
    if "付款进度" in [h["clause_type"] for h in hits]:
        attention.append("付款条款与验收条件必须绑定，财务部门据此控制付款审批节点。")
    if "违约责任" in [h["clause_type"] for h in hits]:
        attention.append("违约责任需保持双方对等，并明确赔偿责任上限，避免无限敞口。")
    if coverage["missing"]:
        attention.append("未见条款类型：%s，建议确认是否为其他文件约定。" %
                         "、".join(coverage["missing"][:6]))
    if doc and doc.get("parse_status") == "blocked":
        attention.append("文档解析受阻，风险审查结果可能不完整，请人工补充审阅后再确认。")

    return {
        "headline": headline,
        "points": points,
        "attention": attention,
        "stats": {
            "high": len(highs), "medium": len(mediums), "low": len(lows),
            "total": len(hits),
        },
        "risk_level": level,
    }


def _first_sentence(text):
    if not text:
        return ""
    m = re.split(r"[。；\n]", text.strip())
    return m[0] if m else text


# ------------------------------------------------------------------ 审批回写文本
_CN_NUM = "一二三四五六七八九十"


def _section_no(n: int) -> str:
    return _CN_NUM[n - 1] if 1 <= n <= 10 else str(n)


def compose_comment(case, doc, hits, summary, meta, lawyer_opinion="", source="rule"):
    """组织写回审批评论区的审查意见（章节编号按实际存在的小节动态生成）。"""
    lines = []
    lines.append("**【合同智能审查意见】**")
    lines.append("")
    lines.append("- 合同名称：%s（%s）" % (case.get("title") or "-", case.get("case_no") or "-"))
    lines.append("- 业务类型：%s" % _btype_label(case.get("business_type")))
    lines.append("- 风险总评：%s（高 %d / 中 %d / 低 %d）" % (
        CONCLUSION_LABEL.get(case.get("conclusion"), "-"),
        summary["stats"]["high"], summary["stats"]["medium"], summary["stats"]["low"]))
    lines.append("- 综合风险等级：%s" % RISK_LABEL.get(case.get("risk_level"), "-"))
    if doc:
        lines.append("- 文档解析：%s · %s · %s 页 / %s 段" % (
            doc.get("filename") or "-", doc.get("parse_engine") or "-",
            doc.get("page_count") or 0, doc.get("para_count") or 0))

    seq = [0]

    def sec(title):
        seq[0] += 1
        lines.append("")
        lines.append("**%s、%s**" % (_section_no(seq[0]), title))

    sec("风险摘要")
    lines.append(summary["headline"])
    for p in summary["points"]:
        lines.append("- %s" % p)

    if summary["attention"]:
        sec("需重点关注事项")
        for a in summary["attention"]:
            lines.append("- %s" % a)

    if hits:
        sec("风险清单明细")
        for h in hits:
            loc = ("第 %s 页 / 第 %s 段" % (h["page_no"], h["para_index"])
                   if h.get("page_no") is not None else "全文未检出（无定位锚点）")
            lines.append("- 【%s】%s（%s，%s）" % (
                RISK_LABEL.get(h["risk_level"], "-"), h["rule_name"],
                h["clause_type"], loc))
            if h.get("evidence"):
                ev = re.sub(r"\s*\n\s*", " / ", h["evidence"]).strip()
                lines.append("  - 原文证据：%s" % ev[:200])
            lines.append("  - 合规依据：%s" % _first_sentence(h.get("legal_basis")))
            lines.append("  - 建议修改：%s" % _first_sentence(h.get("suggestion")))

    if lawyer_opinion:
        sec("法务人工批注")
        lines.append(lawyer_opinion)

    sec("免责与边界声明")
    lines.append("本意见由合同智能审查系统基于可配置合规规则库自动生成，仅供审批参考，"
                 "不构成法律意见，也不代表审批结论。审批平台中的通过、拒绝、转交等最终决定"
                 "仍由审批人作出。")
    lines.append("")
    lines.append("> 审查摘要生成方式：%s · 生成时间：%s" % (
        "大模型润色" if source == "llm" else "确定性规则模板", time.strftime("%Y-%m-%d %H:%M:%S")))
    return "\n".join(lines)


B_TYPE = {"purchase": "采购合同", "sales": "销售合同", "service": "服务合同",
          "labor": "劳动合同"}


def _btype_label(code):
    return B_TYPE.get(code, code or "-")


# ------------------------------------------------------------------ Markdown 报告
def build_report_md(case, doc, meta, hits, summary, coverage, clauses,
                    lawyer_opinion="", writeback=None):
    L = []
    L.append("# 合同审查意见报告")
    L.append("")
    L.append("> 报告编号：RPT-%s ｜ 生成时间：%s ｜ 生成系统：合同智能审查系统 v1.0" % (
        case.get("case_no"), time.strftime("%Y-%m-%d %H:%M:%S")))
    L.append("")
    L.append("## 一、合同基本信息")
    L.append("")
    L.append("| 项目 | 内容 |")
    L.append("| --- | --- |")
    L.append("| 合同标题 | %s |" % (case.get("title") or "-"))
    L.append("| 合同编号 | %s |" % (case.get("case_no") or "-"))
    L.append("| 送审部门 | %s |" % (case.get("send_dept") or "-"))
    L.append("| 申请人 | %s |" % (case.get("applicant") or "-"))
    L.append("| 相对方主体 | %s |" % (case.get("counterparty") or meta.get("party_b") or "-"))
    L.append("| 统一社会信用代码 | %s |" % (case.get("credit_code") or meta.get("credit_code") or "未载明"))
    L.append("| 合同金额 | %s |" % (_amount_text(case)))
    L.append("| 履约周期 | %s |" % (case.get("perform_period") or meta.get("perform_period") or "-"))
    L.append("| 合同类别 | %s |" % _btype_label(case.get("business_type")))
    L.append("| 来源 | %s |" % ("审批系统拉取" if case.get("source") == "approval" else "本地上传"))
    L.append("")
    L.append("## 二、综合审查结论")
    L.append("")
    L.append("- **综合风险等级**：%s" % RISK_LABEL.get(case.get("risk_level"), "-"))
    L.append("- **审查结论**：%s" % CONCLUSION_LABEL.get(case.get("conclusion"), "-"))
    L.append("- **风险统计**：高风险 %d 项 / 中风险 %d 项 / 低风险 %d 项" % (
        summary["stats"]["high"], summary["stats"]["medium"], summary["stats"]["low"]))
    L.append("")
    L.append(summary["headline"])
    L.append("")
    if summary["attention"]:
        L.append("**审批关注要点**")
        L.append("")
        for a in summary["attention"]:
            L.append("- %s" % a)
        L.append("")
    L.append("## 三、风险清单明细")
    L.append("")
    if not hits:
        L.append("未命中规则库中的风险项。")
        L.append("")
    for i, h in enumerate(hits, start=1):
        loc = ("第 %s 页 / 第 %s 段 / 字符区间 [%s, %s)" % (
            h["page_no"], h["para_index"], h.get("char_start"), h.get("char_end"))
            if h.get("page_no") is not None else "全文未检出（无定位锚点）")
        L.append("### %d. 【%s】%s" % (i, RISK_LABEL.get(h["risk_level"], "-"), h["rule_name"]))
        L.append("")
        L.append("- 规则编号：%s ｜ 条款类型：%s ｜ 匹配方式：%s" % (
            h.get("rule_id"), h.get("clause_type"), h.get("match_mode")))
        L.append("- 原文定位：%s" % loc)
        if h.get("evidence"):
            L.append("- 原文片段：")
            L.append("")
            L.append("  > %s" % re.sub(r"\n", "\n  > ", h["evidence"].strip()))
            L.append("")
        L.append("- 风险成因：%s" % (h.get("reason") or "-"))
        L.append("- 法律依据：%s" % (h.get("legal_basis") or "-"))
        L.append("- 推荐修改条款：")
        L.append("")
        L.append("  ```")
        L.append("  %s" % (h.get("suggestion_final") or h.get("suggestion") or "-"))
        L.append("  ```")
        L.append("")
        if h.get("lawyer_note"):
            L.append("- 法务批注：%s" % h["lawyer_note"])
            L.append("")
        L.append("- 处理状态：%s" % ("已采纳" if h.get("adopted") else "待确认"))
        L.append("")
    L.append("## 四、条款结构与覆盖度")
    L.append("")
    L.append("- 已识别条款类型：%s" % ("、".join(coverage["present"]) or "无"))
    L.append("- 未识别条款类型：%s" % ("、".join(coverage["missing"]) or "无"))
    L.append("- 条款覆盖度：%.1f%%（%d / %d 类）" % (
        coverage["coverage_rate"] * 100, len(coverage["present"]), coverage["total_types"]))
    L.append("")
    L.append("| 序号 | 条款 | 类型 | 页码 | 段落区间 | 字数 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for c in clauses[:60]:
        L.append("| %d | %s | %s | %s | %s-%s | %d |" % (
            c["seq"], (c["title"] or "").replace("|", "/")[:34], c["clause_label"],
            c["page"], c["para_start"], c["para_end"], c["char_count"]))
    L.append("")
    L.append("## 五、审批回写记录")
    L.append("")
    if writeback:
        L.append("- 回写状态：%s" % WB_LABEL.get(writeback.get("status"), "-"))
        L.append("- 回写模式：%s ｜ 通道：%s" % (writeback.get("mode"), writeback.get("channel")))
        L.append("- 评论标识：%s" % (writeback.get("comment_id") or "-"))
        L.append("- 回写时间：%s" % (writeback.get("created_at") or "-"))
        if writeback.get("error"):
            L.append("- 错误信息：%s" % writeback["error"])
    else:
        L.append("- 尚未执行回写。")
    L.append("")
    if lawyer_opinion:
        L.append("## 六、法务人工意见")
        L.append("")
        L.append(lawyer_opinion)
        L.append("")
    L.append("---")
    L.append("")
    L.append("本报告由合同智能审查系统 v1.0 自动生成。风险识别采用可配置确定性规则库，"
             "大模型仅参与中文摘要与表达组织，不参与风险判定。报告内容仅供审批参考，"
             "不构成法律意见，最终审批结论由审批人作出。")
    return "\n".join(L)


def _amount_text(case):
    amt = case.get("amount")
    if not amt:
        return "-"
    cur = case.get("currency") or "CNY"
    symbol = "¥" if cur == "CNY" else "$"
    if amt >= 10000:
        return "%s%s（%.2f 万元）" % (symbol, format(amt, ",.2f"), amt / 10000.0)
    return "%s%s" % (symbol, format(amt, ",.2f"))


# ------------------------------------------------------------------ HTML 报告
_PRINT_CSS = """
:root{--ink:#0f172a;--sub:#475569;--line:#e2e8f0;--bg:#ffffff;
--high:#b91c1c;--highbg:#fef2f2;--mid:#b45309;--midbg:#fffbeb;--low:#1d4ed8;--lowbg:#eff6ff;}
*{box-sizing:border-box;}
body{margin:0;padding:36px 44px;background:var(--bg);color:var(--ink);
font-family:"Source Han Sans SC","Microsoft YaHei","PingFang SC",system-ui,sans-serif;
font-size:13px;line-height:1.75;-webkit-print-color-adjust:exact;print-color-adjust:exact;}
h1{font-size:21px;margin:0 0 6px;letter-spacing:.5px;}
h2{font-size:15px;margin:26px 0 10px;padding-left:10px;border-left:3px solid var(--ink);}
h3{font-size:13.5px;margin:16px 0 6px;}
.meta-line{color:var(--sub);font-size:12px;border-bottom:1px solid var(--line);padding-bottom:12px;}
table{width:100%;border-collapse:collapse;margin:8px 0 4px;font-size:12.5px;}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top;}
th{background:#f8fafc;font-weight:600;}
.tag{display:inline-block;padding:1px 7px;border-radius:3px;font-size:11.5px;font-weight:600;}
.risk-high{color:var(--high);background:var(--highbg);border:1px solid #fecaca;}
.risk-medium{color:var(--mid);background:var(--midbg);border:1px solid #fde68a;}
.risk-low{color:var(--low);background:var(--lowbg);border:1px solid #bfdbfe;}
.risk-none{color:var(--sub);background:#f8fafc;border:1px solid var(--line);}
.card{border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin:10px 0;
break-inside:avoid;page-break-inside:avoid;}
.card.high{border-left:3px solid var(--high);}
.card.medium{border-left:3px solid var(--mid);}
.card.low{border-left:3px solid var(--low);}
.kv{color:var(--sub);font-size:12px;}
.quote{background:#f8fafc;border-left:2px solid #cbd5e1;padding:8px 12px;margin:6px 0;
white-space:pre-wrap;font-size:12.5px;}
.sugg{background:#f0fdf4;border:1px solid #bbf7d0;padding:8px 12px;margin:6px 0;
white-space:pre-wrap;font-size:12.5px;}
.foot{margin-top:26px;padding-top:12px;border-top:1px solid var(--line);
color:var(--sub);font-size:11.5px;}
@media print{body{padding:0 8mm;}h2{break-after:avoid;}@page{size:A4;margin:14mm 10mm;}}
"""


def build_report_html(case, doc, meta, hits, summary, coverage, clauses,
                      lawyer_opinion="", writeback=None, autoprint=False):
    e = html_mod.escape

    def tag(level):
        return '<span class="tag %s">%s</span>' % (
            _RISK_CLASS.get(level, "risk-none"), RISK_LABEL.get(level, "-"))

    parts = []
    parts.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append('<title>合同审查意见报告 · %s</title>' % e(case.get("title") or "-"))
    parts.append("<style>%s</style>" % _PRINT_CSS)
    if autoprint:
        parts.append("<script>window.addEventListener('load',function(){"
                     "setTimeout(function(){window.print();},320);});</script>")
    parts.append("</head><body>")

    parts.append("<h1>合同审查意见报告</h1>")
    parts.append('<div class="meta-line">报告编号 RPT-%s ｜ 生成时间 %s ｜ '
                 "合同智能审查系统 v1.0</div>" % (e(case.get("case_no") or "-"),
                                              e(time.strftime("%Y-%m-%d %H:%M:%S"))))

    parts.append("<h2>一、合同基本信息</h2>")
    parts.append("<table>")
    rows = [
        ("合同标题", case.get("title") or "-"),
        ("合同编号", case.get("case_no") or "-"),
        ("送审部门 / 申请人", "%s / %s" % (case.get("send_dept") or "-", case.get("applicant") or "-")),
        ("相对方主体", case.get("counterparty") or meta.get("party_b") or "-"),
        ("统一社会信用代码", case.get("credit_code") or meta.get("credit_code") or "未载明"),
        ("合同金额", _amount_text(case)),
        ("履约周期", case.get("perform_period") or meta.get("perform_period") or "-"),
        ("合同类别", _btype_label(case.get("business_type"))),
        ("来源", "审批系统拉取" if case.get("source") == "approval" else "本地上传"),
    ]
    if doc:
        rows.append(("文档解析", "%s ｜ 引擎 %s ｜ %s 页 / %s 段 / %s 字符" % (
            doc.get("filename") or "-", doc.get("parse_engine") or "-",
            doc.get("page_count") or 0, doc.get("para_count") or 0,
            doc.get("char_count") or 0)))
    for k, v in rows:
        parts.append("<tr><th style='width:22%%'>%s</th><td>%s</td></tr>" % (e(k), e(str(v))))
    parts.append("</table>")

    parts.append("<h2>二、综合审查结论</h2>")
    parts.append("<p><b>综合风险等级：</b>%s ｜ <b>审查结论：</b>%s</p>" % (
        tag(case.get("risk_level")), e(CONCLUSION_LABEL.get(case.get("conclusion"), "-"))))
    parts.append("<p><b>风险统计：</b>高风险 %d 项 / 中风险 %d 项 / 低风险 %d 项</p>" % (
        summary["stats"]["high"], summary["stats"]["medium"], summary["stats"]["low"]))
    parts.append("<p>%s</p>" % e(summary["headline"]))
    if summary["attention"]:
        parts.append("<p><b>审批关注要点</b></p><ul>")
        for a in summary["attention"]:
            parts.append("<li>%s</li>" % e(a))
        parts.append("</ul>")

    parts.append("<h2>三、风险清单明细</h2>")
    if not hits:
        parts.append("<p>未命中规则库中的风险项。</p>")
    for i, h in enumerate(hits, start=1):
        cls = h["risk_level"] if h["risk_level"] in ("high", "medium", "low") else "low"
        if h.get("page_no") is not None:
            loc = "第 %s 页 / 第 %s 段 / 字符区间 [%s, %s)" % (
                h["page_no"], h["para_index"], h.get("char_start"), h.get("char_end"))
        else:
            loc = "全文未检出（无定位锚点）"
        parts.append('<div class="card %s">' % cls)
        parts.append("<h3>%d. %s %s</h3>" % (i, tag(h["risk_level"]), e(h["rule_name"] or "-")))
        parts.append('<div class="kv">规则编号 %s ｜ 条款类型 %s ｜ 匹配方式 %s</div>' % (
            e(str(h.get("rule_id"))), e(str(h.get("clause_type"))), e(str(h.get("match_mode")))))
        parts.append('<div class="kv">原文定位：%s</div>' % e(loc))
        if h.get("evidence"):
            parts.append('<div class="quote">原文片段：\n%s</div>' % e(h["evidence"].strip()))
        parts.append("<p><b>风险成因：</b>%s</p>" % e(h.get("reason") or "-"))
        parts.append("<p><b>法律依据：</b>%s</p>" % e(h.get("legal_basis") or "-"))
        parts.append('<div class="sugg">推荐修改条款：\n%s</div>' % e(
            h.get("suggestion_final") or h.get("suggestion") or "-"))
        if h.get("lawyer_note"):
            parts.append("<p><b>法务批注：</b>%s</p>" % e(h["lawyer_note"]))
        parts.append('<div class="kv">处理状态：%s</div>' % (
            "已采纳" if h.get("adopted") else "待确认"))
        parts.append("</div>")

    parts.append("<h2>四、条款结构与覆盖度</h2>")
    parts.append("<p>已识别：%s<br>未识别：%s<br>条款覆盖度：%.1f%%（%d / %d 类）</p>" % (
        e("、".join(coverage["present"]) or "无"), e("、".join(coverage["missing"]) or "无"),
        coverage["coverage_rate"] * 100, len(coverage["present"]), coverage["total_types"]))
    parts.append("<table><tr><th>序号</th><th>条款</th><th>类型</th><th>页码</th>"
                 "<th>段落区间</th><th>字数</th></tr>")
    for c in clauses[:80]:
        parts.append("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s-%s</td>"
                     "<td>%d</td></tr>" % (
                         c["seq"], e((c["title"] or "")[:40]), e(c["clause_label"]),
                         c["page"], c["para_start"], c["para_end"], c["char_count"]))
    parts.append("</table>")

    parts.append("<h2>五、审批回写记录</h2>")
    if writeback:
        parts.append("<p>回写状态：<b>%s</b> ｜ 模式：%s ｜ 通道：%s ｜ 评论标识：%s ｜ 时间：%s</p>"
                     % (e(WB_LABEL.get(writeback.get("status"), "-")),
                        e(str(writeback.get("mode"))), e(str(writeback.get("channel"))),
                        e(str(writeback.get("comment_id") or "-")),
                        e(str(writeback.get("created_at") or "-"))))
        if writeback.get("error"):
            parts.append("<p>错误信息：%s</p>" % e(writeback["error"]))
    else:
        parts.append("<p>尚未执行回写。</p>")

    if lawyer_opinion:
        parts.append("<h2>六、法务人工意见</h2>")
        parts.append('<div class="quote">%s</div>' % e(lawyer_opinion))

    parts.append('<div class="foot">本报告由合同智能审查系统 v1.0 自动生成。'
                 "风险识别采用可配置确定性规则库，大模型仅参与中文摘要与表达组织，"
                 "不参与风险判定。报告内容仅供审批参考，不构成法律意见，"
                 "最终审批结论由审批人作出。</div>")
    parts.append("</body></html>")
    return "".join(parts)


# ------------------------------------------------------------------ PDF 通道
# Windows 侧用系统自带的 Edge；Linux 侧若安装了 chromium / chrome 同样可复用同一命令行。
_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]


def find_browser():
    for p in _BROWSERS:
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome", "chromium", "chromium-browser",
                 "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def html_to_pdf(html_path: str, pdf_path: str):
    """用系统浏览器的无头打印通道生成 PDF。返回 (ok, info)。"""
    exe = find_browser()
    if not exe:
        return False, "未找到 Edge/Chrome，无法在服务端直接生成 PDF"
    os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
    if os.path.exists(pdf_path):
        try:
            os.remove(pdf_path)
        except OSError:
            pass
    # 浏览器用户目录放到临时目录，避免污染报告目录
    profile = os.path.join(tempfile.gettempdir(), "contract_review_edge_profile")
    os.makedirs(profile, exist_ok=True)
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--no-first-run", "--disable-extensions",
           "--user-data-dir=" + profile,
           "--print-to-pdf-no-header",
           "--print-to-pdf=" + os.path.abspath(pdf_path),
           "file:///" + os.path.abspath(html_path).replace("\\", "/")]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as exc:                                 # noqa: BLE001
        return False, "调用浏览器生成 PDF 失败：%s" % exc
    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1024:
        return True, "ok"
    # 兼容旧参数名
    cmd[3] = "--headless"
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as exc:                                 # noqa: BLE001
        return False, "调用浏览器生成 PDF 失败：%s" % exc
    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1024:
        return True, "ok"
    return False, "浏览器未产出有效 PDF（可改用前端浏览器打印导出）"
