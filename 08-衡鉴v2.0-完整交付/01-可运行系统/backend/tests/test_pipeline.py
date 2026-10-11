# -*- coding: utf-8 -*-
"""状态机 / 幂等 / 回写 端到端测试（走真实业务层，使用 conftest.py 指定的临时数据库）"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config, db  # noqa: E402
from app.services import pipeline, rule_engine, writeback  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _fresh_db():
    """每个测试模块开始前清空业务表，避免模块间用例互相污染。"""
    db.init_db()
    db.reset_db()
    yield
    db.reset_db()


def _sample(pattern: str) -> Path:
    hits = sorted(Path(config.SAMPLES_DIR).glob(pattern))
    if not hits:
        pytest.skip("样例缺失，请先运行 python tools/gen_samples.py")
    return hits[0]


# ------------------------------------------------------------------ 任务与幂等
def test_create_task_and_idempotency():
    p = _sample("*埋雷版.docx")
    a = pipeline.create_task(file_path=p, file_name=p.name, applicant="王倩",
                             dept="信息技术部", biz_type="采购")
    assert a["reused"] is False
    same_path = Path(config.SAMPLES_DIR) / p.name
    b = pipeline.create_task(file_path=same_path, file_name=p.name)
    assert b["reused"] is True and b["contract_id"] == a["contract_id"], \
        "相同 SHA256 应复用既有任务（幂等三键之一）"


def test_pipeline_reaches_completed_with_findings():
    p = _sample("*埋雷版.docx")
    created = pipeline.create_task(file_path=p, file_name=p.name, biz_type="采购",
                                   force_new=True)
    cid = created["contract_id"]
    res = pipeline.run_pipeline(cid)
    assert res["status"] == "completed", res
    assert res["stats"]["high"] >= 5, "埋雷合同应命中至少 5 条高风险"
    levels = {f["level"] for f in res["findings"]}
    assert "high" in levels
    for f in res["findings"]:
        assert f["char_start"] >= 0 and f["char_end"] > f["char_start"]
        assert f["evidence_text"] and f["legal_basis"]


def test_encrypted_document_goes_blocked_then_retry_still_blocked():
    p = _sample("*加密件.pdf")
    created = pipeline.create_task(file_path=p, file_name=p.name, biz_type="采购",
                                   force_new=True)
    cid = created["contract_id"]
    res = pipeline.run_pipeline(cid)
    assert res["status"] == "blocked"
    with db.tx() as conn:
        row = conn.execute("SELECT status, block_code, block_reason FROM contracts"
                           " WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "blocked"
    assert row["block_code"] == "DOC_ENCRYPTED"
    assert row["block_reason"], "阻塞原因必须可读，否则重试没有意义"

    # 重试：文件没换，仍然阻塞，但状态机可正常流转且不产生脏数据
    res2 = pipeline.retry(cid)
    assert res2["status"] == "blocked"
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM clauses WHERE contract_id=?",
                         (cid,)).fetchone()["n"]
    assert n == 0, "阻塞任务不应遗留条款数据"


def test_clean_contract_has_no_high_findings():
    p = _sample("*设备采购合同_标准版.docx")
    created = pipeline.create_task(file_path=p, file_name=p.name, biz_type="采购",
                                   force_new=True)
    res = pipeline.run_pipeline(created["contract_id"])
    assert res["status"] == "completed"
    assert res["stats"]["high"] == 0, "标准合同不应命中高风险（误报控制）"


def test_llm_degradation_produces_complete_output():
    """未配置大模型时，摘要/建议/结论必须完整 —— 演示零事故的底线。"""
    p = _sample("*埋雷版.docx")
    created = pipeline.create_task(file_path=p, file_name=p.name, biz_type="采购",
                                   force_new=True)
    res = pipeline.run_pipeline(created["contract_id"])
    summary = res["summary"]
    assert summary["generated_by"] == "template"
    assert summary["risk_summary"]
    assert summary["conclusion"] in ("建议通过", "建议整改后通过", "建议拒绝")
    assert all(f["advice"] for f in res["findings"]), "每条风险都必须有示范条款"


# ------------------------------------------------------------------ 回写
def test_writeback_dry_run_does_not_write_comment():
    cid = _reviewed_contract()
    body = "**【衡鉴】测试意见**\n\n高风险 1 条。"
    preview = writeback.preview(cid, body, operator="tester", dry_run=True, idem="k1")
    assert preview["status"] == "not_written"
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM platform_comments WHERE contract_id=?",
                         (cid,)).fetchone()["n"]
    assert n == 0, "预览模式不得写入审批系统"


def test_writeback_success_then_idempotent_skip():
    cid = _reviewed_contract()
    body = "**【衡鉴】测试意见**\n\n高风险 1 条。"
    r1 = writeback.write(cid, body, operator="tester", dry_run=False, idem="key-A")
    assert r1["status"] == "success" and r1["platform_comment_id"].startswith("CMT-")
    r2 = writeback.write(cid, body, operator="tester", dry_run=False, idem="key-A")
    assert r2.get("skipped") is True, "相同幂等键应跳过重复回写"
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM platform_comments WHERE contract_id=?",
                         (cid,)).fetchone()["n"]
    assert n == 1, "审批评论区只应出现一条意见"


def test_idem_key_composition():
    k = writeback.idem_key("HJ-1", "abc", "1.0.0")
    assert k == "HJ-1|abc|1.0.0"
    assert writeback.idem_key("HJ-1", "abc", "1.0.1") != k, "规则版本变化应产生新的幂等键"


def test_rule_version_is_readable():
    assert rule_engine.rule_version() not in ("", "unknown")


# ------------------------------------------------------------------ 辅助
_CACHE = {}


def _reviewed_contract() -> int:
    if "cid" in _CACHE:
        return _CACHE["cid"]
    p = _sample("*埋雷版.docx")
    created = pipeline.create_task(file_path=p, file_name=p.name, biz_type="采购",
                                   force_new=True)
    cid = created["contract_id"]
    pipeline.run_pipeline(cid)
    _CACHE["cid"] = cid
    return cid
