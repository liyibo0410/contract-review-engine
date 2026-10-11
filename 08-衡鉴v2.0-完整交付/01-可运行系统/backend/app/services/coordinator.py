# -*- coding: utf-8 -*-
"""协调器：把三种格式的解析结果收敛到同一个坐标系

这是"条款级双锚定证据链"的地基。核心约定只有一条：

    全文字符串 full_text 是唯一的真理坐标系。
    任何一条风险记录，最终都必须能落到 full_text 的 [char_start, char_end) 区间；
    page / para_index / heading 只是让人读得懂的"人话定位"。

这样做的好处是：docx（无真实页码）、pdf（有页码）、扫描件（OCR 行框）三种来源
在数据层完全同构 —— 前端的双向高亮只需要一种渲染逻辑，不需要为每种格式写一套。

本模块对外提供四个纯函数：
    normalize_blocks    块序列 -> 全文 + 字符区间
    classify_blocks      块序列 -> 条款类型标注
    build_clauses        块序列 -> 条款结构树
    extract_metadata     全文     -> 核心元数据（带坐标）
    decide_block         解析结果 -> 是否阻塞 / 阻塞原因
"""
from __future__ import annotations

import re

from .. import config

# ------------------------------------------------------------------ 标题识别
HEADING_RE = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百零〇\d]{1,4}[条节章分]"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]"
    r"|\d{1,2}(?:\.\d{1,2}){0,2}\s*[、.．]?\s*(?=\S)"
    r"|【[^】]{2,24}】"
    r")\s*"
)

CLAUSE_NUM_RE = re.compile(r"第[一二三四五六七八九十百零〇\d]{1,4}条")

# ------------------------------------------------------------------ 条款类型词表
# 注意：这里**刻意不放 甲方 / 乙方 / 供方 / 需方 这类角色词**。
# 角色词在合同里无处不在（几乎每段都会提到双方），一旦计入打分，
# 就会把"知识产权归属"段判成"签约主体"段，导致整篇条款类型错位 —— 实测踩过这个坑。
# 主体类判定只认"主体属性词"（统一社会信用代码、法定代表人、营业执照…）。
CLAUSE_KEYWORDS: dict[str, list[str]] = {
    "subject": ["统一社会信用代码", "法定代表人", "住所地", "营业执照", "资质证书",
                "主体资格", "注册资本", "许可证", "经营异常", "授权委托书"],
    "subject_matter": ["标的", "货物名称", "规格型号", "服务内容", "工作范围", "产品名称",
                       "数量", "单价", "型号", "技术参数", "功能模块"],
    "payment": ["价款", "合同金额", "付款", "支付", "结算", "发票", "预付款",
                "定金", "货款", "费用", "里程碑", "尾款", "工资"],
    "delivery": ["交付", "交货", "验收", "收货", "安装", "调试", "到货", "工期", "交付地点",
                 "配送", "运输", "质保期"],
    "liability": ["违约", "赔偿责任", "违约金", "损害赔偿", "逾期", "单方解除", "无限责任",
                  "解约金", "承担损失"],
    "confidentiality": ["保密", "商业秘密", "机密", "不得披露", "保密期限"],
    "ip": ["知识产权", "著作权", "专利", "商标", "源代码", "技术成果", "所有权归属", "许可",
           "职务发明"],
    "data_security": ["数据安全", "个人信息", "数据保护", "跨境传输", "数据泄露", "隐私"],
    "dispute": ["争议", "仲裁", "诉讼", "管辖", "人民法院", "协商解决", "劳动争议仲裁"],
    "validity": ["生效", "有效期", "合同期限", "终止", "解除条件", "签署之日", "以完成一定工作"],
    "force_majeure": ["不可抗力", "自然灾害", "疫情", "战争", "通知义务"],
}

# ------------------------------------------------------------------ 元数据正则
RE_USCC = re.compile(r"[0-9A-HJ-NPQRTUWXY]{18}")
RE_AMOUNT = re.compile(
    r"(?:人民币|RMB|￥|¥)?\s*([0-9][0-9,，]*(?:\.\d{1,2})?)\s*(万元|万|亿元|亿|元)"
)
RE_DATE = re.compile(r"(20\d{2}\s*[年\-/\.]\s*\d{1,2}\s*[月\-/\.]\s*\d{1,2}\s*日?)")
RE_PERIOD = re.compile(r"(?:有效期|合同期限|履行期限|合作期限)[^\n。；]{0,6}?(?:自[^\n。；]{0,40}?)?([0-9]{1,3}\s*(?:个月|月|年|日|天))")
RE_CODE = re.compile(r"(?:合同编号|编号|项目编号)\s*[:：]?\s*([A-Za-z0-9\-_/]{4,40})")
RE_PARTY = re.compile(
    r"(甲方|乙方|供方|需方|买方|卖方|发包方|承包方|委托方|受托方)\s*(?:（[^）]*）)?\s*[:：]\s*([^\n，。；]{2,60})"
)


