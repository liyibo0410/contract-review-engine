# -*- coding: utf-8 -*-
"""SQLite 持久层 + 任务状态机（零第三方依赖）。

表结构对齐架构图「工程支撑与交付」一节：
    contract_cases / contract_documents / review_results / audit_logs
另加 writeback_logs 承载回写状态流转（not_written/writing/success/failed）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

# ---------------------------------------------------------------- 状态枚举
S_PENDING = "pending"        # 待处理
S_PARSING = "parsing"        # 解析中
S_REVIEWING = "reviewing"    # 审查中
S_BLOCKED = "blocked"        # 解析受阻
S_COMPLETED = "completed"    # 审查完成
STATUS_LABEL = {
    S_PENDING: "待处理",
    S_PARSING: "解析中",
    S_REVIEWING: "审查中",
    S_BLOCKED: "解析受阻",
    S_COMPLETED: "审查完成",
}

WB_NOT_WRITTEN = "not_written"
WB_WRITING = "writing"
WB_SUCCESS = "success"
WB_FAILED = "failed"
WB_LABEL = {
    WB_NOT_WRITTEN: "未回写",
    WB_WRITING: "回写中",
    WB_SUCCESS: "回写成功",
    WB_FAILED: "回写失败",
}

R_HIGH = "high"
R_MEDIUM = "medium"
R_LOW = "low"
R_NONE = "none"
RISK_LABEL = {R_HIGH: "高风险", R_MEDIUM: "中风险", R_LOW: "低风险", R_NONE: "无风险"}
RISK_WEIGHT = {R_NONE: 0, R_LOW: 1, R_MEDIUM: 2, R_HIGH: 3}

# 解析引擎标识
ENGINE_DOCX = "docx-xml"
ENGINE_PDF = "pdf-text"
ENGINE_OCR = "ocr-win"
ENGINE_NONE = "-"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contract_cases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no             TEXT    NOT NULL UNIQUE,
    title               TEXT    NOT NULL,
    applicant           TEXT,
    send_dept           TEXT,
    business_type       TEXT,              -- purchase / sales / service / labor
    counterparty        TEXT,
    credit_code         TEXT,
    amount              REAL,
    currency            TEXT DEFAULT 'CNY',
    perform_period      TEXT,
    effective_condition TEXT,
    source              TEXT DEFAULT 'local',   -- approval(审批拉取) / local(本地上传)
    approval_instance   TEXT,
    document_id         INTEGER,
    doc_sha256          TEXT,
    review_status       TEXT DEFAULT 'pending',
    writeback_status    TEXT DEFAULT 'not_written',
    risk_level          TEXT DEFAULT 'none',
    conclusion          TEXT,              -- pass / rectify / reject
    blocked_reason      TEXT,
    lawyer_opinion      TEXT,
    created_at          TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS contract_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       INTEGER NOT NULL,
    filename      TEXT    NOT NULL,
    doc_type      TEXT,                    -- docx / pdf / image
    parse_engine  TEXT,
    parse_status  TEXT DEFAULT 'pending',  -- ok / blocked
    parse_error   TEXT,
    page_count    INTEGER DEFAULT 0,
    para_count    INTEGER DEFAULT 0,
    char_count    INTEGER DEFAULT 0,
    ocr_confidence REAL DEFAULT 0,
    need_manual   INTEGER DEFAULT 0,
    sha256        TEXT,
    file_path     TEXT,
    text_path     TEXT,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS review_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      INTEGER NOT NULL,
    rule_id      TEXT,
    rule_name    TEXT,
    risk_level   TEXT,
    clause_type  TEXT,
    match_mode   TEXT,
    evidence     TEXT,
    page_no      INTEGER,
    para_index   INTEGER,
    char_start   INTEGER,
    char_end     INTEGER,
    reason       TEXT,
    legal_basis  TEXT,
    suggestion   TEXT,
    suggestion_final TEXT,
    adopted      INTEGER DEFAULT 0,
    lawyer_note  TEXT,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER,
    step       TEXT,
    tool       TEXT,
    status     TEXT,
    detail     TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS writeback_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL,
    mode        TEXT,                      -- dry_run / live
    channel     TEXT,                      -- feishu_mock
    status      TEXT,
    payload     TEXT,
    comment_id  TEXT,
    error       TEXT,
    created_at  TEXT
);

-- 索引：覆盖列表筛选、幂等去重、详情聚合三类高频查询
CREATE INDEX IF NOT EXISTS idx_results_case    ON review_results(case_id, risk_level);
CREATE INDEX IF NOT EXISTS idx_audit_case      ON audit_logs(case_id, id);
CREATE INDEX IF NOT EXISTS idx_docs_case       ON contract_documents(case_id);
CREATE INDEX IF NOT EXISTS idx_wb_case         ON writeback_logs(case_id, id);
-- approval_instance 唯一性由应用层（pull_from_approval）保证，这里用普通索引，
-- 避免历史数据存在重复值时导致建库直接失败
CREATE INDEX IF NOT EXISTS idx_cases_appr ON contract_cases(approval_instance);
CREATE INDEX IF NOT EXISTS idx_cases_sha       ON contract_cases(doc_sha256);
CREATE INDEX IF NOT EXISTS idx_cases_status    ON contract_cases(review_status, risk_level);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# 可更新字段白名单（与建表语句保持一致；白名单同时起到防注入与防误用的作用）
_CASE_FIELDS = {
    "title", "applicant", "send_dept", "business_type", "counterparty",
    "credit_code", "amount", "currency", "perform_period", "effective_condition",
    "source", "approval_instance", "document_id", "doc_sha256",
    "review_status", "writeback_status", "risk_level", "conclusion",
    "blocked_reason", "lawyer_opinion",
}
_RESULT_FIELDS = {
    "rule_name", "risk_level", "clause_type", "match_mode", "evidence",
    "page_no", "para_index", "char_start", "char_end", "reason",
    "legal_basis", "suggestion", "suggestion_final", "adopted", "lawyer_note",
}


def new_case_no(prefix: str = "HT") -> str:
    return "%s-%s-%s" % (prefix, time.strftime("%Y%m%d"), uuid.uuid4().hex[:6].upper())


class Store:
    """线程安全的轻量持久层。单连接 + 互斥锁，足够支撑演示与单机部署。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL + NORMAL：单机多线程下读写不互相阻塞，显著降低页面卡顿；
            # busy_timeout 避免并发写入直接抛 database is locked。
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.execute("PRAGMA foreign_keys=ON")
            except sqlite3.Error:
                pass
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ 基础
    def _q(self, sql: str, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def _q1(self, sql: str, args=()):
        r = self._q(sql, args)
        return r[0] if r else None

    def _x(self, sql: str, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid

    def audit(self, case_id, step: str, tool: str, status: str, detail: str = ""):
        self._x(
            "INSERT INTO audit_logs(case_id,step,tool,status,detail,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (case_id, step, tool, status, detail, _now()),
        )

    # ------------------------------------------------------------ 合同案件
    def create_case(self, **kw) -> int:
        fields = dict(
            case_no=kw.get("case_no") or new_case_no(),
            title=kw.get("title") or "未命名合同",
            applicant=kw.get("applicant") or "李奕波",
            send_dept=kw.get("send_dept") or "采购部",
            business_type=kw.get("business_type") or "purchase",
            counterparty=kw.get("counterparty") or "",
            credit_code=kw.get("credit_code") or "",
            amount=kw.get("amount"),
            currency=kw.get("currency") or "CNY",
            perform_period=kw.get("perform_period") or "",
            effective_condition=kw.get("effective_condition") or "",
            source=kw.get("source") or "local",
            approval_instance=kw.get("approval_instance") or "",
            doc_sha256=kw.get("doc_sha256") or "",
            review_status=kw.get("review_status") or S_PENDING,
            writeback_status=kw.get("writeback_status") or WB_NOT_WRITTEN,
            risk_level=kw.get("risk_level") or R_NONE,
            conclusion=kw.get("conclusion") or "",
            blocked_reason=kw.get("blocked_reason") or "",
            created_at=_now(),
            updated_at=_now(),
        )
        cols = ",".join(fields.keys())
        marks = ",".join("?" * len(fields))
        cid = self._x(
            "INSERT INTO contract_cases (%s) VALUES (%s)" % (cols, marks),
            tuple(fields.values()),
        )
        self.audit(cid, "create_case", "store.create_case", "ok",
                   "来源=%s 单据号=%s" % (fields["source"], fields["case_no"]))
        return cid

    def update_case(self, case_id: int, **kw):
        """按字段白名单更新案件，防止误传字段导致 SQL 拼接异常。"""
        safe = {k: v for k, v in kw.items() if k in _CASE_FIELDS}
        if not safe:
            if kw:
                self.audit(case_id, "store", "store.update_case", "skipped",
                           "忽略非法字段：%s" % "、".join(sorted(kw)))
            return
        safe["updated_at"] = _now()
        sets = ",".join("%s=?" % k for k in safe)
        self._x("UPDATE contract_cases SET %s WHERE id=?" % sets,
                tuple(safe.values()) + (case_id,))

    def get_case(self, case_id: int):
        return self._q1("SELECT * FROM contract_cases WHERE id=?", (case_id,))

    def list_cases(self, risk=None, status=None, btype=None, kw=None):
        sql = "SELECT * FROM contract_cases WHERE 1=1"
        args = []
        if risk:
            sql += " AND risk_level=?"
            args.append(risk)
        if status:
            sql += " AND review_status=?"
            args.append(status)
        if btype:
            sql += " AND business_type=?"
            args.append(btype)
        if kw:
            sql += " AND (title LIKE ? OR counterparty LIKE ? OR case_no LIKE ?)"
            args += ["%%%s%%" % kw] * 3
        sql += " ORDER BY id DESC"
        return self._q(sql, args)

    def case_by_sha(self, sha: str):
        return self._q1("SELECT * FROM contract_cases WHERE doc_sha256=?", (sha,))

    def case_by_approval(self, instance: str):
        return self._q1(
            "SELECT * FROM contract_cases WHERE approval_instance=?", (instance,))

    def stats(self):
        total = self._q1("SELECT COUNT(*) c FROM contract_cases")["c"]
        by_status = {r["review_status"]: r["c"] for r in self._q(
            "SELECT review_status, COUNT(*) c FROM contract_cases GROUP BY review_status")}
        by_risk = {r["risk_level"]: r["c"] for r in self._q(
            "SELECT risk_level, COUNT(*) c FROM contract_cases GROUP BY risk_level")}
        wb = {r["writeback_status"]: r["c"] for r in self._q(
            "SELECT writeback_status, COUNT(*) c FROM contract_cases GROUP BY writeback_status")}
        return {"total": total, "by_status": by_status, "by_risk": by_risk,
                "by_writeback": wb}

    # ------------------------------------------------------------ 文档
    def add_document(self, case_id: int, **kw) -> int:
        fields = dict(
            case_id=case_id,
            filename=kw.get("filename") or "contract",
            doc_type=kw.get("doc_type") or "unknown",
            parse_engine=kw.get("parse_engine") or ENGINE_NONE,
            parse_status=kw.get("parse_status") or "pending",
            parse_error=kw.get("parse_error") or "",
            page_count=kw.get("page_count") or 0,
            para_count=kw.get("para_count") or 0,
            char_count=kw.get("char_count") or 0,
            ocr_confidence=kw.get("ocr_confidence") or 0,
            need_manual=1 if kw.get("need_manual") else 0,
            sha256=kw.get("sha256") or "",
            file_path=kw.get("file_path") or "",
            text_path=kw.get("text_path") or "",
            created_at=_now(),
        )
        cols = ",".join(fields.keys())
        marks = ",".join("?" * len(fields))
        did = self._x("INSERT INTO contract_documents (%s) VALUES (%s)" % (cols, marks),
                      tuple(fields.values()))
        self.update_case(case_id, document_id=did)
        return did

    def get_document(self, doc_id: int):
        return self._q1("SELECT * FROM contract_documents WHERE id=?", (doc_id,))

    def case_document(self, case_id: int):
        return self._q1(
            "SELECT * FROM contract_documents WHERE case_id=? ORDER BY id DESC LIMIT 1",
            (case_id,))

    # ------------------------------------------------------------ 审查结果
    def clear_results(self, case_id: int):
        self._x("DELETE FROM review_results WHERE case_id=?", (case_id,))

    def add_results(self, case_id: int, items: list):
        for it in items:
            self._x(
                "INSERT INTO review_results(case_id,rule_id,rule_name,risk_level,clause_type,"
                "match_mode,evidence,page_no,para_index,char_start,char_end,reason,legal_basis,"
                "suggestion,adopted,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                (case_id, it.get("rule_id"), it.get("rule_name"), it.get("risk_level"),
                 it.get("clause_type"), it.get("match_mode"), it.get("evidence"),
                 it.get("page_no"), it.get("para_index"), it.get("char_start"),
                 it.get("char_end"), it.get("reason"), it.get("legal_basis"),
                 it.get("suggestion"), _now()),
            )

    def results(self, case_id: int):
        rows = self._q(
            "SELECT * FROM review_results WHERE case_id=? ORDER BY "
            "CASE risk_level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 "
            "ELSE 3 END, id", (case_id,))
        return rows

    def get_result(self, rid: int):
        return self._q1("SELECT * FROM review_results WHERE id=?", (rid,))

    def update_result(self, rid: int, **kw):
        """按字段白名单更新风险项（法务复核）。"""
        safe = {k: v for k, v in kw.items() if k in _RESULT_FIELDS}
        if not safe:
            return
        sets = ",".join("%s=?" % k for k in safe)
        self._x("UPDATE review_results SET %s WHERE id=?" % sets,
                tuple(safe.values()) + (rid,))

    def overall_risk(self, case_id: int) -> str:
        rows = self.results(case_id)
        best = R_NONE
        for r in rows:
            if RISK_WEIGHT.get(r["risk_level"], 0) > RISK_WEIGHT[best]:
                best = r["risk_level"]
        return best

    # ------------------------------------------------------------ 审计 / 回写
    def audit_list(self, case_id=None, limit=200):
        if case_id:
            return self._q("SELECT * FROM audit_logs WHERE case_id=? ORDER BY id DESC LIMIT ?",
                           (case_id, limit))
        return self._q("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))

    def add_writeback(self, case_id: int, mode: str, channel: str, status: str,
                      payload: str, comment_id: str = "", error: str = "") -> int:
        return self._x(
            "INSERT INTO writeback_logs(case_id,mode,channel,status,payload,comment_id,error,"
            "created_at) VALUES(?,?,?,?,?,?,?,?)",
            (case_id, mode, channel, status, payload, comment_id, error, _now()),
        )

    def writeback_list(self, case_id: int):
        return self._q(
            "SELECT * FROM writeback_logs WHERE case_id=? ORDER BY id DESC", (case_id,))

    def export_all(self):
        return {
            "cases": self._q("SELECT * FROM contract_cases ORDER BY id"),
            "documents": self._q("SELECT * FROM contract_documents ORDER BY id"),
            "results": self._q("SELECT * FROM review_results ORDER BY id"),
            "audit": self._q("SELECT * FROM audit_logs ORDER BY id"),
            "writeback": self._q("SELECT * FROM writeback_logs ORDER BY id"),
        }

    def dumps_export(self) -> str:
        return json.dumps(self.export_all(), ensure_ascii=False, indent=2)
