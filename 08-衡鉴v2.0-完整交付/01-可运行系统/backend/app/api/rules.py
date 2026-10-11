# -*- coding: utf-8 -*-
"""合规规则与模板接口（规则配置页 / 评测看板）"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from .. import config, db, schemas
from ..services import audit, rule_engine

router = APIRouter(prefix="/api", tags=["rules"])


@router.get("/rules")
def list_rules(clause_type: str = Query(""), level: str = Query("")):
    data = rule_engine.load_rules()
    rules = data.get("rules", [])
    if clause_type:
        rules = [r for r in rules if (r.get("clause_type") or "other") == clause_type]
    if level:
        rules = [r for r in rules if r["level"] == level]
    return {"version": data.get("version"), "updated_at": data.get("updated_at"),
            "total": len(rules), "items": rules, "stats": rule_engine.stats()}


@router.get("/rules/tree")
def rules_tree():
    return {"version": rule_engine.rule_version(), "tree": rule_engine.rule_tree(),
            "stats": rule_engine.stats()}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, req: schemas.RuleUpdateRequest):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    try:
        updated = rule_engine.save_rule(rule_id, patch)
    except rule_engine.RuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.log(None, "rule.update", tool="rule_console",
              input_value={"rule_id": rule_id, "patch": patch},
              output_value={"version": rule_engine.rule_version()})
    return {"ok": True, "rule": updated, "version": rule_engine.rule_version()}


@router.get("/templates")
def list_templates(clause_type: str = Query("")):
    tpls = (rule_engine.load_templates().get("templates") or [])
    if clause_type:
        tpls = [t for t in tpls if t.get("clause_type") == clause_type]
    return {"total": len(tpls), "items": tpls}


@router.get("/templates/{template_id}")
def get_template(template_id: str):
    tpls = (rule_engine.load_templates().get("templates") or [])
    hit = next((t for t in tpls if t.get("id") == template_id), None)
    if not hit:
        raise HTTPException(status_code=404, detail=f"示范条款不存在：{template_id}")
    # 反向引用：哪些规则在使用这套模板 —— 修改模板前必须先看清影响面
    users = [r["id"] for r in rule_engine.load_rules().get("rules", [])
             if r.get("advice_ref") == template_id]
    return {"template": hit, "used_by_rules": users}


@router.get("/meta/clause-types")
def clause_types():
    return {"items": [{"key": k, "label": v} for k, v in config.CLAUSE_LABEL.items()]}


@router.get("/system/audit")
def system_audit(limit: int = Query(200, ge=1, le=2000), step: str = Query("")):
    """全局审计流水。用于"从审查意见反查处理链路"以及运维排障。"""
    sql = "SELECT * FROM audit_logs"
    args: list = []
    if step:
        sql += " WHERE step LIKE ?"
        args.append(f"%{step}%")
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.tx() as conn:
        return {"total": len(rows := db.rows(conn.execute(sql, tuple(args)))), "items": rows}


# ------------------------------------------------------------------ 评测看板
from ..eval import run_eval  # noqa: E402  （放在文件末尾避免应用启动时导入副作用）


@router.get("/eval/score")
def eval_score():
    """返回最近一次跑分结果；若从未跑过，返回提示而不伪造数据。"""
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {"has_result": False,
                "message": "尚未执行过评测。请点击「运行评测」或执行 "
                           "python -m app.eval.run_eval --persist",
                "rule_version": rule_engine.rule_version()}
    r = dict(row)
    try:
        detail = json.loads(r.get("detail_json") or "[]")
    except Exception:
        detail = []
    return {"has_result": True, "run": {
        "id": r["id"], "rule_version": r["rule_version"], "total_cases": r["total_cases"],
        "parse_ok": r["parse_ok"], "high_total": r["high_total"], "high_hit": r["high_hit"],
        "high_recall": r["high_recall"], "false_positive": r["false_positive"],
        "precision": r["precision"], "avg_ms": r["avg_ms"], "verdict": r["verdict"],
        "created_at": r["created_at"],
    }, "cases": detail}


@router.post("/eval/run")
def eval_run(req: schemas.EvalRunRequest):
    try:
        return run_eval.run(limit=req.cases or None, persist=req.persist)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"评测执行失败：{exc}") from exc


@router.get("/stats/overview")
def stats_overview():
    from ..services import pipeline
    return pipeline.overview()


@router.get("/system/health")
def health():
    from ..services import llm, parser_ocr
    return {
        "status": "ok",
        "project": config.PROJECT_CODE,
        "version": config.PROJECT_VERSION,
        "role": config.ROLE,
        "demo_mode": config.DEMO_MODE,
        "db": str(config.DB_PATH),
        "llm": {"enabled": config.LLM_ENABLED, "configured": llm.is_configured(),
                "model": config.LLM_MODEL, "base_url": config.LLM_BASE_URL,
                "timeout": config.LLM_TIMEOUT},
        "ocr": {"enabled": config.OCR_ENABLED, "available": parser_ocr.available(),
                "min_confidence": config.OCR_MIN_CONFIDENCE},
        "rules": rule_engine.stats(),
        "writeback_dry_run_default": config.WRITEBACK_DRY_RUN,
    }
