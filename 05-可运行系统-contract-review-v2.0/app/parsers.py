# -*- coding: utf-8 -*-
"""文档解析引擎（零第三方依赖）

- DOCX：标准库 zipfile + xml.etree 直接解析 OOXML，输出段落树 / 表格 / 页码
- PDF ：自实现对象扫描 + FlateDecode + ToUnicode CMap 映射，输出分页文本
- 扫描件/图片：调用 Windows 内置 OCR（WinRT），见 ocr_win.py

所有解析结果统一为「段落树」结构，每个段落都带页码与原文起止字符位置，
以满足「双向可溯源定位」的要求。
"""
from __future__ import annotations

import base64
import io
import os
import re
import zipfile
import zlib

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

DOCX = "docx"
PDF = "pdf"
IMAGE = "image"
UNKNOWN = "unknown"

ENGINE_DOCX = "docx-xml"
ENGINE_PDF = "pdf-text"
ENGINE_OCR = "ocr-win"
ENGINE_NONE = "-"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}


# ------------------------------------------------------------------ 类型探测
def detect_type(path: str, filename: str = "") -> str:
    name = (filename or os.path.basename(path) or "").lower()
    ext = os.path.splitext(name)[1]
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        head = b""
    if head.startswith(b"%PDF"):
        return PDF
    if head.startswith(b"PK\x03\x04"):
        # OOXML 容器；进一步确认是否含 word/document.xml
        try:
            with zipfile.ZipFile(path) as z:
                if "word/document.xml" in z.namelist():
                    return DOCX
        except Exception:
            pass
        return DOCX if ext in DOCX_EXT else UNKNOWN
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head.startswith(b"BM"):
        return IMAGE
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "doc_legacy"          # 旧版 .doc，需转换后解析
    if ext in PDF_EXT:
        return PDF
    if ext in DOCX_EXT:
        return DOCX
    if ext in IMAGE_EXT:
        return IMAGE
    return UNKNOWN


# ------------------------------------------------------------------ 段落树工具
def build_paragraphs(raw_blocks):
    """raw_blocks: [(text, page, is_heading)] -> 带字符位置的段落树。

    同时在 flat_text 上记录每个段落的 [char_start, char_end)，用于前端高亮锚定。
    """
    paras = []
    cursor = 0
    pieces = []
    for i, (text, page, is_heading) in enumerate(raw_blocks):
        text = (text or "").strip()
        if not text:
            continue
        start = cursor
        end = start + len(text)
        paras.append({
            "index": len(paras),
            "text": text,
            "page": page,
            "is_heading": bool(is_heading),
            "char_start": start,
            "char_end": end,
        })
        pieces.append(text)
        cursor = end + 1                      # +1 对应拼接时的换行符
    flat = "\n".join(pieces)
    return paras, flat


# ------------------------------------------------------------------ 页眉页脚剔除（P1-02）
# 判定依据：**跨页重复的短行** + **位于页面首尾边缘** + **不在保护名单内**。
# 三条同时满足才剔除，任一不满足一律保留 —— 宁可留一行噪声，也不能删掉正文。
_HDR_PROTECT = (
    re.compile(r"^\s*(第[一二三四五六七八九十百千零〇\d]+[章条节款项]|[一二三四五六七八九十]+[、．.]|\d+[\.、\)）])"),
    re.compile(r"(甲方|乙方|丙方|供方|需方|买方|卖方|委托方|受托方|发包方|承包方|"
               r"用人单位|劳动者|出租方|承租方|许可方|被许可方)"),
    re.compile(r"(金额|价款|价格|人民币|元整|万元|亿元|大写|小写)"),
    re.compile(r"(合同编号|合同名称|统一社会信用代码|附件|签署|盖章|签字|日期)"),
)
# 典型页眉页脚形态（页码 / 密级 / 文件编号 / 分隔线页码）。
# 这一类**逐页内容不同**（「第 1 页 共 3 页」vs「第 2 页 共 3 页」），
# 靠「跨页重复」抓不到，必须用形态匹配单独兜一层。
_HDR_TYPICAL = re.compile(
    r"^(?:"
    r"第\s*\d+\s*页(?:\s*(?:共|/|，|,)\s*\d+\s*页)?"
    r"|Page\s*\d+(?:\s*(?:of|/)\s*\d+)?"
    r"|\d+\s*/\s*\d+"
    r"|[-—–]{2,}\s*\d+\s*[-—–]{2,}"
    r"|内部资料|内部文件|仅供内部使用|请勿外传|严禁外传"
    r"|绝密|机密|秘密|保密文件|保密资料"
    r"|(?:合同|文件|文档)编号[:：]?\s*[A-Za-z0-9\-/]{3,}"
    r"|[A-Z]{2,}-\d{4}-\d{2,}"
    r")$")


