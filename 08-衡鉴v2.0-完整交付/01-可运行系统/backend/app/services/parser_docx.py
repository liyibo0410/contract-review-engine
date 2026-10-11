# -*- coding: utf-8 -*-
"""DOCX 解析引擎

产出：有序文本块（Block）序列，每块带 page / para_index / confidence。
页码来源：优先使用 Word 落版标记（w:lastRenderedPageBreak）与显式分页符（w:br type="page"）；
若无任何分页信息（例如纯程序生成的文档），退化为 page=1 并如实标注，
此时定位能力仍由 para_index + 字符区间保证 —— 不做"假装知道页码"的估算。
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_PAGE_BREAK = re.compile(r'<w:br[^>]*w:type="page"', re.IGNORECASE)
_LAST_RENDERED = re.compile(r"<w:lastRenderedPageBreak", re.IGNORECASE)


class DocxEncrypted(Exception):
    """文档被加密 / 无法打开。"""


def _iter_body(doc: Document):
    """按文档流顺序产出段落与表格（python-docx 的 doc.paragraphs 不含表格位置）。"""
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == NS_W + "p":
            yield Paragraph(child, doc)
        elif tag == NS_W + "tbl":
            yield Table(child, doc)


def _has_page_break(el) -> bool:
    try:
        xml = el.xml
    except Exception:
        return False
    return bool(_PAGE_BREAK.search(xml)) or bool(_LAST_RENDERED.search(xml))


def _table_text(table: Table) -> str:
    lines = []
    for row in table.rows:
        cells = [c.text.strip().replace("\n", " ") for c in row.cells]
        if any(cells):
            lines.append(" ｜ ".join(cells))
    return "\n".join(lines)


def parse(path: str | Path) -> dict:
    """解析 docx，返回 {blocks, page_count, engine, encrypted}。

    blocks: [{"page", "para_index", "text", "kind", "confidence"}]
    """
    path = Path(path)
    try:
        doc = Document(str(path))
    except Exception as exc:  # 加密、损坏、非 OOXML
        msg = str(exc)
        if "encrypted" in msg.lower() or "password" in msg.lower():
            raise DocxEncrypted(msg) from exc
        raise DocxEncrypted("DOCX 无法打开：" + msg) from exc

    blocks: list[dict] = []
    page = 1
    idx = 0
    for item in _iter_body(doc):
        if isinstance(item, Table):
            text = _table_text(item)
            kind = "table"
            brk = False
        else:
            text = item.text or ""
            brk = _has_page_break(item._p)
            style = (item.style.name or "").lower() if item.style is not None else ""
            kind = "heading" if ("heading" in style or "标题" in style) else "text"

        text = text.replace("\u00a0", " ").rstrip()
        if text.strip():
            blocks.append({
                "page": page,
                "para_index": idx,
                "text": text,
                "kind": kind,
                "confidence": 1.0,
            })
            idx += 1
        if brk:
            page += 1

    return {
        "blocks": blocks,
        "page_count": max(page, 1),
        "engine": "python-docx",
        "encrypted": False,
        "has_real_pagination": page > 1,
    }


def count_paragraphs(path: str | Path) -> int:
    try:
        return len(parse(path)["blocks"])
    except Exception:
        return 0