# ------------------------------------------------------------------ ①② normalized
def normalize_blocks(blocks: list[dict]) -> tuple[list[dict], str]:
    """为每个块补上全文字符区间，并拼出唯一真理坐标系 full_text。"""
    offset = 0
    out: list[dict] = []
    for b in blocks:
        text = (b.get("text") or "").rstrip()
        if not text:
            continue
        start = offset
        end = start + len(text)
        nb = dict(b)
        nb["char_start"] = start
        nb["char_end"] = end
        nb["text"] = text
        out.append(nb)
        offset = end + 1  # 块间以单个 \n 连接
    full_text = "\n".join(b["text"] for b in out)
    return out, full_text


# ------------------------------------------------------------------ ③ 条款分类
def _score_clause_type(text: str) -> tuple[str, int]:
    best, best_hits = "other", 0
    for ctype, words in CLAUSE_KEYWORDS.items():
        hits = sum(1 for w in words if w in text)
        if hits > best_hits:
            best, best_hits = ctype, hits
    return best, best_hits


def classify_blocks(blocks: list[dict]) -> list[dict]:
    """先判定标题块，再把标题语义向下传播到正文块。

    传播是必要的：真实合同的条款正文往往不含"违约责任"字样，
    但整条都在讲违约责任 —— 只按关键词逐块判定会大面积落到 other。

    自证门槛设为 2 个命中关键词；切换条款类型时要求 **严格优于当前上下文**（平局不动）：
    "甲方逾期支付款项的，应承担无限赔偿责任"这类句子，付款关键词与责任关键词各命中 2 个，
    若按字典序决胜会整段跳到 payment，导致后续违约责任段落丢失上下文 —— 平局保持归属更符合阅读直觉。
    """
    out: list[dict] = []
    current = "other"
    for b in blocks:
        nb = dict(b)
        raw = nb["text"]
        is_heading = bool(HEADING_RE.match(raw)) or (len(raw) <= 26 and bool(CLAUSE_NUM_RE.match(raw)))

        own, hits = _score_clause_type(raw)
        if is_heading:
            current = own if hits > 0 else current
            nb["clause_type"] = current
        else:
            if hits >= 2:
                current_score = sum(1 for w in CLAUSE_KEYWORDS.get(current, []) if w in raw)
                if hits > current_score:
                    current = own
            nb["clause_type"] = current
        nb["is_heading"] = is_heading
        out.append(nb)
    return out


def build_clauses(blocks: list[dict]) -> list[dict]:
    """把连续同类型的块合并成条款节点。"""
    clauses: list[dict] = []
    for b in blocks:
        text = b["text"]
        if not text.strip():
            continue
        if clauses and clauses[-1]["clause_type"] == b["clause_type"]:
            cur = clauses[-1]
            cur["char_end"] = b["char_end"]
            cur["text"] = cur["text"] + "\n" + text
            cur["para_end"] = b["para_index"]
        else:
            clauses.append({
                "clause_type": b["clause_type"],
                "heading": text[:40],
                "page": b["page"],
                "para_index": b["para_index"],
                "para_end": b["para_index"],
                "char_start": b["char_start"],
                "char_end": b["char_end"],
                "text": text,
                "confidence": b.get("confidence"),
            })
    return clauses


def locate(full_text: str, needle: str, from_pos: int = 0) -> tuple[int, int]:
    """在全文里定位一段文字，返回 (start, end)；找不到返回 (-1, -1)。"""
    if not needle:
        return -1, -1
    pos = full_text.find(needle, from_pos)
    if pos < 0:
        compact = " ".join(needle.split())
        pos = full_text.find(compact)
        if pos < 0:
            return -1, -1
        needle = compact
    return pos, pos + len(needle)


def coord_of(full_text: str, blocks: list[dict], start: int) -> tuple[int, int]:
    """字符偏移 -> (page, para_index)。"""
    page, para = 1, 0
    for b in blocks:
        if b["char_start"] <= start < b["char_end"]:
            return b["page"], b["para_index"]
        if b["char_start"] > start:
            break
        page, para = b["page"], b["para_index"]
    return page, para


# ------------------------------------------------------------------ ④ 元数据
def _mk(name: str, label: str, value: str, full_text: str, blocks: list[dict],
        anchor: str | None = None) -> dict | None:
    if not value:
        return None
    start, end = locate(full_text, anchor or value)
    page, para = coord_of(full_text, blocks, start) if start >= 0 else (1, 0)
    return {
        "key": name,
        "label": label,
        "value": value.strip(),
        "char_start": start,
        "char_end": end,
        "page": page,
        "para_index": para,
    }


