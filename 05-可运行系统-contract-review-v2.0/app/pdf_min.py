# -*- coding: utf-8 -*-
"""服务端 PDF 直出（纯标准库，零第三方依赖）

背景：1.0 的 PDF 通道依赖本机 Edge/Chrome 无头打印。线上 Linux 实例按决议 D-26
不装 chromium，于是导出 PDF 永远落到「返回可打印 HTML」的降级路径 —— 交付形态
上始终缺一个「服务端能自己吐出 PDF」的能力。

本模块提供纯标准库兜底通道：**直接按字节拼出 PDF 文件**。
关键技术点（也是唯一需要解释的地方）：
  * 中文不嵌字体。嵌入一个 CJK 子集需要 TTF 解析 + 子集化 + cmap/glyf 重写，
    远超「零依赖兜底」的复杂度；这里改用 PDF 标准的非嵌入式 CID 字体
    `/STSong-Light` + `/UniGB-UCS2-H` 编码，由阅读器用系统内置宋体族替代渲染。
    这是 PDF 规范允许的用法，Acrobat / Edge / Chrome / WPS 均可正常显示与复制。
  * 文本用 UTF-16BE 十六进制串（`<4E2D>`）书写，配合 `UniGB-UCS2-H` 的 UCS-2 语义，
    避免任何转义与编码歧义。
  * 同时写入 ToUnicode CMap，保证复制/检索得到的是正确汉字而不是 CID 码位。

定位：**兜底通道**。若本机有浏览器，报告导出仍优先走高质量排版通道（output.py），
本模块只在浏览器不可用（线上实例）时接管，保证「服务端 PDF 直出」始终可用。
"""
from __future__ import annotations

import io
import time

A4_W = 595.28
A4_H = 841.89
MARGIN = 52.0
CONTENT_W = A4_W - MARGIN * 2

PAGE_SIZE = {"a4": (A4_W, A4_H)}


# ------------------------------------------------------------------ 度量
def char_width(ch: str, size: float) -> float:
    """粗略字宽：CJK 全宽、西文约半宽。用于换行，不需要精确排版。"""
    o = ord(ch)
    if o > 0x2E80:                       # CJK / 全角标点 / 假名等
        return size
    if ch == " ":
        return size * 0.28
    return size * 0.55


def text_width(s: str, size: float) -> float:
    return sum(char_width(c, size) for c in s)


def wrap(text: str, size: float, width: float = CONTENT_W):
    """按宽度贪心折行；中文按字断，西文尽量按词断。"""
    out = []
    for para in str(text or "").split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        w = 0.0
        for ch in para:
            cw = char_width(ch, size)
            if w + cw > width and line:
                out.append(line)
                line, w = "", 0.0
            line += ch
            w += cw
        out.append(line)
    return out


# ------------------------------------------------------------------ 编码
def _u16hex(s: str) -> str:
    buf = []
    for ch in s:
        o = ord(ch)
        if o > 0xFFFF:                   # 非 BMP：UCS-2 表达不了，降级为问号
            o = 0x003F
        buf.append("%04X" % o)
    return "".join(buf)


def _pdf_escape_text(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


# ------------------------------------------------------------------ 内容流
class _Canvas:
    def __init__(self):
        self.buf = []
        self.y = 0.0

    def raw(self, s: str):
        self.buf.append(s)

    def text(self, s: str, x: float, y: float, size: float, color=(0, 0, 0),
             bold_gap: float = 0.0):
        if not s:
            return
        r, g, b = color
        self.buf.append("q %.4f %.4f %.4f rg BT /F1 %.2f Tf %.2f %.2f Td"
                        % (r, g, b, size, x, y))
        self.buf.append("<%s> Tj" % _u16hex(s))
        if bold_gap:
            # 伪粗体：横向微移重描一次（非嵌入 CID 字体没有真正的粗体字形）
            self.buf.append("ET BT /F1 %.2f Tf %.2f %.2f Td" % (size, x + bold_gap, y))
            self.buf.append("<%s> Tj" % _u16hex(s))
        self.buf.append("ET Q")

    def rule(self, x1, y1, x2, y2, color=(0.85, 0.85, 0.85), width=0.6):
        r, g, b = color
        self.buf.append("q %.4f %.4f %.4f RG %.2f w %.2f %.2f m %.2f %.2f l S Q"
                        % (r, g, b, width, x1, y1, x2, y2))

    def rect(self, x, y, w, h, color=(0.95, 0.95, 0.95)):
        r, g, b = color
        self.buf.append("q %.4f %.4f %.4f rg %.2f %.2f %.2f %.2f re f Q"
                        % (r, g, b, x, y, w, h))

    def stream(self) -> bytes:
        return "\n".join(self.buf).encode("latin-1", "replace")


def _tounicode_cmap(codes) -> bytes:
    """为出现过的码位生成 ToUnicode CMap（保证复制出来是汉字）。"""
    uniq = sorted(set(codes))
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
    ]
    for i in range(0, len(uniq), 100):
        chunk = uniq[i:i + 100]
        lines.append("%d beginbfchar" % len(chunk))
        for c in chunk:
            lines.append("<%04X> <%04X>" % (c, c))
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("latin-1", "replace")


