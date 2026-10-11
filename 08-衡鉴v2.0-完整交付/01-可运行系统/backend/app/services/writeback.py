# -*- coding: utf-8 -*-
"""审批回写模块（含 DRY_RUN 预览与幂等控制）

安全边界（不可突破）：
    · 系统只有"写评论"的能力，**没有任何**通过 / 拒绝 / 转交 / 撤回的接口；
    · 回写前一律先做 DRY_RUN 检查：先生成待写入正文，再决定是否真正提交；
    · 幂等三键 = 合同编号 + 附件 SHA256 + 规则版本号。同一份文件、同一版规则，
      无论触发多少次，评论只会出现一条 —— 重复触发直接返回既有结果，不重复解析、
      不重复审查、不重复写评论。

真实审批系统接入时只需替换 `_submit_to_platform`，其余状态机与幂等逻辑完全复用。
"""
from __future__ import annotations

import json
from datetime import datetime

from .. import config, db
from . import audit

_TABLE = "writebacks"


class WritebackError(Exception):
    pass


def idem_key(code: str, sha256: str, rule_version: str) -> str:
    return f"{code}|{sha256}|{rule_version}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get(contract_id: int) -> dict | None:
    with db.tx() as conn:
        cur = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,),
        )
        rows = db.rows(cur)
    return rows[0] if rows else None


def history(contract_id: int) -> list[dict]:
    with db.tx() as conn:
        cur = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE contract_id=? ORDER BY id DESC", (contract_id,))
        return db.rows(cur)


def _upsert(conn, contract_id: int, **fields) -> int:
    exist = conn.execute(
        f"SELECT id FROM {_TABLE} WHERE contract_id=? ORDER BY id DESC LIMIT 1",
        (contract_id,),
    ).fetchone()
    if exist:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE {_TABLE} SET {sets} WHERE id=?",
                     (*fields.values(), exist["id"]))
        return int(exist["id"])
    fields = {"contract_id": contract_id, "created_at": _now(), **fields}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO {_TABLE}({cols}) VALUES({marks})", tuple(fields.values()))
    return int(cur.lastrowid)


# ------------------------------------------------------------------ 预览
def preview(contract_id: int, body_md: str, *, operator: str = "",
            dry_run: bool | None = None, idem: str = "") -> dict:
    """生成待写入正文并落库，状态停在 not_written。不触发任何对外动作。"""
    dry = config.WRITEBACK_DRY_RUN if dry_run is None else bool(dry_run)
    with db.tx() as conn:
        wid = _upsert(conn, contract_id,
                      status="not_written",
                      dry_run=1 if dry else 0,
                      idem_key=idem,
                      payload_md=body_md,
                      platform_comment_id="",
                      error="",
                      operator=operator,
                      updated_at=_now())
    audit.log(contract_id, "writeback.preview", tool="writeback",
              output_value={"writeback_id": wid, "dry_run": dry, "chars": len(body_md)})
    return {"writeback_id": wid, "dry_run": dry, "chars": len(body_md), "status": "not_written"}


# ------------------------------------------------------------------ 提交
def _submit_to_platform(contract_id: int, body_md: str, operator: str) -> str:
    """内置审批系统模拟器：把评论写进 platform_comments 表。

    接入真实系统时，把这个函数替换为对审批平台评论接口的调用即可，
    返回真实评论 ID；失败请抛 WritebackError，状态机会自动落到 failed。
    """
    if config.APPROVAL_SIM_BASE:
        raise WritebackError(
            "已配置外部审批系统地址但未实现对接适配器，请实现 _submit_to_platform 后重试")
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO platform_comments(contract_id, writeback_id, author, body_md, kind, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (contract_id, None, f"{config.PROJECT_CODE}审查助手", body_md,
             "review_opinion", _now()),
        )
        return f"CMT-{cur.lastrowid:06d}"


