# -*- coding: utf-8 -*-
"""合同审查相关接口（大盘 / 工作台 / 回写 / 报告）"""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from queue import Empty

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import config, db, schemas
from ..services import (audit, coordinator, parser_docx, parser_ocr, parser_pdf,
                        pipeline, report, rule_engine, writeback, writer)

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


# ------------------------------------------------------------------ 工具
def _task_brief(row: dict) -> dict:
    return {
        "id": row["id"], "code": row["code"], "name": row["name"],
        "applicant": row.get("applicant", ""), "dept": row.get("dept", ""),
        "biz_type": row.get("biz_type", ""), "counterparty": row.get("counterparty", ""),
        "amount": row.get("amount"), "currency": row.get("currency", "CNY"),
        "status": row.get("status", ""), "risk_level": row.get("risk_level", "none"),
        "conclusion": row.get("conclusion", ""), "block_reason": row.get("block_reason", ""),
        "file_type": row.get("file_type", ""),
        "high_cnt": row.get("high_cnt", 0), "medium_cnt": row.get("medium_cnt", 0),
        "low_cnt": row.get("low_cnt", 0),
        "created_at": row.get("created_at", ""), "updated_at": row.get("updated_at", ""),
    }


def _require(contract_id: int) -> dict:
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"合同不存在：{contract_id}")
    return dict(row)


def _document(contract_id: int) -> dict | None:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
    return dict(row) if row else None


def _findings(contract_id: int) -> list[dict]:
    with db.tx() as conn:
        return db.rows(conn.execute(
            "SELECT * FROM findings WHERE contract_id=?"
            " ORDER BY CASE level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
            " page, char_start", (contract_id,)))


def _review(contract_id: int) -> dict | None:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM reviews WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
    return dict(row) if row else None


def _finding_out(f: dict) -> dict:
    return {
        "id": f["id"], "rule_id": f["rule_id"], "title": f["title"], "level": f["level"],
        "level_label": config.RISK_LABEL.get(f["level"], f["level"]),
        "clause_type": f["clause_type"],
        "clause_label": config.CLAUSE_LABEL.get(f["clause_type"], f["clause_type"]),
        "judge_mode": f.get("judge_mode", "rule"), "evidence_text": f["evidence_text"],
        "page": f["page"], "para_index": f["para_index"], "char_start": f["char_start"],
        "char_end": f["char_end"], "reason": f["reason"], "legal_basis": f["legal_basis"],
        "advice": f["advice"], "advice_source": f["advice_source"],
        "adopted": bool(f["adopted"]), "adopted_text": f["adopted_text"],
    }


def _review_out(r: dict | None) -> dict | None:
    if not r:
        return None
    try:
        focus = json.loads(r.get("focus_points") or "[]")
    except Exception:
        focus = []
    return {
        "risk_summary": r.get("risk_summary", ""), "conclusion": r.get("conclusion", ""),
        "focus_points": focus, "high_cnt": r.get("high_cnt", 0),
        "medium_cnt": r.get("medium_cnt", 0), "low_cnt": r.get("low_cnt", 0),
        "reviewer_note": r.get("reviewer_note", ""),
        "generated_by": r.get("generated_by", "template"),
        "rule_version": r.get("rule_version", ""),
    }