def extract_metadata(full_text: str, blocks: list[dict]) -> dict:
    items: list[dict] = []

    def add(item: dict | None):
        if item and item["value"]:
            items.append(item)

    # 合同名称
    m = re.search(r"^\s*[【《]?([^】》\n]{4,40}合同[^】》\n]{0,20})[】》]?\s*$", full_text, re.M)
    if m:
        add(_mk("contract_name", "合同名称", m.group(1).strip(), full_text, blocks, m.group(1).strip()))

    # 合同编号
    m = RE_CODE.search(full_text)
    if m:
        add(_mk("contract_code", "合同编号", m.group(1), full_text, blocks, m.group(1)))

    # 签约主体
    parties: dict[str, str] = {}
    for role, name in RE_PARTY.findall(full_text):
        name = name.strip().strip("：: ").strip()
        if role not in parties and 1 < len(name) < 50:
            parties[role] = name
    for role in ("甲方", "乙方", "供方", "需方", "买方", "卖方", "发包方", "承包方", "委托方", "受托方"):
        if role in parties:
            add(_mk("party_" + role, f"{role}名称", parties[role], full_text, blocks, parties[role]))

    # 统一社会信用代码
    m = RE_USCC.search(full_text)
    if m:
        add(_mk("uscc", "统一社会信用代码", m.group(0), full_text, blocks, m.group(0)))

    # 合同金额
    best = None
    for mm in RE_AMOUNT.finditer(full_text):
        raw = mm.group(1).replace(",", "").replace("，", "")
        try:
            num = float(raw)
        except ValueError:
            continue
        unit = mm.group(2)
        factor = {"万元": 10000, "万": 10000, "亿元": 100000000, "亿": 100000000, "元": 1}[unit]
        amount = num * factor
        if best is None or amount > best[0]:
            best = (amount, mm.group(0))
    if best:
        add(_mk("amount", "合同金额", best[1].strip(), full_text, blocks, best[1]))
        items[-1]["amount_value"] = best[0]

    # 币种
    currency = "CNY"
    if "美元" in full_text or "USD" in full_text:
        currency = "USD"
    elif "欧元" in full_text or "EUR" in full_text:
        currency = "EUR"
    if "人民币" in full_text:
        currency = "CNY"
    items.append({"key": "currency", "label": "币种", "value": currency,
                  "char_start": -1, "char_end": -1, "page": 1, "para_index": 0})

    # 日期：签订日 / 生效日
    dates = RE_DATE.findall(full_text)
    if dates:
        add(_mk("sign_date", "签订日期", dates[0], full_text, blocks, dates[0]))
        if len(dates) > 1:
            add(_mk("perform_start", "生效/起始日期", dates[1], full_text, blocks, dates[1]))
        if len(dates) > 2:
            add(_mk("perform_end", "履行期限截止", dates[-1], full_text, blocks, dates[-1]))

    # 履行期限（文字型）
    m = RE_PERIOD.search(full_text)
    if m:
        add(_mk("perform_period", "履行期限", m.group(1), full_text, blocks, m.group(0)))

    return {"items": items, "currency": currency,
            "amount_value": best[0] if best else None}


# ------------------------------------------------------------------ ⑤ 阻塞判定
def decide_block(parse: dict, full_text: str, ocr_avg: float | None,
                 ocr_error: str | None = None) -> tuple[bool, str, str]:
    """返回 (是否阻塞, 阻塞代码, 阻塞原因)。"""
    if parse.get("encrypted"):
        return True, "DOC_ENCRYPTED", "文档已加密，无法解析正文，请上传去除密码的版本或提供打开密码"

    text_len = len(full_text.strip())
    if text_len < config.MIN_TEXT_CHARS:
        if ocr_error:
            return True, "OCR_ENGINE_UNAVAILABLE", f"扫描件正文为空，且 OCR 引擎不可用：{ocr_error}"
        if ocr_avg is not None and ocr_avg < config.OCR_BLOCK_THRESHOLD:
            return True, "OCR_LOW_CONFIDENCE", (
                f"扫描件识别置信度过低（{ocr_avg:.2f} < {config.OCR_BLOCK_THRESHOLD:.2f}），"
                "正文不可用，建议重新上传清晰扫描件"
            )
        return True, "EMPTY_CONTENT", f"解析后正文仅 {text_len} 字，低于最小阈值 {config.MIN_TEXT_CHARS}，疑为空文或纯图片无文字"

    if ocr_avg is not None and ocr_avg < config.OCR_MIN_CONFIDENCE:
        # 置信度偏低但正文可用：不阻塞，只降级提示（审查结果附带质量提示）
        return False, "OCR_LOW_QUALITY", f"扫描件识别置信度偏低（{ocr_avg:.2f}），部分条款可能识别不全，建议人工复核"

    return False, "", ""


def detect_file_type(file_name: str, path: str) -> str:
    ext = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
    if ext in {"docx", "doc", "wps"}:
        return "docx"
    if ext in {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}:
        return "image"
    if ext == "pdf":
        try:
            import fitz
            doc = fitz.open(path)
            try:
                has_text = any(len((doc.load_page(i).get_text("text") or "").strip()) > 30
                               for i in range(min(3, doc.page_count)))
            finally:
                doc.close()
            return "pdf" if has_text else "scan_pdf"
        except Exception:
            return "pdf"
    if ext in {"txt", "md"}:
        return "text"
    return "unknown"