def write(contract_id: int, body_md: str, *, operator: str = "",
          dry_run: bool | None = None, idem: str = "", force: bool = False) -> dict:
    """执行回写。返回状态字典。

    幂等：若相同 idem_key 已经 success，直接返回既有结果（除非 force=True）。
    """
    dry = config.WRITEBACK_DRY_RUN if dry_run is None else bool(dry_run)
    current = get(contract_id)

    if not force and current and current.get("idem_key") == idem \
            and current.get("status") == "success" and current.get("idem_key"):
        audit.log(contract_id, "writeback.skip", tool="writeback", level="warn",
                  detail=f"幂等命中，跳过重复回写（idem={idem}）")
        return {"status": "success", "skipped": True,
                "platform_comment_id": current.get("platform_comment_id", ""),
                "writeback_id": current["id"], "message": "相同文件与规则版本已回写成功，本次跳过"}

    with audit.Step(contract_id, "writeback.write", tool="writeback",
                    input_value={"dry_run": dry, "idem": idem}):
        attempt = (current or {}).get("attempt", 0) + 1
        with db.tx() as conn:
            wid = _upsert(conn, contract_id, status="writing", dry_run=1 if dry else 0,
                          idem_key=idem, attempt=attempt, payload_md=body_md,
                          operator=operator, error="", updated_at=_now())

        if dry:
            with db.tx() as conn:
                _upsert(conn, contract_id, status="not_written", dry_run=1,
                        idem_key=idem, attempt=attempt, payload_md=body_md,
                        operator=operator, updated_at=_now())
            return {"status": "not_written", "dry_run": True, "writeback_id": wid,
                    "platform_comment_id": "",
                    "message": "预览模式（DRY_RUN）：意见已生成并暂存，未写入审批系统"}

        try:
            comment_id = _submit_to_platform(contract_id, body_md, operator)
        except Exception as exc:  # 任何下游异常都落到 failed，并保留错误与重试入口
            with db.tx() as conn:
                _upsert(conn, contract_id, status="failed", dry_run=0, idem_key=idem,
                        attempt=attempt, payload_md=body_md, operator=operator,
                        error=str(exc)[:500], updated_at=_now())
            audit.log(contract_id, "writeback.failed", tool="writeback", level="error",
                      output_value=str(exc))
            return {"status": "failed", "dry_run": False, "writeback_id": wid,
                    "platform_comment_id": "", "error": str(exc)[:300],
                    "message": "回写失败，可点击重试"}

        with db.tx() as conn:
            _upsert(conn, contract_id, status="success", dry_run=0, idem_key=idem,
                    attempt=attempt, payload_md=body_md, operator=operator,
                    platform_comment_id=comment_id, error="", updated_at=_now())
            conn.execute(
                "UPDATE platform_comments SET writeback_id=? WHERE contract_id=?"
                " AND id=(SELECT MAX(id) FROM platform_comments WHERE contract_id=?)",
                (wid, contract_id, contract_id),
            )
        return {"status": "success", "dry_run": False, "writeback_id": wid,
                "platform_comment_id": comment_id, "message": "已写回审批系统评论区"}


def retry(contract_id: int, body_md: str | None = None, *, operator: str = "") -> dict:
    current = get(contract_id)
    if not current:
        raise WritebackError("尚无回写记录，请先生成审查意见")
    if current["status"] == "success":
        return {"status": "success", "message": "已回写成功，无需重试",
                "platform_comment_id": current.get("platform_comment_id", ""),
                "writeback_id": current["id"]}
    body = body_md or current.get("payload_md") or ""
    if not body:
        raise WritebackError("缺少待写入正文，无法重试")
    return write(contract_id, body, operator=operator, dry_run=bool(current.get("dry_run")),
                 idem=current.get("idem_key", ""), force=True)


def platform_comments(contract_id: int) -> list[dict]:
    with db.tx() as conn:
        cur = conn.execute(
            "SELECT * FROM platform_comments WHERE contract_id=? ORDER BY id ASC",
            (contract_id,),
        )
        return db.rows(cur)


def serialize(wb: dict | None) -> dict:
    if not wb:
        return {"status": "not_written", "dry_run": True, "platform_comment_id": "",
                "error": "", "attempt": 0, "idem_key": "", "updated_at": ""}
    return {
        "status": wb.get("status", "not_written"),
        "dry_run": bool(wb.get("dry_run")),
        "platform_comment_id": wb.get("platform_comment_id", ""),
        "error": wb.get("error", ""),
        "attempt": wb.get("attempt", 0),
        "idem_key": wb.get("idem_key", ""),
        "updated_at": wb.get("updated_at", ""),
        "payload_chars": len(wb.get("payload_md") or ""),
    }


def dump(wb: dict | None) -> str:
    return json.dumps(serialize(wb), ensure_ascii=False)
