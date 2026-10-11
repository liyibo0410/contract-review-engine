# -*- coding: utf-8 -*-
"""解析器与协调器测试：验证三格式统一坐标系与阻塞判定。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.services import coordinator, parser_docx, parser_ocr, parser_pdf

SAMPLES = Path(config.SAMPLES_DIR)


def _sample(pattern: str) -> Path:
    hits = sorted(SAMPLES.glob(pattern))
    if not hits:
        pytest.skip(f"样例不存在：{pattern}（请先运行 python tools/gen_samples.py）")
    return hits[0]


# ------------------------------------------------------------------ 坐标体系
def test_normalize_produces_contiguous_offsets():
    blocks = [{"page": 1, "para_index": 0, "text": "第一段", "kind": "text"},
              {"page": 1, "para_index": 1, "text": "第二段", "kind": "text"}]
    nb, full = coordinator.normalize_blocks(blocks)
    assert full == "第一段\n第二段"
    assert nb[0]["char_start"] == 0 and nb[0]["char_end"] == 3
    assert nb[1]["char_start"] == 4
    # 用坐标切片能取回原文 —— 这是"证据可溯源"的最小可用性证明
    assert full[nb[1]["char_start"]:nb[1]["char_end"]] == "第二段"


def test_locate_finds_substring():
    full = "本合同自签署之日起生效。"
    s, e = coordinator.locate(full, "签署之日起生效")
    assert full[s:e] == "签署之日起生效"
    assert coordinator.locate(full, "不存在的文字") == (-1, -1)


def test_coord_of_maps_offset_to_page_and_paragraph():
    blocks = [{"page": 1, "para_index": 0, "text": "甲", "kind": "text"},
              {"page": 2, "para_index": 1, "text": "乙", "kind": "text"}]
    nb, full = coordinator.normalize_blocks(blocks)
    assert coordinator.coord_of(full, nb, 0) == (1, 0)
    assert coordinator.coord_of(full, nb, nb[1]["char_start"]) == (2, 1)


# ------------------------------------------------------------------ 条款切分
def test_classify_propagates_heading_context_to_body():
    blocks = coordinator.normalize_blocks([
        {"page": 1, "para_index": 0, "text": "第六条　违约责任", "kind": "heading"},
        {"page": 1, "para_index": 1, "text": "任一方若未按约定履行义务，应向对方支付相应款项。",
         "kind": "text"},
    ])[0]
    classified = coordinator.classify_blocks(blocks)
    assert classified[0]["clause_type"] == "liability"
    assert classified[1]["clause_type"] == "liability", "标题语义应向下传播"


def test_role_words_do_not_hijack_clause_type():
    """甲方/乙方这类角色词不应把"知识产权段落"判成"签约主体"段落。

    这是实测踩过的坑：角色词在合同里无处不在，一旦计入打分，
    会把整篇条款类型带偏，连带风险命中全部失效。
    """
    own, _hits = coordinator._score_clause_type(
        "本合同项下乙方为甲方定制开发形成的全部交付成果，其知识产权归乙方所有")
    assert own in ("ip", "delivery"), own
    assert own != "subject", "角色词不得把段落判成签约主体"
    # 明确的知识产权表述必须判成 ip
    own2, _ = coordinator._score_clause_type("本合同项下软件源代码与设计文档的知识产权归属见附件。")
    assert own2 == "ip"


def test_build_clauses_merges_same_type():
    blocks = coordinator.normalize_blocks([
        {"page": 1, "para_index": 0, "text": "第四条　知识产权", "kind": "heading"},
        {"page": 1, "para_index": 1, "text": "成果归甲方所有。", "kind": "text"},
    ])[0]
    clauses = coordinator.build_clauses(coordinator.classify_blocks(blocks))
    assert len(clauses) == 1
    assert "成果归甲方所有" in clauses[0]["text"]


# ------------------------------------------------------------------ 元数据
def test_extract_metadata_returns_coordinates():
    text = ("设备采购合同\n"
            "合同编号：HJ-CG-2026-0001\n"
            "甲方：洛阳衡远制造有限公司\n"
            "乙方：江苏恒力机械装备有限公司\n"
            "统一社会信用代码：91410300MA45H2X31K\n"
            "合同金额为人民币 2,400,000.00 元。\n"
            "本合同自 2026年10月1日 起生效。")
    blocks = coordinator.normalize_blocks(
        [{"page": 1, "para_index": i, "text": ln, "kind": "text"}
         for i, ln in enumerate(text.split("\n"))])[0]
    meta = coordinator.extract_metadata(text, blocks)
    keys = {i["key"] for i in meta["items"]}
    assert "uscc" in keys and "amount" in keys
    for item in meta["items"]:
        if item["char_start"] >= 0:
            assert text[item["char_start"]:item["char_end"]] == item["value"], \
                f"{item['key']} 的坐标无法取回原值"
    assert meta["amount_value"] == 2400000.0


# ------------------------------------------------------------------ 阻塞判定
def test_decide_block_on_empty_content():
    blocked, code, reason = coordinator.decide_block({}, "太短", None, None)
    assert blocked and code == "EMPTY_CONTENT" and reason


def test_decide_block_on_encrypted():
    blocked, code, _ = coordinator.decide_block({"encrypted": True}, "x" * 500, None, None)
    assert blocked and code == "DOC_ENCRYPTED"


def test_decide_block_distinguishes_ocr_missing_from_blur():
    blocked, code, _ = coordinator.decide_block({}, "", None, "引擎未安装")
    assert blocked and code == "OCR_ENGINE_UNAVAILABLE"
    blocked2, code2, _ = coordinator.decide_block({}, "", 0.10, None)
    assert blocked2 and code2 == "OCR_LOW_CONFIDENCE"


def test_clean_document_is_not_blocked():
    blocked, code, _ = coordinator.decide_block({}, "正文" * 200, 0.98, None)
    assert not blocked and code == ""


# ------------------------------------------------------------------ 真实文件
def test_parse_docx_sample():
    p = _sample("*埋雷版.docx")
    res = parser_docx.parse(p)
    assert res["blocks"] and res["page_count"] >= 1
    assert res["engine"] == "python-docx"


def test_parse_pdf_sample():
    p = _sample("*标准版.pdf")
    res = parser_pdf.parse(p)
    assert res["blocks"], "文本型 PDF 应抽取到内容"
    assert res["page_count"] >= 1


def test_encrypted_pdf_raises():
    p = _sample("*加密件.pdf")
    with pytest.raises(parser_pdf.PdfEncrypted):
        parser_pdf.parse(p)


def test_scan_sample_via_ocr():
    p = _sample("*扫描件.png")
    if not parser_ocr.available():
        pytest.skip("OCR 引擎未安装")
    blocks, conf = parser_ocr.recognize_file(p)
    assert blocks, "扫描件应识别出文本行"
    assert 0 <= conf <= 1


def test_detect_file_type():
    p = _sample("*扫描件.png")
    assert coordinator.detect_file_type(p.name, str(p)) == "image"
    p2 = _sample("*标准版.pdf")
    assert coordinator.detect_file_type(p2.name, str(p2)) == "pdf"