def _norm_line(s: str) -> str:
    """归一化：去空白、去常见分隔符，让「第 1 页」与「第1页」视为同一行。"""
    return re.sub(r"[\s·•\-—_]+", "", s or "")


def _hdr_protected(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    for pat in _HDR_PROTECT:
        if pat.search(t):
            return True
    return False


def strip_repeat_lines(raw_blocks, min_pages: int = 2, max_chars: int = 46,
                       edge_lines: int = 2):
    """剔除页眉/页脚短行。

    两条判定通道，取并集：
      A. **跨页重复边缘短行** —— 同一行（归一化后）出现在 ≥ min_pages 页的首尾边缘；
      B. **典型形态边缘行** —— 命中「页码 / 密级 / 文件编号」形态且位于页面边缘。
        页码逐页不同（1、2、3…），只靠重复抓不到，必须单列这一条。

    保护名单优先于两条通道：条款标题、含「甲方/乙方/金额/编号/签署」等实体词的行
    一律保留 —— 宁可留一行噪声，也不能删掉正文。

    返回 (kept_blocks, removed) —— removed 为被剔除行明细（含出现页与判定原因），
    由调用方写审计，保证「删了什么」可查、可复核、可回滚。
    """
    if not raw_blocks:
        return list(raw_blocks), []
    by_page = {}
    order = []
    for i, (_t, pg, _h) in enumerate(raw_blocks):
        if pg not in by_page:
            by_page[pg] = []
            order.append(pg)
        by_page[pg].append(i)

    removed = {}

    def _edge_indexes(pg):
        idxs = by_page[pg]
        edge = idxs[:int(edge_lines)]
        if len(idxs) > int(edge_lines):
            edge = edge + idxs[-int(edge_lines):]
        return list(dict.fromkeys(edge))

    def _candidate(i):
        text, _pg, _h = raw_blocks[i]
        t = (text or "").strip()
        if len(t) < 2 or len(t) > int(max_chars):
            return ""
        if _hdr_protected(t):
            return ""
        return t

    # ---- 通道 B：典型形态边缘行（单页也适用；页码只有一页时同样应剔除）
    for pg in order:
        for i in _edge_indexes(pg):
            t = _candidate(i)
            if t and _HDR_TYPICAL.match(t):
                removed[i] = {"text": raw_blocks[i][0], "page": pg,
                              "repeat_pages": [pg],
                              "channel": "typical",
                              "reason": "页面边缘的页码/密级/编号类典型行"}

    # ---- 通道 A：跨页重复边缘短行
    if len(order) >= max(2, int(min_pages)):
        cand = {}
        for pg in order:
            for i in _edge_indexes(pg):
                t = _candidate(i)
                if not t:
                    continue
                key = _norm_line(t)
                if not key:
                    continue
                ent = cand.setdefault(key, {"text": t, "pages": set(), "idxs": []})
                ent["pages"].add(pg)
                ent["idxs"].append(i)
        for _key, ent in cand.items():
            if len(ent["pages"]) < int(min_pages):
                continue
            typical = bool(_HDR_TYPICAL.match(ent["text"].strip()))
            for i in ent["idxs"]:
                removed[i] = {
                    "text": raw_blocks[i][0],
                    "page": raw_blocks[i][1],
                    "repeat_pages": sorted(ent["pages"]),
                    "channel": "repeat",
                    "reason": "跨 %d 页重复的页面边缘短行%s" % (
                        len(ent["pages"]), "（页码/密级典型形态）" if typical else ""),
                }

    kept = [b for i, b in enumerate(raw_blocks) if i not in removed]
    return kept, [removed[i] for i in sorted(removed)]


# ------------------------------------------------------------------ 剔除开关
def _maybe_strip(raw_blocks, strip_cfg):
    """按配置执行页眉页脚剔除；未启用时原样返回（strip_cfg 为 None 即关闭）。"""
    if not strip_cfg or not strip_cfg.get("enabled", True):
        return list(raw_blocks), []
    return strip_repeat_lines(
        raw_blocks,
        min_pages=int(strip_cfg.get("min_pages", 2)),
        max_chars=int(strip_cfg.get("max_chars", 46)),
        edge_lines=int(strip_cfg.get("edge_lines", 2)),
    )


# ------------------------------------------------------------------ DOCX
def parse_docx(path: str, strip_cfg=None):
    """解析 .docx，返回统一结果字典。"""
    result = {
        "ok": False, "doc_type": DOCX, "engine": ENGINE_DOCX,
        "paragraphs": [], "text": "", "page_count": 0,
        "char_count": 0, "error": "", "need_manual": False, "ocr_confidence": 0.0,
        "tables": [], "stripped": [],
    }
    try:
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                result["error"] = "DOCX 容器缺少 word/document.xml，文件可能已损坏"
                result["need_manual"] = True
                return result
            xml = z.read("word/document.xml")
    except Exception as exc:                                   # noqa: BLE001
        result["error"] = "DOCX 打开失败：%s" % exc
        result["need_manual"] = True
        return result

    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
    except Exception as exc:                                   # noqa: BLE001
        result["error"] = "DOCX XML 解析失败：%s" % exc
        result["need_manual"] = True
        return result

    body = root.find(W + "body")
    if body is None:
        result["error"] = "DOCX 缺少 body 节点"
        result["need_manual"] = True
        return result

    raw_blocks = []
    tables = []
    page = 1

    def para_text(p):
        nonlocal page
        buf = []
        for node in p.iter():
            tag = node.tag
            if tag == W + "t":
                buf.append(node.text or "")
            elif tag == W + "tab":
                buf.append("\t")
            elif tag == W + "br":
                if (node.get(W + "type") or "") == "page":
                    page += 1
                    buf.append("\n")
                else:
                    buf.append("\n")
            elif tag == W + "lastRenderedPageBreak":
                page += 1
        return "".join(buf)

    def para_style(p):
        ppr = p.find(W + "pPr")
        if ppr is None:
            return ""
        st = ppr.find(W + "pStyle")
        return (st.get(W + "val") or "") if st is not None else ""

    for child in body:
        if child.tag == W + "p":
            txt = para_text(child)
            style = para_style(child)
            is_head = style.lower().startswith("heading") or style.startswith("标题") \
                or bool(re.match(r"^\s*(第[一二三四五六七八九十百]+[章条节]|[一二三四五六七八九十]+、|\d+\.\s)", txt))
            raw_blocks.append((txt, page, is_head))
        elif child.tag == W + "tbl":
            rows = []
            for tr in child.findall(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    ct = " ".join((t.text or "") for t in tc.iter(W + "t")).strip()
                    cells.append(ct)
                rows.append(cells)
                raw_blocks.append((" | ".join(cells), page, False))
            if rows:
                tables.append(rows)

    raw_blocks, stripped = _maybe_strip(raw_blocks, strip_cfg)
    paras, flat = build_paragraphs(raw_blocks)
    result.update({
        "ok": bool(flat.strip()),
        "paragraphs": paras,
        "text": flat,
        "page_count": page,
        "char_count": len(flat),
        "tables": tables,
        "stripped": stripped,
    })
    if not flat.strip():
        result["error"] = "DOCX 正文为空"
        result["need_manual"] = True
    return result


# ------------------------------------------------------------------ PDF
_OBJ_HEAD = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")


class _PdfObjects:
    """扫描式对象表，不依赖 xref（兼容交叉引用表/流两种写法）。"""

    def __init__(self, data: bytes):
        self.data = data
        self.objs = {}
        for m in _OBJ_HEAD.finditer(data):
            num = int(m.group(1))
            start = m.end()
            end = data.find(b"endobj", start)
            if end < 0:
                end = len(data)
            self.objs[num] = data[start:end]

    def get(self, num: int) -> bytes:
        return self.objs.get(num, b"")

    def refs(self, body: bytes):
        return [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R\b", body)]

    def stream_of(self, body: bytes):
        i = body.find(b"stream")
        if i < 0:
            return None
        j = body.find(b"endstream", i)
        if j < 0:
            return None
        raw = body[i + 6:j]
        if raw.startswith(b"\r\n"):
            raw = raw[2:]
        elif raw.startswith(b"\n") or raw.startswith(b"\r"):
            raw = raw[1:]
        return raw

    @staticmethod
    def _a85(raw: bytes):
        data = re.sub(rb"\s", b"", raw)
        if data.endswith(b"~>"):
            data = data[:-2]
        try:
            return base64.a85decode(data, adobe=False, ignorechars=b"")
        except Exception:                                        # noqa: BLE001
            try:
                return base64.a85decode(data + b"~>", adobe=True)
            except Exception:                                    # noqa: BLE001
                return None

    @staticmethod
    def _flate(raw: bytes):
        for wbits in (15, -15, 47):
            try:
                return zlib.decompress(raw, wbits)
            except Exception:                                    # noqa: BLE001
                continue
        i = raw.find(b"\x78")
        if i > 0:
            try:
                return zlib.decompress(raw[i:])
            except Exception:                                    # noqa: BLE001
                pass
        return None

    def decode(self, body: bytes):
        """按 /Filter 链顺序解码流（支持 ASCII85 / ASCIIHex / Flate 及其组合链）。"""
        raw = self.stream_of(body)
        if raw is None:
            return None
        idx = body.find(b"stream")
        head = body[:idx] if idx > 0 else body
        filters = re.findall(rb"/(\w+Decode)", head)
        if not filters:
            return raw
        data = raw
        for f in filters:
            if f == b"ASCII85Decode":
                data = self._a85(data)
            elif f == b"ASCIIHexDecode":
                h = re.sub(rb"[^0-9A-Fa-f]", b"", data)
                if len(h) % 2:
                    h += b"0"
                try:
                    data = bytes.fromhex(h.decode("ascii"))
                except Exception:                                # noqa: BLE001
                    data = None
            elif f == b"FlateDecode":
                data = self._flate(data)
            elif f in (b"DCTDecode", b"JPXDecode", b"CCITTFaxDecode",
                       b"JBIG2Decode", b"RunLengthDecode", b"LZWDecode"):
                # 图像类与字典压缩滤镜不由本模块解码
                return None
            if data is None:
                return None
        return data


def _hex_to_bytes(tok: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", tok)
    if len(h) % 2:
        h += b"0"
    try:
        return bytes.fromhex(h.decode("ascii"))
    except Exception:                                        # noqa: BLE001
        return b""


def _parse_cmap(data: bytes):
    """解析 ToUnicode CMap -> {code:int -> unicode_str} 与代码字节长度。"""
    mapping = {}
    nbytes = 2
    m = re.search(rb"begincodespacerange(.*?)endcodespacerange", data, re.S)
    if m:
        first = re.search(rb"<([0-9A-Fa-f]+)>", m.group(1))
        if first:
            nbytes = max(1, len(first.group(1)) // 2)

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        pairs = re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block)
        for src, dst in pairs:
            try:
                code = int(src, 16)
            except ValueError:
                continue
            mapping[code] = _hex_to_bytes(dst).decode("utf-16-be", "ignore")

    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])",
                block, re.S):
            try:
                start, stop = int(lo, 16), int(hi, 16)
            except ValueError:
                continue
            if rest.startswith(b"["):
                items = re.findall(rb"<([0-9A-Fa-f]*)>", rest)
                for k, dst in enumerate(items):
                    if start + k > stop:
                        break
                    mapping[start + k] = _hex_to_bytes(dst).decode("utf-16-be", "ignore")
            else:
                base = _hex_to_bytes(rest)
                if not base:
                    continue
                base_val = int.from_bytes(base, "big")
                width = len(base)
                for code in range(start, min(stop, start + 65536) + 1):
                    v = base_val + (code - start)
                    mapping[code] = v.to_bytes(max(width, 2), "big").decode(
                        "utf-16-be", "ignore")
    return mapping, nbytes


def _page_objects(objs: _PdfObjects):
    """按页面树顺序返回页面对象号；失败则退化为对象号排序。"""
    order = []
    root = None
    for num, body in objs.objs.items():
        if b"/Type" in body and b"/Catalog" in body:
            root = body
            break
    if root:
        seen = set()

        def walk(body, depth=0):
            if depth > 64 or len(order) > 2000:
                return
            if b"/Type" in body and b"/Page" in body and b"/Pages" not in body:
                ref = re.search(rb"(\d+)\s+\d+\s+R", body)
                # 页面自身对象号由调用方比对，这里用 /Kids 顺序
                return
            kids = re.search(rb"/Kids\s*\[(.*?)\]", body, re.S)
            if not kids:
                return
            for kid in re.findall(rb"(\d+)\s+\d+\s+R", kids.group(1)):
                k = int(kid)
                if k in seen:
                    continue
                seen.add(k)
                kb = objs.get(k)
                if b"/Kids" in kb:
                    walk(kb, depth + 1)
                else:
                    order.append(k)

        pages_ref = re.search(rb"/Pages\s+(\d+)\s+\d+\s+R", root)
        if pages_ref:
            walk(objs.get(int(pages_ref.group(1))))
    if not order:
        order = sorted(n for n, b in objs.objs.items()
                       if b"/Type" in b and b"/Page" in b and b"/Pages" not in b)
    return order


def _resolve_dict(objs: _PdfObjects, body: bytes, key: bytes):
    m = re.search(key + rb"\s*(\d+)\s+\d+\s+R", body)
    if m:
        return objs.get(int(m.group(1)))
    m = re.search(key + rb"\s*(\[.*?\]|<<.*?>>)", body, re.S)
    return m.group(1) if m else b""


def _font_maps(objs: _PdfObjects, page_body: bytes):
    """返回 {字体资源名: (cmap_dict, code_bytes)}。"""
    fonts = {}
    res = _resolve_dict(objs, page_body, rb"/Resources")
    if not res:
        # 页面未自带资源时，向上查找父 /Pages 节点的资源（PDF 标准继承行为）
        for body in objs.objs.values():
            if b"/Kids" in body and b"/Resources" in body:
                res = _resolve_dict(objs, body, rb"/Resources")
                if res:
                    break
    if not res:
        return fonts
    fdict = _resolve_dict(objs, res, rb"/Font")
    if not fdict:
        return fonts
    for name, num in re.findall(rb"/([A-Za-z0-9+.\-_]+)\s+(\d+)\s+\d+\s+R", fdict):
        body = objs.get(int(num))
        cmap, nbytes = {}, 2
        if b"/Type0" not in body:
            nbytes = 1
        tu = _resolve_dict(objs, body, rb"/ToUnicode")
        if tu:
            data = objs.decode(tu) or tu
            if data:
                cmap, nbytes = _parse_cmap(data)
        fonts[name.decode("latin-1")] = (cmap, nbytes)
    return fonts


_STR_TOK = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>", re.S)


def _decode_literal(tok: bytes) -> bytes:
    body = tok[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c == 0x5C and i + 1 < len(body):
            nxt = body[i + 1]
            mp = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                  0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
            if nxt in mp:
                out.append(mp[nxt])
                i += 2
                continue
            if 0x30 <= nxt <= 0x37:
                oct_digits = body[i + 1:i + 4]
                try:
                    out.append(int(oct_digits, 8) & 0xFF)
                except ValueError:
                    pass
                i += 1 + len(oct_digits)
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return bytes(out)


def _map_bytes(raw: bytes, cmap, nbytes: int) -> str:
    if not raw:
        return ""
    if nbytes == 2 or (cmap and max(cmap) > 255):
        text = []
        for i in range(0, len(raw) - 1, 2):
            code = (raw[i] << 8) | raw[i + 1]
            ch = cmap.get(code)
            if ch is None:
                try:
                    ch = bytes([raw[i], raw[i + 1]]).decode("utf-16-be")
                except Exception:                            # noqa: BLE001
                    ch = ""
            text.append(ch or "")
        return "".join(text)
    text = []
    for b in raw:
        if cmap and b in cmap:
            text.append(cmap[b])
        elif b in (9, 10, 13) or 32 <= b < 127:
            text.append(chr(b))
        elif b >= 0xA0:
            text.append(bytes([b]).decode("latin-1"))
    return "".join(text)


def _extract_content_text(content: bytes, fonts):
    out = []
    cur_font = None
    if not content:
        return ""
    # 逐行扫描，识别 Tf 选择字体、TJ/Tj/'/" 输出文本
    for m in re.finditer(
            rb"/([A-Za-z0-9+.\-_]+)\s+[\d.]+\s+Tf|"
            rb"(\[(?:[^\[\]\\]|\\.)*\])\s*TJ|"
            rb"(\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>)\s*(?:Tj|'|\")|"
            rb"(T\*|TD|Td)",
            content, re.S):
        if m.group(1):
            cur_font = m.group(1).decode("latin-1")
            continue
        if m.group(4):
            out.append("\n")
            continue
        cmap, nbytes = fonts.get(cur_font or "", ({}, 2))
        if m.group(2):
            for tm in _STR_TOK.finditer(m.group(2)):
                tok = tm.group(0)
                raw = _decode_literal(tok) if tok.startswith(b"(") else _hex_to_bytes(tok)
                out.append(_map_bytes(raw, cmap, nbytes))
        elif m.group(3):
            tok = m.group(3)
            raw = _decode_literal(tok) if tok.startswith(b"(") else _hex_to_bytes(tok)
            out.append(_map_bytes(raw, cmap, nbytes))
    return "".join(out)


def parse_pdf(path: str, min_chars: int = 30, strip_cfg=None):
    """解析 PDF 文本层，返回统一结果字典。min_chars 为判定「有有效文本层」的阈值。"""
    result = {
        "ok": False, "doc_type": PDF, "engine": ENGINE_PDF,
        "paragraphs": [], "text": "", "page_count": 0,
        "char_count": 0, "error": "", "need_manual": False, "ocr_confidence": 0.0,
        "tables": [], "stripped": [],
    }
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        result["error"] = "PDF 读取失败：%s" % exc
        result["need_manual"] = True
        return result

    if not data.startswith(b"%PDF"):
        result["error"] = "PDF 头标识缺失，文件可能已损坏或被加密"
        result["need_manual"] = True
        return result
    if b"/Encrypt" in data:
        result["error"] = "文档已加密，无法抽取文本，需人工解密后重新上传"
        result["need_manual"] = True
        return result

    objs = _PdfObjects(data)
    pages = _page_objects(objs)
    raw_blocks = []
    total_chars = 0
    for pno, onum in enumerate(pages, start=1):
        body = objs.get(onum)
        fonts = _font_maps(objs, body)
        # /Contents 可能是单个引用、引用数组或内联流
        cnums = []
        m = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", body)
        if m:
            cnums = [int(m.group(1))]
        else:
            m2 = re.search(rb"/Contents\s*\[(.*?)\]", body, re.S)
            if m2:
                cnums = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", m2.group(1))]
        texts = []
        for cnum in cnums:
            cdata = objs.decode(objs.get(cnum))
            if cdata:
                texts.append(_extract_content_text(cdata, fonts))
        page_text = "".join(texts)
        total_chars += len(page_text.strip())
        for line in re.split(r"\n+", page_text):
            line = line.strip()
            if line:
                raw_blocks.append((line, pno, bool(re.match(
                    r"^(第[一二三四五六七八九十百]+[章条节]|[一二三四五六七八九十]+、|\d+[\.、])", line))))

    raw_blocks, stripped = _maybe_strip(raw_blocks, strip_cfg)
    paras, flat = build_paragraphs(raw_blocks)
    result.update({
        "ok": total_chars >= max(1, int(min_chars)),
        "paragraphs": paras, "text": flat,
        "page_count": len(pages) or 0, "char_count": len(flat),
        "stripped": stripped,
    })
    if not result["ok"]:
        result["error"] = ("未抽取到有效文本层（可能为扫描件或图片型 PDF，"
                           "共 %d 页 / %d 字符，阈值 %d）" % (len(pages), total_chars,
                                                          int(min_chars)))
        result["need_manual"] = True
    return result


# ------------------------------------------------------------------ 统一入口
def parse_document(path: str, filename: str = "", min_text_chars: int = 30,
                   ocr_min_confidence: float = 0.5, ocr_min_chars: int = 30,
                   strip_cfg=None):
    """统一解析入口：按格式路由到 DOCX / PDF / OCR，阈值由调用方（配置）传入。

    strip_cfg 形如 {"enabled": True, "min_pages": 2, "max_chars": 46, "edge_lines": 2}，
    由 config.review.strip_* 组装；为 None 表示不做页眉页脚剔除。
    """
    dt = detect_type(path, filename)
    if dt == DOCX:
        res = parse_docx(path, strip_cfg=strip_cfg)
        if res["char_count"] and res["char_count"] < max(1, int(min_text_chars)):
            res["ok"] = False
            res["need_manual"] = True
            res["error"] = ("DOCX 正文过少（%d 字符，阈值 %d），疑似内容缺失"
                            % (res["char_count"], int(min_text_chars)))
        return res
    if dt == PDF:
        return parse_pdf(path, min_chars=min_text_chars, strip_cfg=strip_cfg)
    if dt == IMAGE:
        from . import ocr_win
        return ocr_win.ocr_image(path, min_chars=ocr_min_chars,
                                 min_confidence=ocr_min_confidence)
    if dt == "doc_legacy":
        return {
            "ok": False, "doc_type": "doc_legacy", "engine": ENGINE_NONE,
            "paragraphs": [], "text": "", "page_count": 0, "char_count": 0,
            "error": "旧版 .doc 格式需先转换为 .docx 后重新上传",
            "need_manual": True, "ocr_confidence": 0.0,
        }
    return {
        "ok": False, "doc_type": UNKNOWN, "engine": ENGINE_NONE,
        "paragraphs": [], "text": "", "page_count": 0, "char_count": 0,
        "error": "不支持的文件格式：%s（支持 DOCX / PDF / PNG / JPG）" % (filename or path),
        "need_manual": True, "ocr_confidence": 0.0,
    }
