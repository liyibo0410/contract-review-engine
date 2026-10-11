# -*- coding: utf-8 -*-
"""PDF 解析引擎

两条路径：
1. 文本型 PDF —— PyMuPDF 直接抽取 block，按 (页, y, x) 排序，页码天然真实；
2. 图片型 PDF（扫描件转 PDF）—— 页文本为空时回退渲染成位图交 OCR 引擎，
   并把 OCR 行框坐标折算回"页内阅读顺序"，与文本型共用同一套坐标系。

加密 PDF：needs_pass 且未提供密码时抛 PdfEncrypted，由协调器转 blocked。
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


class PdfEncrypted(Exception):
    """PDF 需要密码。"""


def _sort_key(block):
    x0, y0, x1, y1 = block[:4]
    return (round(y0, 1), round(x0, 1))


def _page_ocr_blocks(page, page_no: int, start_idx: int) -> list[dict]:
    """图片型 PDF 单页走 OCR；OCR 不可用时返回空列表（上层据此判定空文/阻塞）。"""
    try:
        from . import parser_ocr
    except Exception:
        return []
    pix = page.get_pixmap(dpi=200)
    try:
        blocks, conf = parser_ocr.recognize_bytes(pix.tobytes("png"))
    except Exception:
        return []
    out = []
    for i, b in enumerate(blocks):
        b = dict(b)
        b["page"] = page_no
        b["para_index"] = start_idx + i
        b["confidence"] = conf
        out.append(b)
    return out


def parse(path: str | Path, password: str | None = None, enable_ocr: bool = True) -> dict:
    path = Path(path)
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise PdfEncrypted("PDF 无法打开：" + str(exc)) from exc

    try:
        if doc.needs_pass:
            if not password:
                raise PdfEncrypted("文档已加密，需提供打开密码")
            if not doc.authenticate(password):
                raise PdfEncrypted("文档密码错误")

        blocks: list[dict] = []
        idx = 0
        ocr_pages = 0
        engine = "PyMuPDF"

        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            raw = page.get_text("blocks") or []
            raw = [b for b in raw if len(b) >= 5 and str(b[4]).strip()]
            raw.sort(key=_sort_key)

            if raw:
                for b in raw:
                    text = " ".join(str(b[4]).split())
                    if not text:
                        continue
                    kind = "table" if text.count("｜") >= 2 or text.count("|") >= 3 else "text"
                    blocks.append({
                        "page": pno + 1,
                        "para_index": idx,
                        "text": text,
                        "kind": kind,
                        "confidence": 1.0,
                    })
                    idx += 1
            elif enable_ocr:
                got = _page_ocr_blocks(page, pno + 1, idx)
                if got:
                    blocks.extend(got)
                    idx += len(got)
                    ocr_pages += 1

        if ocr_pages:
            engine = "PyMuPDF+RapidOCR"
        return {
            "blocks": blocks,
            "page_count": doc.page_count,
            "engine": engine,
            "encrypted": False,
            "ocr_pages": ocr_pages,
            "has_real_pagination": True,
        }
    finally:
        doc.close()


def is_encrypted(path: str | Path) -> bool:
    try:
        doc = fitz.open(str(path))
        try:
            return bool(doc.needs_pass)
        finally:
            doc.close()
    except Exception:
        return True
