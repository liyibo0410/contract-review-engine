# -*- coding: utf-8 -*-
"""报告导出（Markdown / PDF）

PDF 生成走 PyMuPDF 内置中文字体（china-s），不引入 reportlab / wkhtmltopdf 之类外部依赖 ——
"零外部服务依赖"这条红线在导出环节同样成立：拷到任何一台装了 Python 的机器上都能出报告。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import config

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_HEAD = re.compile(r"^(#{1,6})\s*(.+)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
_RULE = re.compile(r"^\s*[-*_]{3,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def _plain(text: str) -> str:
    text = _BOLD.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    return text.rstrip()


def _safe_name(contract: dict) -> str:
    raw = f"{contract.get('code', 'HJ')}_{contract.get('name', '合同')}"
    return re.sub(r'[\\/:*?"<>|\s]+', "_", raw)[:80]


# ------------------------------------------------------------------ Markdown
def export_markdown(contract: dict, md: str) -> Path:
    path = config.REPORT_DIR / f"{_safe_name(contract)}_审查意见.md"
    path.write_text(md, encoding="utf-8")
    return path


# ------------------------------------------------------------------ PDF
def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for ch in text:
        line += ch
        if len(line) >= width:
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return out or [""]


def export_pdf(contract: dict, md: str) -> Path:
    import fitz

    path = config.REPORT_DIR / f"{_safe_name(contract)}_审查意见.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)          # A4
    left, top, right, bottom = 48, 54, 547, 800
    x = left
    y = top
    width_chars = 46

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=595, height=842)
        y = top

    def put(text: str, size: float = 9.5, bold: bool = False, advance: float | None = None):
        nonlocal y
        lh = advance or size * 1.65
        for seg in _wrap(text, int(width_chars * (9.5 / size)) if size else width_chars):
            if y + lh > bottom:
                new_page()
            page.insert_text((x, y), seg, fontsize=size, fontname="china-s",
                             color=(0, 0, 0))
            y += lh

    lines = md.split("\n")
    for raw in lines:
        line = raw.rstrip()
        if _RULE.match(line):
            if y + 10 > bottom:
                new_page()
            page.draw_line((left, y), (right, y), color=(0.6, 0.6, 0.6), width=0.5)
            y += 12
            continue
        if not line.strip():
            y += 6
            continue
        m = _HEAD.match(line)
        if m:
            level = len(m.group(1))
            size = {1: 16, 2: 13, 3: 11.5}.get(level, 10.5)
            y += 4
            put(_plain(m.group(2)), size=size, bold=True, advance=size * 1.9)
            y += 2
            continue
        if _TABLE_SEP.match(line):
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            put("  ｜  ".join(x for x in cells if x), size=9.0)
            continue
        q = _QUOTE.match(line)
        if q:
            put("    " + _plain(q.group(1)), size=9.0)
            continue
        b = _BULLET.match(line)
        if b:
            indent = min(len(b.group(1)), 6)
            put(" " * indent + "· " + _plain(b.group(2)), size=9.5)
            continue
        put(_plain(line))

    doc.save(str(path), deflate=True)
    doc.close()
    return path


def export_both(contract: dict, md: str) -> dict:
    md_path = export_markdown(contract, md)
    result = {"markdown": str(md_path), "pdf": "", "generated_at": datetime.now().isoformat(timespec="seconds")}
    try:
        pdf_path = export_pdf(contract, md)
        result["pdf"] = str(pdf_path)
    except Exception as exc:
        result["pdf_error"] = str(exc)
    return result
