# -*- coding: utf-8 -*-
"""审计留痕

每一个链路步骤都要留下一行记录 —— 这是"从审查意见反查到完整处理链路"的实现方式。
digest 用 SHA256 前 16 位即可：目的是"能比对、能定位"，不是"防篡改密码学证明"。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any

from .. import db


def digest(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def log(contract_id: int | None, step: str, *, tool: str = "", level: str = "info",
        input_value: Any = None, output_value: Any = None, cost_ms: int = 0,
        detail: str = "") -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO audit_logs(contract_id, step, tool, level, input_digest,"
            " output_digest, cost_ms, detail, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (contract_id, step, tool, level, digest(input_value), digest(output_value),
             int(cost_ms), detail[:2000], datetime.now().isoformat(timespec="seconds")),
        )


class Step:
    """上下文管理器：自动记录耗时与成败。"""

    def __init__(self, contract_id: int | None, step: str, *, tool: str = "",
                 input_value: Any = None):
        self.contract_id = contract_id
        self.step = step
        self.tool = tool
        self.input_value = input_value
        self.started = 0.0

    def __enter__(self):
        self.started = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        cost = int((time.time() - self.started) * 1000)
        if exc_type is None:
            log(self.contract_id, self.step, tool=self.tool, level="info",
                input_value=self.input_value, output_value="ok", cost_ms=cost)
        else:
            log(self.contract_id, self.step, tool=self.tool, level="error",
                input_value=self.input_value, output_value=str(exc), cost_ms=cost,
                detail=f"{exc_type.__name__}: {exc}")
        return False


def list_logs(contract_id: int, limit: int = 200) -> list[dict]:
    with db.tx() as conn:
        cur = conn.execute(
            "SELECT * FROM audit_logs WHERE contract_id=? ORDER BY id ASC LIMIT ?",
            (contract_id, limit),
        )
        return db.rows(cur)