# ------------------------------------------------------------------ 文档构建
def build(pages, title="审查意见报告", meta_lines=None, footer=""):
    """pages: [ [ (kind, text), ... ], ... ]

    kind 取值（决定字号/颜色/缩进）：
        h1 / h2 / h3 / body / kv / bullet / risk_high / risk_medium / risk_low / note
    """
    meta_lines = meta_lines or []
    objects = {}

    # ---- 版面常量
    size_map = {"h1": 15.5, "h2": 12.0, "h3": 11.0, "body": 10.5,
                "kv": 10.0, "bullet": 10.0, "note": 9.0}
    color_map = {
        "h1": (0.08, 0.10, 0.14), "h2": (0.10, 0.13, 0.20), "h3": (0.12, 0.15, 0.22),
        "body": (0.14, 0.16, 0.20), "kv": (0.20, 0.23, 0.28),
        "bullet": (0.16, 0.19, 0.24), "note": (0.42, 0.45, 0.50),
        "risk_high": (0.90, 0.28, 0.30), "risk_medium": (0.96, 0.65, 0.14),
        "risk_low": (0.19, 0.64, 0.42),
    }
    lead_map = {"h1": 22.0, "h2": 17.0, "h3": 15.5, "body": 15.0,
                "kv": 14.0, "bullet": 14.0, "note": 13.0}

    all_codes = []
    page_streams = []
    total_pages_idx = []                 # (obj_num of page) 顺序
    content_objs = []

    for pi, blocks in enumerate(pages):
        c = _Canvas()
        y = A4_H - MARGIN
        if pi == 0:
            c.text(title, MARGIN, y - 6, 17.0, (0.06, 0.08, 0.12), bold_gap=1.0)
            for ch in title:
                all_codes.append(ord(ch) if ord(ch) <= 0xFFFF else 0x3F)
            y -= 30
            for m in meta_lines:
                c.text(m, MARGIN, y, 9.6, (0.42, 0.45, 0.50))
                for ch in m:
                    all_codes.append(ord(ch) if ord(ch) <= 0xFFFF else 0x3F)
                y -= 13.5
            c.rule(MARGIN, y + 6, A4_W - MARGIN, y + 6, (0.80, 0.82, 0.85), 0.8)
            y -= 12

        for kind, text in blocks:
            kind = kind if kind in size_map or kind.startswith("risk_") else "body"
            size = size_map.get(kind, 10.5)
            color = color_map.get(kind, (0.14, 0.16, 0.20))
            lead = lead_map.get(kind, 15.0)
            indent = 0.0
            prefix = ""
            if kind == "bullet":
                prefix, indent = "· ", 10.0
            elif kind == "kv":
                indent = 0.0
            elif kind.startswith("risk_"):
                prefix, indent = "■ ", 12.0
            if kind in ("h1", "h2"):
                y -= 6

            for ln in wrap((prefix + text) if prefix else text, size, CONTENT_W - indent):
                if y < MARGIN + 26:                       # 换页
                    page_streams.append(c.stream())
                    c = _Canvas()
                    y = A4_H - MARGIN
                c.text(ln, MARGIN + indent, y, size, color,
                       bold_gap=1.0 if kind in ("h1", "h2") else 0.0)
                for ch in ln:
                    all_codes.append(ord(ch) if ord(ch) <= 0xFFFF else 0x3F)
                y -= lead
            if kind in ("h1", "h2"):
                y -= 4
        page_streams.append(c.stream())

    # ---- 页码 / 页脚（第二遍写入，需要知道总页数）
    n_pages = len(page_streams)
    stamp = footer or ("衡鉴 V2.1.7 ｜ %s ｜ 本意见仅供审批参考，不构成法律意见"
                       % time.strftime("%Y-%m-%d"))
    final_streams = []
    for i, s in enumerate(page_streams):
        c = _Canvas()
        c.buf.append(s.decode("latin-1"))
        c.rule(MARGIN, MARGIN + 14, A4_W - MARGIN, MARGIN + 14, (0.88, 0.89, 0.91), 0.5)
        c.text(stamp, MARGIN, MARGIN + 2, 8.0, (0.55, 0.58, 0.62))
        c.text("第 %d / %d 页" % (i + 1, n_pages), A4_W - MARGIN - 62, MARGIN + 2,
               8.0, (0.55, 0.58, 0.62))
        for ch in stamp + "第 %d / %d 页" % (i + 1, n_pages):
            all_codes.append(ord(ch) if ord(ch) <= 0xFFFF else 0x3F)
        final_streams.append(c.stream())

    # ---- 组装 PDF 对象
    def add(blob: bytes) -> int:
        n = len(objects) + 1
        objects[n] = blob
        return n

    font_cid = None
    font_t0 = None
    cmap_obj = None
    font_descr = None

    # 预留编号：1=Catalog 2=Pages 3=Font 4=CIDFont 5=Descriptor 6=ToUnicode
    catalog_n, pages_n, font_n, cid_n, descr_n, cmap_n = 1, 2, 3, 4, 5, 6
    objects[catalog_n] = b""            # 占位，最后回填
    objects[pages_n] = b""
    next_n = 7

    page_nums = []
    for s in final_streams:
        cnum = next_n
        next_n += 1
        first = next_n
        next_n += 1
        objects[cnum] = (
            "<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
            "/Resources << /Font << /F1 %d 0 R >> /ProcSet [/PDF /Text] >> "
            "/Contents %d 0 R >>" % (pages_n, A4_W, A4_H, font_n, first)
        ).encode("latin-1")
        objects[first] = (b"<< /Length %d >>\nstream\n" % len(s)) + s + b"\nendstream"
        page_nums.append(cnum)

    objects[pages_n] = (
        "<< /Type /Pages /Count %d /Kids [%s] >>"
        % (len(page_nums), " ".join("%d 0 R" % n for n in page_nums))
    ).encode("latin-1")

    objects[font_n] = (
        "<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
        "/Encoding /UniGB-UCS2-H /DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
        % (cid_n, cmap_n)
    ).encode("latin-1")
    objects[cid_n] = (
        "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
        "/FontDescriptor %d 0 R /DW 1000 >>" % descr_n
    ).encode("latin-1")
    objects[descr_n] = (
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
        b"/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -254 "
        b"/CapHeight 880 /StemV 93 >>"
    )
    cmap_bytes = _tounicode_cmap(all_codes)
    objects[cmap_n] = (b"<< /Length %d >>\nstream\n" % len(cmap_bytes)) + cmap_bytes \
        + b"\nendstream"
    objects[catalog_n] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_n

    # ---- 序列化（保持对象号顺序，便于交叉引用表精确计算偏移）
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for n in sorted(objects.keys()):
        offsets[n] = out.tell()
        out.write(("%d 0 obj\n" % n).encode("latin-1"))
        out.write(objects[n])
        out.write(b"\nendobj\n")

    xref_pos = out.tell()
    max_n = max(objects.keys())
    out.write(("xref\n0 %d\n" % (max_n + 1)).encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for n in range(1, max_n + 1):
        out.write(("%010d 00000 n \n" % offsets.get(n, 0)).encode("latin-1"))
    out.write(("trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
               % (max_n + 1, catalog_n, xref_pos)).encode("latin-1"))
    return out.getvalue()


def write(path: str, pages, title="审查意见报告", meta_lines=None, footer="") -> str:
    data = build(pages, title=title, meta_lines=meta_lines, footer=footer)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ------------------------------------------------------------------ 便捷：从报告数据结构生成
def pages_from_report(case, doc, meta, hits, summary, coverage, clauses,
                      lawyer_opinion="", wb_last=None):
    """把审查报告的数据结构摊平成 (kind, text) 列表，供 build() 使用。

    字段与 `output.build_report_md` 保持一致，保证三通道（MD / HTML / PDF）
    口径同源 —— 不出现「PDF 里少了一节」的交付风险。
    """
    pages = [[]]

    def put(kind, text):
        pages[-1].append((kind, text))

    def newpage():
        pages.append([])

    put("h1", "一、合同基本信息")
    kv = [
        ("合同标题", case.get("title")), ("合同编号", case.get("case_no")),
        ("送审部门", case.get("send_dept")), ("申请人", case.get("applicant")),
        ("业务类型", case.get("business_type_label")),
        ("相对方主体", case.get("counterparty") or "未载明"),
        ("统一社会信用代码", case.get("credit_code") or "未载明"),
        ("合同金额", case.get("amount_text") or "未载明"),
        ("履约周期", case.get("perform_period") or "未载明"),
        ("生效条件", case.get("effective_condition") or "未载明"),
        ("合同文档", (doc or {}).get("filename") or "-"),
        ("解析引擎", (doc or {}).get("parse_engine") or "-"),
    ]
    for k, v in kv:
        put("kv", "%s：%s" % (k, v if v not in (None, "") else "未载明"))

    put("h1", "二、综合审查结论")
    put("body", "总风险等级：%s" % (case.get("risk_level_label") or "-"))
    put("body", "审查结论：%s" % (case.get("conclusion_label") or "-"))
    put("body", summary.get("headline") or "")
    if summary.get("headline_llm"):
        put("body", "模型润色摘要：%s" % summary["headline_llm"])
    put("kv", "摘要生成方式：%s" % (summary.get("summary_source") or "rule-template"))
    for a in summary.get("attention") or []:
        put("bullet", a)

    put("h1", "三、风险清单明细")
    if not hits:
        put("body", "未命中规则库覆盖范围内的风险项。")
    for i, h in enumerate(hits, 1):
        lvl = h.get("risk_level")
        put("risk_%s" % lvl if lvl in ("high", "medium", "low") else "h3",
            "%d. %s ｜ %s ｜ 条款类型：%s ｜ 匹配模式：%s"
            % (i, h.get("rule_name"), h.get("risk_level_label"),
               h.get("clause_type"), h.get("match_mode")))
        loc = "第 %s 页 / 第 %s 段" % (
            h.get("page_no") if h.get("page_no") is not None else "全文",
            h.get("para_index") if h.get("para_index") is not None else "-")
        put("kv", "定位：%s ｜ 字符区间：[%s, %s）"
            % (loc, h.get("char_start"), h.get("char_end")))
        put("kv", "原文片段：%s" % (h.get("evidence") or "-"))
        put("kv", "风险成因：%s" % (h.get("reason") or "-"))
        put("kv", "法律/合规依据：%s" % (h.get("legal_basis") or "-"))
        put("kv", "推荐修改条款：%s" % (h.get("suggestion_final") or h.get("suggestion") or "-"))
        for alt in (h.get("suggestion_alternatives") or [])[:3]:
            put("bullet", "备选：%s" % (alt if isinstance(alt, str)
                                      else alt.get("text") or alt))
        if h.get("lawyer_note"):
            put("kv", "法务批注：%s" % h["lawyer_note"])
        if h.get("adopted"):
            put("kv", "采纳状态：已采纳")

    put("h1", "四、条款覆盖情况")
    put("body", "识别条款类型 %s / %s 类，覆盖率 %s%%"
        % (len(coverage.get("present") or []), coverage.get("total_types") or 0,
           round((coverage.get("coverage_rate") or 0) * 100, 1)))
    if coverage.get("present"):
        put("kv", "已覆盖：%s" % "、".join(coverage["present"]))
    if coverage.get("missing"):
        put("kv", "未见条款：%s" % "、".join(coverage["missing"]))

    if clauses:
        put("h1", "五、条款层级切分")
        for c in clauses[:60]:
            put("kv", "%s ｜ %s" % (c.get("clause_type") or "-",
                                    (c.get("title") or c.get("text") or "")[:70]))

    if lawyer_opinion:
        put("h1", "六、法务人工批注")
        put("body", lawyer_opinion)

    put("h1", "审批回写状态")
    if wb_last:
        put("kv", "最近一次回写：%s ｜ 模式 %s ｜ 通道 %s ｜ 评论号 %s"
            % (wb_last.get("created_at"), wb_last.get("mode"),
               wb_last.get("channel"), wb_last.get("comment_id") or "-"))
    else:
        put("kv", "尚未回写审批单")

    put("note", "边界声明：本审查意见由确定性规则引擎生成、仅供审批参考，"
                "不构成法律意见；系统不具备通过/拒绝/转交审批单的能力，"
                "最终结论由审批人作出。")
    return pages
