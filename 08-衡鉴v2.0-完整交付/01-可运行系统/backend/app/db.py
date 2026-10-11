# -*- coding: utf-8 -*-
"""衡鉴 · 数据层

SQLite + WAL。零外部服务依赖是本项目的一条红线（见《项目环境要求》），
所以所有持久化都落在单文件数据库里，演示现场不需要额外起 MySQL/Redis。

对外只暴露三个东西：
    get_conn()   —— 线程安全的连接（每次调用取一条新连接，WAL 下并发安全）
    init_db()    —— 幂等建表 + 索引 + 迁移
    tx()         —— 事务上下文管理器
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from . import config

_lock = threading.Lock()
_initialised = False

SCHEMA = """
-- ① 合同/任务主表 ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS contracts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT    NOT NULL,                  -- 合同编号（自动生成 HJ-YYYYMMDD-xxxx）
    name          TEXT    NOT NULL,
    applicant     TEXT    NOT NULL DEFAULT '',       -- 申请人（业务经办人）
    dept          TEXT    NOT NULL DEFAULT '',       -- 送审部门
    biz_type      TEXT    NOT NULL DEFAULT 'other',  -- 采购/销售/服务/劳动/其他
    counterparty  TEXT    NOT NULL DEFAULT '',       -- 相对方主体名称
    uscc          TEXT    NOT NULL DEFAULT '',       -- 相对方统一社会信用代码
    amount        REAL,                              -- 合同金额（元）
    currency      TEXT    NOT NULL DEFAULT 'CNY',
    sign_date     TEXT,
    perform_start TEXT,
    perform_end   TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending',-- pending/parsing/reviewing/blocked/completed
    risk_level    TEXT    NOT NULL DEFAULT 'none',   -- high/medium/low/none
    risk_summary  TEXT    NOT NULL DEFAULT '',
    conclusion    TEXT    NOT NULL DEFAULT '',       -- 通过/整改/建议拒绝
    block_reason  TEXT    NOT NULL DEFAULT '',
    block_code    TEXT    NOT NULL DEFAULT '',
    idem_key      TEXT    NOT NULL DEFAULT '',       -- 幂等三键：code + sha256 + rule_version
    source        TEXT    NOT NULL DEFAULT 'upload', -- upload / approval_pull / demo
    meta_json     TEXT    NOT NULL DEFAULT '{}',     -- 元数据抽取结果（含坐标）
    profile_json  TEXT    NOT NULL DEFAULT '{}',     -- 合同画像六维评分
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contracts_idem ON contracts(idem_key) WHERE idem_key <> '';
CREATE INDEX IF NOT EXISTS ix_contracts_status ON contracts(status, risk_level);

-- ② 文档与解析状态 ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    file_name     TEXT    NOT NULL,
    file_type     TEXT    NOT NULL,                  -- docx / pdf / scan_pdf / image
    sha256        TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    stored_path   TEXT    NOT NULL DEFAULT '',
    parse_engine  TEXT    NOT NULL DEFAULT '',
    parse_status  TEXT    NOT NULL DEFAULT 'pending',
    page_count    INTEGER NOT NULL DEFAULT 0,
    char_count    INTEGER NOT NULL DEFAULT 0,
    block_reason  TEXT    NOT NULL DEFAULT '',
    ocr_confidence REAL,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_contract ON documents(contract_id);
CREATE INDEX IF NOT EXISTS ix_documents_sha ON documents(sha256);

-- ③ 条款结构树（锚定基础）--------------------------------------------------
CREATE TABLE IF NOT EXISTS clauses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    contract_id   INTEGER NOT NULL,
    clause_type   TEXT    NOT NULL DEFAULT 'other',
    heading       TEXT    NOT NULL DEFAULT '',
    page          INTEGER NOT NULL DEFAULT 1,
    para_index    INTEGER NOT NULL DEFAULT 0,
    char_start    INTEGER NOT NULL,
    char_end      INTEGER NOT NULL,
    text          TEXT    NOT NULL,
    confidence    REAL,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clauses_contract ON clauses(contract_id, clause_type);
CREATE INDEX IF NOT EXISTS ix_clauses_range ON clauses(document_id, char_start);

-- ④ 风险命中（证据链核心）--------------------------------------------------
CREATE TABLE IF NOT EXISTS findings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    clause_id     INTEGER REFERENCES clauses(id) ON DELETE SET NULL,
    rule_id       TEXT    NOT NULL,
    rule_version  TEXT    NOT NULL DEFAULT '',
    title         TEXT    NOT NULL,
    level         TEXT    NOT NULL DEFAULT 'low',
    clause_type   TEXT    NOT NULL DEFAULT 'other',
    judge_mode    TEXT    NOT NULL DEFAULT 'rule',   -- rule / llm
    evidence_text TEXT    NOT NULL DEFAULT '',
    page          INTEGER NOT NULL DEFAULT 1,
    para_index    INTEGER NOT NULL DEFAULT 0,
    char_start    INTEGER NOT NULL DEFAULT 0,
    char_end      INTEGER NOT NULL DEFAULT 0,
    reason        TEXT    NOT NULL DEFAULT '',
    legal_basis   TEXT    NOT NULL DEFAULT '',
    advice        TEXT    NOT NULL DEFAULT '',
    advice_source TEXT    NOT NULL DEFAULT 'template',-- template / llm
    adopted       INTEGER NOT NULL DEFAULT 0,
    adopted_text  TEXT    NOT NULL DEFAULT '',
    confidence    REAL,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_contract ON findings(contract_id, level);