# ------------------------------------------------------------------ 上传与拉取
@router.post("/upload", response_model=schemas.UploadResponse)
async def upload(file: UploadFile = File(...),
                 name: str = Form(""),
                 applicant: str = Form(""),
                 dept: str = Form(""),
                 biz_type: str = Form("other"),
                 auto_run: bool = Form(True)):
    """上传合同（DOCX / PDF / 扫描件）创建审查任务。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".docx", ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{suffix}")

    tmp = Path(config.UPLOAD_DIR) / f"_incoming_{datetime.now().strftime('%H%M%S%f')}{suffix}"
    size = 0
    with tmp.open("wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                f.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(status_code=413,
                                    detail=f"文件超过 {config.MAX_UPLOAD_MB}MB 上限")
            f.write(chunk)

    try:
        created = pipeline.create_task(file_path=tmp, file_name=file.filename,
                                      applicant=applicant, dept=dept, biz_type=biz_type,
                                      name=name or None, source="upload")
    finally:
        tmp.unlink(missing_ok=True)

    if auto_run and not created.get("reused"):
        pipeline.run_pipeline_async(created["contract_id"])
    return {"contract_id": created["contract_id"], "code": created["code"],
            "reused": created.get("reused", False), "sha256": created.get("sha256", ""),
            "auto_run": bool(auto_run and not created.get("reused")),
            "message": "任务已创建，正在后台解析与审查" if not created.get("reused")
                       else "检测到相同附件，已复用既有审查任务"}


@router.post("/pull")
def pull_from_approval(req: schemas.PullRequest):
    """从审批系统拉取待办任务（内置模拟器：按样例合同顺序生成待审任务）。"""
    samples = sorted(Path(config.SAMPLES_DIR).glob("*"))
    samples = [p for p in samples if p.suffix.lower() in
               {".docx", ".pdf", ".png", ".jpg", ".jpeg"}]
    if not samples:
        raise HTTPException(status_code=400,
                            detail="样例目录为空，请先执行 python tools/gen_samples.py 生成样例合同")
    created = []
    for p in samples[:req.count]:
        c = pipeline.create_task(file_path=p, file_name=p.name, applicant="王倩",
                                 dept="采购中心", biz_type="other", source="approval_pull")
        created.append(c)
        if req.auto_run and not c.get("reused"):
            pipeline.run_pipeline_async(c["contract_id"])
    return {"count": len(created), "items": created, "auto_run": req.auto_run}


# ------------------------------------------------------------------ 列表与详情
@router.get("")
def list_contracts(status: str = Query("", description="按状态筛选"),
                   risk: str = Query("", description="按风险等级筛选"),
                   biz_type: str = Query(""),
                   keyword: str = Query(""),
                   limit: int = Query(100, ge=1, le=500)):
    sql = ("SELECT c.*, "
           " COALESCE((SELECT COUNT(*) FROM findings f WHERE f.contract_id=c.id AND f.level='high'),0) AS high_cnt,"
           " COALESCE((SELECT COUNT(*) FROM findings f WHERE f.contract_id=c.id AND f.level='medium'),0) AS medium_cnt,"
           " COALESCE((SELECT COUNT(*) FROM findings f WHERE f.contract_id=c.id AND f.level='low'),0) AS low_cnt,"
           " COALESCE((SELECT d.file_type FROM documents d WHERE d.contract_id=c.id ORDER BY d.id DESC LIMIT 1),'') AS file_type"
           " FROM contracts c WHERE 1=1")
    args: list = []
    if status:
        sql += " AND c.status=?"
        args.append(status)
    if risk:
        sql += " AND c.risk_level=?"
        args.append(risk)
    if biz_type:
        sql += " AND c.biz_type=?"
        args.append(biz_type)
    if keyword:
        sql += " AND (c.name LIKE ? OR c.code LIKE ? OR c.counterparty LIKE ?)"
        args += [f"%{keyword}%"] * 3
    sql += " ORDER BY CASE c.risk_level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, c.id DESC LIMIT ?"
    args.append(limit)
    with db.tx() as conn:
        rows = db.rows(conn.execute(sql, tuple(args)))
    return {"total": len(rows), "items": [_task_brief(r) for r in rows]}


@router.get("/{contract_id}")
def get_contract(contract_id: int):
    c = _require(contract_id)
    doc = _document(contract_id)
    try:
        meta = json.loads(c.get("meta_json") or "{}")
    except Exception:
        meta = {}
    try:
        profile = json.loads(c.get("profile_json") or "{}")
    except Exception:
        profile = {}
    with db.tx() as conn:
        clauses = db.rows(conn.execute(
            "SELECT * FROM clauses WHERE contract_id=? ORDER BY char_start", (contract_id,)))
        wb = conn.execute(
            "SELECT * FROM writebacks WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
    brief = _task_brief(c)
    fs = _findings(contract_id)
    brief["high_cnt"] = sum(1 for f in fs if f["level"] == "high")
    brief["medium_cnt"] = sum(1 for f in fs if f["level"] == "medium")
    brief["low_cnt"] = sum(1 for f in fs if f["level"] == "low")
    return {
        "task": brief,
        "document": ({
            "file_name": doc["file_name"], "file_type": doc["file_type"],
            "parse_engine": doc["parse_engine"], "parse_status": doc["parse_status"],
            "page_count": doc["page_count"], "char_count": doc["char_count"],
            "sha256": doc["sha256"], "ocr_confidence": doc["ocr_confidence"],
            "block_reason": doc["block_reason"],
        } if doc else None),
        "meta": meta.get("items", []),
        "profile": profile,
        "review": _review_out(_review(contract_id)),
        "writeback": writeback.serialize(dict(wb) if wb else None),
        "clauses": [{
            "id": cl["id"], "clause_type": cl["clause_type"],
            "clause_label": config.CLAUSE_LABEL.get(cl["clause_type"], cl["clause_type"]),
            "heading": cl["heading"], "page": cl["page"], "para_index": cl["para_index"],
            "char_start": cl["char_start"], "char_end": cl["char_end"], "text": cl["text"],
        } for cl in clauses],
    }


@router.get("/{contract_id}/document")
def get_document(contract_id: int):
    """返回带锚点的段落序列，供前端"文档视窗"渲染与高亮。

    用后端渲染的纯文本段落 + 字符区间，而不是 pdf.js 文本层 ——
    因为我们需要跨 docx / pdf / OCR 三种来源使用同一套高亮逻辑。
    """
    c = _require(contract_id)
    doc = _document(contract_id)
    if not doc:
        raise HTTPException(status_code=404, detail="缺少文档记录")
    with db.tx() as conn:
        clauses = db.rows(conn.execute(
            "SELECT clause_type, char_start, char_end, page FROM clauses"
            " WHERE contract_id=? ORDER BY char_start", (contract_id,)))

    path = doc.get("stored_path") or ""
    blocks: list[dict] = []
    file_type = doc.get("file_type", "")
    try:
        if file_type == "docx":
            raw = parser_docx.parse(path)["blocks"]
        elif file_type in ("pdf", "scan_pdf"):
            raw = parser_pdf.parse(path, enable_ocr=config.OCR_ENABLED)["blocks"]
        elif file_type == "image":
            raw = parser_ocr.recognize_file(path)[0]
        else:
            raw = []
        blocks = coordinator.normalize_blocks(raw)[0]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"文档重渲染失败：{exc}") from exc

    out = []
    for b in blocks:
        ctype = "other"
        for cl in clauses:
            if cl["char_start"] <= b["char_start"] < cl["char_end"]:
                ctype = cl["clause_type"]
                break
        out.append({
            "page": b["page"], "para_index": b["para_index"],
            "char_start": b["char_start"], "char_end": b["char_end"],
            "text": b["text"], "clause_type": ctype,
            "clause_label": config.CLAUSE_LABEL.get(ctype, ctype),
            "is_heading": b.get("kind") == "heading",
        })
    return {"contract_id": contract_id, "file_name": doc["file_name"],
            "file_type": file_type, "page_count": doc["page_count"],
            "paragraphs": out}


# ------------------------------------------------------------------ 风险与建议
@router.get("/{contract_id}/findings")
def get_findings(contract_id: int, level: str = Query("")):
    _require(contract_id)
    items = [_finding_out(f) for f in _findings(contract_id)]
    if level:
        items = [i for i in items if i["level"] == level]
    return {"total": len(items), "items": items,
            "by_level": {lv: sum(1 for i in items if i["level"] == lv)
                         for lv in ("high", "medium", "low")}}


@router.post("/{contract_id}/findings/{finding_id}/adopt")
def adopt_finding(contract_id: int, finding_id: int, req: schemas.AdoptRequest):
    _require(contract_id)
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM findings WHERE id=? AND contract_id=?",
                           (finding_id, contract_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="风险项不存在")
        text = req.adopted_text.strip() or row["advice"]
        conn.execute("UPDATE findings SET adopted=?, adopted_text=? WHERE id=?",
                     (1 if req.adopted else 0, text if req.adopted else "", finding_id))
    audit.log(contract_id, "finding.adopt", tool="workbench",
              input_value={"finding_id": finding_id, "adopted": req.adopted},
              output_value=text[:200])
    return {"ok": True, "finding_id": finding_id, "adopted": req.adopted,
            "adopted_text": text if req.adopted else ""}


@router.post("/{contract_id}/annotations")
def add_annotation(contract_id: int, req: schemas.AnnotationRequest):
    c = _require(contract_id)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT id FROM reviews WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
        if row:
            conn.execute("UPDATE reviews SET reviewer_note=? WHERE id=?",
                         (req.note, row["id"]))
        else:
            conn.execute(
                "INSERT INTO reviews(contract_id, reviewer_note, generated_by, created_at)"
                " VALUES(?,?,?,?)",
                (contract_id, req.note, "manual", datetime.now().isoformat(timespec="seconds")))
    audit.log(contract_id, "review.annotation", tool="workbench",
              input_value={"reviewer": req.reviewer}, output_value=req.note[:200])
    return {"ok": True, "note": req.note}


@router.get("/{contract_id}/review")
def get_review(contract_id: int):
    _require(contract_id)
    return {"review": _review_out(_review(contract_id)),
            "writeback": writeback.serialize(writeback.get(contract_id))}


@router.get("/{contract_id}/report")
def get_report(contract_id: int, download: bool = Query(False)):
    """生成审查意见报告；download=true 时返回 PDF 文件，否则返回 Markdown 文本。"""
    c = _require(contract_id)
    md = _build_markdown(c)
    if download:
        try:
            pdf_path = report.export_pdf(c, md)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"PDF 生成失败：{exc}") from exc
        return FileResponse(str(pdf_path), media_type="application/pdf",
                            filename=Path(pdf_path).name)
    return {"markdown": md, "chars": len(md)}


def _build_markdown(c: dict) -> str:
    try:
        meta = json.loads(c.get("meta_json") or "{}")
    except Exception:
        meta = {}
    with db.tx() as conn:
        clauses = db.rows(conn.execute(
            "SELECT * FROM clauses WHERE contract_id=? ORDER BY char_start", (c["id"],)))
    findings = [_finding_out(f) for f in _findings(c["id"])]
    r = _review_out(_review(c["id"])) or {}
    try:
        profile = json.loads(c.get("profile_json") or "{}")
    except Exception:
        profile = {}
    return writer.render_markdown(c, meta, clauses, findings,
                                  {"risk_summary": r.get("risk_summary", ""),
                                   "conclusion": r.get("conclusion", ""),
                                   "focus_points": r.get("focus_points", []),
                                   "generated_by": r.get("generated_by", "template")},
                                  profile, reviewer_note=r.get("reviewer_note", ""),
                                  rule_version=r.get("rule_version", ""),
                                  writeback=writeback.serialize(writeback.get(c["id"])))


@router.get("/{contract_id}/audit")
def get_audit(contract_id: int, limit: int = Query(200, ge=1, le=1000)):
    _require(contract_id)
    return {"items": audit.list_logs(contract_id, limit)}


# ------------------------------------------------------------------ 回写
@router.post("/{contract_id}/writeback")
def do_writeback(contract_id: int, req: schemas.WritebackRequest):
    c = _require(contract_id)
    if c["status"] != "completed":
        raise HTTPException(status_code=409,
                            detail=f"任务尚未审查完成（当前状态 {c['status']}），不可回写")
    # HITL 门禁：被法务驳回的审查结果不得回写，必须先重新审查
    last = _last_hitl(contract_id)
    if last and last["action"] == "reject":
        raise HTTPException(status_code=409,
                            detail="审查结果已被驳回，请重新发起审查后再回写")
    if req.dry_run and req.reviewer_note:
        add_annotation(contract_id, schemas.AnnotationRequest(note=req.reviewer_note,
                                                              reviewer=req.operator))
    r = _review_out(_review(contract_id)) or {}
    doc = _document(contract_id)
    body = writer.render_writeback_comment(
        c, r, [_finding_out(f) for f in _findings(contract_id)],
        reviewer_note=req.reviewer_note or r.get("reviewer_note", ""),
        operator=req.operator or config.OWNER_NAME)
    idem = writeback.idem_key(c["code"], (doc or {}).get("sha256", ""),
                              rule_engine.rule_version())
    if req.dry_run:
        pv = writeback.preview(contract_id, body, operator=req.operator,
                               dry_run=True, idem=idem)
        return {"status": "not_written", "dry_run": True,
                "preview": body, **pv,
                "message": "预览模式（DRY_RUN）：意见已生成并暂存，未写入审批系统"}
    return writeback.write(contract_id, body, operator=req.operator, dry_run=False, idem=idem)


@router.get("/{contract_id}/writeback/preview")
def writeback_preview(contract_id: int):
    c = _require(contract_id)
    r = _review_out(_review(contract_id)) or {}
    body = writer.render_writeback_comment(
        c, r, [_finding_out(f) for f in _findings(contract_id)],
        reviewer_note=r.get("reviewer_note", ""))
    doc = _document(contract_id)
    return {"preview": body,
            "idem_key": writeback.idem_key(c["code"], (doc or {}).get("sha256", ""),
                                           rule_engine.rule_version()),
            "writeback": writeback.serialize(writeback.get(contract_id))}


@router.post("/{contract_id}/writeback/retry")
def writeback_retry(contract_id: int, operator: str = Query("")):
    _require(contract_id)
    try:
        return writeback.retry(contract_id, operator=operator)
    except writeback.WritebackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{contract_id}/comments")
def get_comments(contract_id: int):
    """审批系统评论区（内置模拟器）——演示"意见已写回审批单"的可见效果。"""
    _require(contract_id)
    return {"items": writeback.platform_comments(contract_id)}


# ------------------------------------------------------------------ 流程控制
@router.post("/{contract_id}/pipeline")
def run_pipeline(contract_id: int, wait: bool = Query(False)):
    _require(contract_id)
    if wait:
        return pipeline.run_pipeline(contract_id)
    pipeline.run_pipeline_async(contract_id)
    return {"ok": True, "message": "流水线已在后台启动"}


@router.post("/{contract_id}/retry")
def retry_task(contract_id: int, wait: bool = Query(True)):
    _require(contract_id)
    if wait:
        return pipeline.retry(contract_id)
    pipeline.run_pipeline_async(contract_id)
    return {"ok": True, "message": "重试已提交"}


@router.get("/{contract_id}/events")
def events(contract_id: int, from_seq: int = Query(0), idle_limit: int = Query(150)):
    """SSE 实时进度流。idle_limit*0.1s 内无新事件则结束连接（默认 15 秒）。"""
    def gen():
        seq = from_seq
        idle = 0
        while idle < idle_limit:
            evts = pipeline.bus.since(contract_id, seq)
            if evts:
                idle = 0
                for e in evts:
                    seq = e["seq"] + 1
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            else:
                idle += 1
                if idle % 50 == 0:
                    yield ": keep-alive\n\n"
            time.sleep(0.1)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.post("/{contract_id}/search")
def search_clauses(contract_id: int, req: schemas.SearchRequest):
    """自然语言条款检索（压轴彩蛋）：把口语化提问映射到条款类型与关键词。"""
    _require(contract_id)
    with db.tx() as conn:
        clauses = db.rows(conn.execute(
            "SELECT * FROM clauses WHERE contract_id=? ORDER BY char_start", (contract_id,)))
    intent_map = [
        (["付款", "支付", "钱", "款", "结算", "发票"], "payment"),
        (["验收", "交付", "交货", "工期", "进度"], "delivery"),
        (["违约", "赔偿", "责任", "罚款", "滞纳金"], "liability"),
        (["保密", "泄露", "机密"], "confidentiality"),
        (["知识产权", "著作权", "专利", "源代码", "商标"], "ip"),
        (["数据", "个人信息", "隐私"], "data_security"),
        (["争议", "仲裁", "诉讼", "管辖", "法院"], "dispute"),
        (["生效", "期限", "有效期", "终止"], "validity"),
        (["不可抗力", "疫情", "灾害"], "force_majeure"),
        (["主体", "资质", "营业执照", "信用代码"], "subject"),
    ]
    hits_types = {t for words, t in intent_map if any(w in req.query for w in words)}
    tokens = [t for t in re.split(r"[，,。？?\s]+", req.query) if len(t) >= 2]
    matched = []
    for cl in clauses:
        score = 0
        if cl["clause_type"] in hits_types:
            score += 5
        for tk in tokens:
            if tk in cl["text"]:
                score += 2
        if score:
            matched.append((score, cl))
    matched.sort(key=lambda x: (-x[0], x[1]["char_start"]))
    top = matched[:3]
    if not top:
        return {"query": req.query, "found": 0, "items": [],
                "answer": "未在合同中定位到与该问题直接相关的条款，建议人工确认。"}
    answer = (
        f"已在合同第 {top[0][1]['page']} 页定位到与「{req.query}」最相关的"
        f"{config.CLAUSE_LABEL.get(top[0][1]['clause_type'], '相关')}条款，"
        f"共匹配 {len(matched)} 处，以下为前 {len(top)} 处："
    )
    return {"query": req.query, "found": len(matched), "answer": answer,
            "items": [{
                "id": cl["id"], "clause_type": cl["clause_type"],
                "clause_label": config.CLAUSE_LABEL.get(cl["clause_type"], cl["clause_type"]),
                "page": cl["page"], "char_start": cl["char_start"], "char_end": cl["char_end"],
                "text": cl["text"][:400],
            } for _s, cl in top]}


@router.delete("/{contract_id}")
def delete_contract(contract_id: int):
    _require(contract_id)
    with db.tx() as conn:
        conn.execute("DELETE FROM contracts WHERE id=?", (contract_id,))
    pipeline.bus.clear(contract_id)
    return {"ok": True}


# ------------------------------------------------------------------ 人工审核（HITL）
_HITL_ACTIONS = ("approve", "edit", "reject", "resume")


def _hitl_history(contract_id: int) -> list[dict]:
    with db.tx() as conn:
        rows = db.rows(conn.execute(
            "SELECT * FROM hitl_actions WHERE contract_id=? ORDER BY id ASC", (contract_id,)))
    for r in rows:
        try:
            r["payload"] = json.loads(r.get("payload_json") or "{}")
        except Exception:
            r["payload"] = {}
    return rows


def _interrupt_point(c: dict) -> tuple[str, str]:
    """判断当前流程停在哪个环节、为什么停 —— 这是 HITL 的"中断点"定义。

    与 LangGraph 的 interrupt 语义对齐：中断点不是"报错"，而是
    "流程主动停下来等人做决定"的位置，因此必须给出可执行的下一步动作。
    """
    status = c.get("status", "")
    if status == "blocked":
        return "parsing", c.get("block_reason") or "文档解析受阻，需要人工介入"
    if status == "completed":
        last = _last_hitl(c["id"])
        if last and last["action"] == "reject":
            return "reviewing", "审查结果已被驳回，等待重新审查"
        if last and last["action"] in ("approve", "edit"):
            return "applying", "已确认审查结果，等待回写审批系统"
        return "reviewing", "审查已完成，等待法务确认审查结论"
    if status in ("pending", "parsing", "reviewing"):
        return status, "流水线运行中，无需人工介入"
    return status or "unknown", ""


def _last_hitl(contract_id: int) -> dict | None:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM hitl_actions WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
    return dict(row) if row else None


def _available_actions(c: dict, last: dict | None) -> list[str]:
    status = c.get("status", "")
    if status == "blocked":
        return ["resume"]
    if status in ("pending", "parsing", "reviewing"):
        return []
    if status == "completed":
        if last and last["action"] == "reject":
            return []
        if last and last["action"] in ("approve", "edit"):
            return ["reject"]
        return ["approve", "edit", "reject"]
    return []


@router.get("/{contract_id}/hitl")
def get_hitl(contract_id: int):
    c = _require(contract_id)
    last = _last_hitl(contract_id)
    interrupt_at, reason = _interrupt_point(c)
    actions = _available_actions(c, last)
    if c["status"] == "completed" and actions:
        status = "pending_review"
    elif last and last["action"] == "approve":
        status = "approved"
    elif last and last["action"] == "edit":
        status = "edited"
    elif last and last["action"] == "reject":
        status = "rejected"
    else:
        status = c["status"]
    history = _hitl_history(contract_id)
    return {
        "contract_id": contract_id,
        "interrupt_at": interrupt_at,
        "interrupt_reason": reason,
        "status": status,
        "available_actions": actions,
        "last_action": history[-1] if history else {},
        "history": history,
        "writeback_allowed": ("success" in (writeback.serialize(writeback.get(contract_id)) or {})
                              or c["status"] == "completed") and not (
            last and last["action"] == "reject"),
    }


@router.post("/{contract_id}/human-review")
def human_review(contract_id: int, req: schemas.HumanReviewRequest):
    """人工审核决策入口。所有动作都会写入 hitl_actions 流水，可完整回溯。"""
    c = _require(contract_id)
    action = (req.action or "").strip().lower()
    if action not in _HITL_ACTIONS:
        raise HTTPException(status_code=400,
                            detail=f"不支持的审核动作：{req.action}（可选 {list(_HITL_ACTIONS)}）")

    interrupt_at, reason = _interrupt_point(c)
    edits = [e.model_dump() for e in req.edits]

    if action == "reject" and c["status"] != "completed":
        raise HTTPException(status_code=409,
                            detail=f"当前状态 {c['status']} 无可驳回的审查结果")
    if action in ("approve", "edit") and c["status"] != "completed":
        raise HTTPException(status_code=409,
                            detail=f"当前状态 {c['status']} 尚无审查结果可供确认")

    applied: list[dict] = []
    if action == "edit":
        if not edits:
            raise HTTPException(status_code=400, detail="action=edit 时必须提供 edits 修改清单")
        for e in edits:
            fid = int(e.get("finding_id") or 0)
            with db.tx() as conn:
                row = conn.execute("SELECT * FROM findings WHERE id=? AND contract_id=?",
                                   (fid, contract_id)).fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"风险项不存在：{fid}")
                sets, args = [], []
                if e.get("level") in ("high", "medium", "low"):
                    sets.append("level=?")
                    args.append(e["level"])
                if e.get("advice"):
                    sets.append("advice=?")
                    sets.append("advice_source=?")
                    args += [e["advice"], "manual"]
                if e.get("adopted") is not None:
                    sets.append("adopted=?")
                    args.append(1 if e["adopted"] else 0)
                if sets:
                    conn.execute(f"UPDATE findings SET {', '.join(sets)} WHERE id=?",
                                 (*args, fid))
            applied.append({"finding_id": fid, "level": e.get("level"),
                            "advice_changed": bool(e.get("advice"))})

        # 人工改级后重算综合风险等级与计数，保证大盘展示与明细一致
        with db.tx() as conn:
            cnt = {r["level"]: r["n"] for r in db.rows(conn.execute(
                "SELECT level, COUNT(*) n FROM findings WHERE contract_id=? GROUP BY level",
                (contract_id,)))}
            conn.execute(
                "UPDATE reviews SET high_cnt=?, medium_cnt=?, low_cnt=?, generated_by=?"
                " WHERE contract_id=?",
                (cnt.get("high", 0), cnt.get("medium", 0), cnt.get("low", 0),
                 "human_edited", contract_id))
        overall = ("high" if cnt.get("high") else "medium" if cnt.get("medium")
                   else "low" if cnt.get("low") else "none")
        conclusion = ("建议拒绝" if cnt.get("high") else
                      "建议整改后通过" if cnt.get("medium") else "建议通过")
        _update_task(contract_id, risk_level=overall, conclusion=conclusion)

    if action == "reject":
        # 驳回：清空本次审查产物，任务回到 pending 等待重跑（与 retry 同语义）
        with db.tx() as conn:
            conn.execute("DELETE FROM findings WHERE contract_id=?", (contract_id,))
            conn.execute("DELETE FROM reviews WHERE contract_id=?", (contract_id,))
        _update_task(contract_id, status="pending", risk_level="none",
                     conclusion="", risk_summary="", block_code="", block_reason="")

    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO hitl_actions(contract_id, action, actor, note, interrupt_at,"
            " payload_json, created_at) VALUES(?,?,?,?,?,?,?)",
            (contract_id, action, req.actor, req.note, interrupt_at,
             json.dumps({"edits": applied}, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")))
        hitl_id = int(cur.lastrowid)

    audit.log(contract_id, f"hitl.{action}", tool="workbench",
              input_value={"actor": req.actor, "edits": len(edits)},
              output_value={"hitl_id": hitl_id, "interrupt_at": interrupt_at},
              detail=req.note[:500])

    result = {"ok": True, "hitl_id": hitl_id, "action": action,
              "applied": applied, "interrupt_at": interrupt_at}
    if action == "reject":
        result["message"] = "已驳回审查结果，任务回到待处理，可重新发起审查"
        result["rerun"] = pipeline.run_pipeline(contract_id)
    elif action == "resume":
        result["message"] = "已从阻塞中断点恢复"
        result["rerun"] = pipeline.retry(contract_id)
    elif action == "edit":
        result["message"] = f"已应用 {len(applied)} 处人工修改，风险等级与结论已重算"
    else:
        result["message"] = "已确认审查结论，可执行写回审批系统"
    return result


def _update_task(contract_id: int, **fields) -> None:
    pipeline._update_contract(contract_id, **fields)