-- ⑤ 审查意见 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    risk_summary  TEXT    NOT NULL DEFAULT '',
    conclusion    TEXT    NOT NULL DEFAULT '',
    focus_points  TEXT    NOT NULL DEFAULT '[]',
    high_cnt      INTEGER NOT NULL DEFAULT 0,
    medium_cnt    INTEGER NOT NULL DEFAULT 0,
    low_cnt       INTEGER NOT NULL DEFAULT 0,
    reviewer_note TEXT    NOT NULL DEFAULT '',
    generated_by  TEXT    NOT NULL DEFAULT 'template',
    rule_version  TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reviews_contract ON reviews(contract_id);

-- ⑥ 回写状态机 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS writebacks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    status        TEXT    NOT NULL DEFAULT 'not_written',
    dry_run       INTEGER NOT NULL DEFAULT 1,
    idem_key      TEXT    NOT NULL DEFAULT '',
    attempt       INTEGER NOT NULL DEFAULT 0,
    payload_md    TEXT    NOT NULL DEFAULT '',
    platform_comment_id TEXT NOT NULL DEFAULT '',
    error         TEXT    NOT NULL DEFAULT '',
    operator      TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_writebacks_contract ON writebacks(contract_id, status);

-- ⑦ 审计日志（全链路留痕）---------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER,
    step          TEXT    NOT NULL,
    tool          TEXT    NOT NULL DEFAULT '',
    level         TEXT    NOT NULL DEFAULT 'info',
    input_digest  TEXT    NOT NULL DEFAULT '',
    output_digest TEXT    NOT NULL DEFAULT '',
    cost_ms       INTEGER NOT NULL DEFAULT 0,
    detail        TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_contract ON audit_logs(contract_id, id);

-- ⑧ 评测跑分 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eval_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version   TEXT    NOT NULL DEFAULT '',
    total_cases    INTEGER NOT NULL DEFAULT 0,
    parse_ok       INTEGER NOT NULL DEFAULT 0,
    high_total     INTEGER NOT NULL DEFAULT 0,
    high_hit       INTEGER NOT NULL DEFAULT 0,
    high_recall    REAL    NOT NULL DEFAULT 0,
    false_positive INTEGER NOT NULL DEFAULT 0,
    precision      REAL    NOT NULL DEFAULT 0,
    avg_ms         INTEGER NOT NULL DEFAULT 0,
    verdict        TEXT    NOT NULL DEFAULT 'pass',
    detail_json    TEXT    NOT NULL DEFAULT '[]',
    created_at     TEXT    NOT NULL
);

-- ⑨ 审批评论区（内置模拟器，替代真实审批系统）-------------------------------
CREATE TABLE IF NOT EXISTS platform_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    writeback_id INTEGER,
    author       TEXT    NOT NULL DEFAULT '衡鉴审查助手',
    body_md      TEXT    NOT NULL,
    kind         TEXT    NOT NULL DEFAULT 'review_opinion',
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_comments_contract ON platform_comments(contract_id, id);

-- ⑩ 人工审核（HITL）动作流水 -------------------------------------------------
-- 设计要点：不落"状态字段"而落"动作流水"，当前状态由最后一条动作推出。
-- 这样做的好处是：谁在什么时候同意/修改/驳回了什么，天然可追溯，
-- 不需要额外的状态迁移审计（审计信息就是业务数据本身）。
CREATE TABLE IF NOT EXISTS hitl_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    action       TEXT    NOT NULL,                   -- approve / edit / reject / resume
    actor        TEXT    NOT NULL DEFAULT '',
    note         TEXT    NOT NULL DEFAULT '',
    interrupt_at TEXT    NOT NULL DEFAULT '',        -- 触发中断的环节
    payload_json TEXT    NOT NULL DEFAULT '{}',      -- 编辑内容（改等级/改建议/改批注）
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hitl_contract ON hitl_actions(contract_id, id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def get_conn() -> sqlite3.Connection:
    return _connect()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    global _initialised
    with _lock:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _initialised = True


def reset_db() -> None:
    """仅用于演示/测试：清空全部业务表（保留表结构）。"""
    tables = [
        "platform_comments", "hitl_actions", "writebacks", "reviews", "findings",
        "clauses", "documents", "audit_logs", "eval_runs", "contracts",
    ]
    with tx() as conn:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence")


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
