# -*- coding: utf-8 -*-
"""审查流水线：状态机 + 事件总线 + 阻塞重试

任务状态机（与项目规格 2.4.4 完全一致）：
    pending ──► parsing ──► reviewing ──► completed
                   │            │
                   └────► blocked ◄┘        （阻塞原因 + 一键重试 → 回到 pending）

设计要点：
1. **解析与审查严格分离** —— 解析失败（加密 / 空文 / OCR 低置信）在 parsing 阶段就分流出 blocked，
   不会带着空正文去跑规则引擎，避免产出"看起来正常其实全错"的审查意见；
2. **进度可观测** —— 每个阶段向 EventBus 推事件，前端通过 SSE 实时看到流水线点亮，
   不必轮询、也不会出现"卡住了不知道卡在哪"；
3. **重试是幂等的** —— 重试会清空该合同的历史 clauses/findings/review 后重跑，
   不会累积脏数据；回写状态单独保留（回写幂等键不因重试而失效）。
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

from .. import config, db
from . import (audit, coordinator, llm, parser_docx, parser_ocr, parser_pdf,
               report, rule_engine, writeback, writer)

# ------------------------------------------------------------------ 事件总线
class EventBus:
    """极简内存事件总线：每个合同一条追加序列，SSE 按索引增量拉取。

    没有用 Redis / 消息队列 —— 单机演示场景下，进程内的队列更简单、更可控，
    也符合"零外部服务依赖"的项目红线。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[int, list[dict]] = {}
        self._waiters: dict[int, list[Queue]] = {}

    def publish(self, contract_id: int, stage: str, message: str, *,
                progress: int = 0, level: str = "info", extra: dict | None = None) -> dict:
        evt = {
            "seq": 0,
            "contract_id": contract_id,
            "stage": stage,
            "message": message,
            "progress": progress,
            "level": level,
            "ts": datetime.now().strftime("%H:%M:%S"),
            "extra": extra or {},
        }
        with self._lock:
            bucket = self._events.setdefault(contract_id, [])
            evt["seq"] = len(bucket)
            bucket.append(evt)
            waiters = list(self._waiters.get(contract_id, []))
        for q in waiters:
            try:
                q.put_nowait(evt)
            except Exception:
                pass
        return evt

    def since(self, contract_id: int, seq: int) -> list[dict]:
        with self._lock:
            bucket = self._events.get(contract_id, [])
            return bucket[seq:]

    def clear(self, contract_id: int) -> None:
        with self._lock:
            self._events.pop(contract_id, None)

    def subscribe(self, contract_id: int) -> Queue:
        q: Queue = Queue()
        with self._lock:
            self._waiters.setdefault(contract_id, []).append(q)
        return q

    def unsubscribe(self, contract_id: int, q: Queue) -> None:
        with self._lock:
            lst = self._waiters.get(contract_id, [])
            if q in lst:
                lst.remove(q)


bus = EventBus()
_jobs: dict[int, threading.Thread] = {}
_jobs_lock = threading.Lock()


# ------------------------------------------------------------------ 工具
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gen_code() -> str:
    """生成任务编号。

    末尾加 4 位随机码是必须的：编号参与幂等键构造，若只精确到秒，
    同一秒内创建的两个任务会生成相同编号 → 幂等键撞车 → 唯一索引直接报错。
    真实审批系统的审批单号天然唯一，这里的编号只是它在系统中的映射。
    """
    return "HJ-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + \
        "%04x" % random.getrandbits(16)


def _contract(contract_id: int) -> dict | None:
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    return dict(row) if row else None


def _update_contract(contract_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as conn:
        conn.execute(f"UPDATE contracts SET {sets} WHERE id=?",
                     (*fields.values(), contract_id))


# ------------------------------------------------------------------ 建任务
def create_task(*, file_path: str | Path | None, file_name: str, applicant: str = "",
                dept: str = "", biz_type: str = "other", source: str = "upload",
                name: str | None = None, force_new: bool = False) -> dict:
    """创建审查任务。幂等键 = 合同编号 + 附件 SHA256 + 规则版本号。"""
    sha = file_sha256(file_path) if file_path else ""
    rv = rule_engine.rule_version()
    code = gen_code()
    idem = writeback.idem_key(code, sha, rv)

    if file_path:
        stored = Path(config.UPLOAD_DIR) / f"{sha[:16]}_{Path(file_name).name}"
        if not stored.exists():
            stored.write_bytes(Path(file_path).read_bytes())
    else:
        stored = None

    if sha and not force_new:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT c.id, c.code, c.status, d.sha256 FROM contracts c"
                " JOIN documents d ON d.contract_id=c.id WHERE d.sha256=?"
                " ORDER BY c.id DESC LIMIT 1", (sha,),
            ).fetchone()
        if row:
            bus.publish(int(row["id"]), "idempotent", "检测到相同附件指纹，复用既有审查任务",
                        level="warn")
            return {"contract_id": int(row["id"]), "code": row["code"],
                    "reused": True, "sha256": sha}

    now = _now()
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO contracts(code, name, applicant, dept, biz_type, status,"
            " idem_key, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (code, name or Path(file_name).stem, applicant, dept, biz_type,
             "pending", idem, source, now, now),
        )
        cid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO documents(contract_id, file_name, file_type, sha256, size_bytes,"
            " stored_path, parse_status, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (cid, file_name, "unknown", sha,
             Path(file_path).stat().st_size if file_path else 0,
             str(stored) if stored else "", "pending", now),
        )
    audit.log(cid, "task.create", tool="ingest",
              input_value={"file": file_name, "sha256": sha, "source": source},
              output_value={"id": cid, "code": code})
    return {"contract_id": cid, "code": code, "reused": False, "sha256": sha}


# ------------------------------------------------------------------ 解析
def run_parse(contract_id: int) -> dict:
    c = _contract(contract_id)
    if not c:
        raise ValueError(f"合同不存在：{contract_id}")

    with db.tx() as conn:
        drow = conn.execute(
            "SELECT * FROM documents WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
    if not drow:
        raise ValueError("缺少文档记录")
    doc = dict(drow)
    path = doc.get("stored_path") or ""

    _update_contract(contract_id, status="parsing", block_reason="", block_code="")
    bus.publish(contract_id, "parsing", "开始解析合同文档", progress=10)

    blocks: list[dict] = []
    engine = ""
    page_count = 0
    ocr_avg: float | None = None
    ocr_error: str | None = None
    encrypted = False
    parse_error = ""

    file_type = coordinator.detect_file_type(doc["file_name"], path)
    started = time.time()
    try:
        if file_type == "docx":
            res = parser_docx.parse(path)
            blocks, engine, page_count = res["blocks"], res["engine"], res["page_count"]
            bus.publish(contract_id, "parsing", f"DOCX 段落树解析完成（{len(blocks)} 段）", progress=35)
        elif file_type in ("pdf", "scan_pdf"):
            try:
                res = parser_pdf.parse(path, enable_ocr=config.OCR_ENABLED)
            except parser_pdf.PdfEncrypted as exc:
                encrypted = True
                raise ValueError(str(exc)) from exc
            blocks, engine, page_count = res["blocks"], res["engine"], res["page_count"]
            if res.get("ocr_pages"):
                ocr_avg = 0.0
                vals = [b.get("confidence") or 0 for b in blocks if b.get("confidence") is not None]
                ocr_avg = round(sum(vals) / len(vals), 4) if vals else 0.0
                bus.publish(contract_id, "parsing",
                            f"图片型 PDF：已对 {res['ocr_pages']} 页执行 OCR", progress=35)
            else:
                bus.publish(contract_id, "parsing", f"PDF 文本层解析完成（{page_count} 页）", progress=35)
        elif file_type == "image":
            blocks, ocr_avg = parser_ocr.recognize_file(path)
            engine = parser_ocr.engine_name()
            page_count = 1
            bus.publish(contract_id, "parsing",
                        f"扫描件 OCR 完成（{len(blocks)} 行，平均置信度 {ocr_avg:.2f}）", progress=35)
        elif file_type == "text":
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            blocks = [{"page": i + 1, "para_index": i, "text": ln.strip(), "kind": "text",
                       "confidence": 1.0}
                      for i, ln in enumerate(text.split("\n")) if ln.strip()]
            engine, page_count = "plaintext", 1
        else:
            raise ValueError(f"不支持的文件类型：{doc['file_name']}")
    except parser_ocr.OcrUnavailable as exc:
        ocr_error = str(exc)
        bus.publish(contract_id, "blocked", f"OCR 引擎不可用：{exc}", progress=100, level="error")
    except Exception as exc:
        parse_error = str(exc)

    cost_ms = int((time.time() - started) * 1000)

    if parse_error and not encrypted:
        reason = f"解析失败：{parse_error}"
        _apply_block(contract_id, doc["id"], "PARSE_FAILED", reason,
                     engine=engine, page_count=page_count, cost_ms=cost_ms)
        bus.publish(contract_id, "blocked", reason, progress=100, level="error")
        raise ValueError(reason)

    blocks, full_text = coordinator.normalize_blocks(blocks)
    blocked, block_code, block_reason = coordinator.decide_block(
        {"encrypted": encrypted}, full_text, ocr_avg, ocr_error)

    if blocked:
        _apply_block(contract_id, doc["id"], block_code, block_reason,
                     engine=engine, page_count=page_count, cost_ms=cost_ms,
                     char_count=len(full_text))
        bus.publish(contract_id, "blocked", block_reason, progress=100, level="error")
        return {"blocked": True, "block_code": block_code, "reason": block_reason}

    classified = coordinator.classify_blocks(blocks)
    clauses = coordinator.build_clauses(classified)
    meta = coordinator.extract_metadata(full_text, classified)

    with db.tx() as conn:
        conn.execute("DELETE FROM clauses WHERE contract_id=?", (contract_id,))
        for cl in clauses:
            conn.execute(
                "INSERT INTO clauses(document_id, contract_id, clause_type, heading, page,"
                " para_index, char_start, char_end, text, confidence, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (doc["id"], contract_id, cl["clause_type"], cl["heading"], cl["page"],
                 cl["para_index"], cl["char_start"], cl["char_end"], cl["text"],
                 cl.get("confidence"), _now()),
            )
        conn.execute(
            "UPDATE documents SET file_type=?, parse_engine=?, parse_status=?, page_count=?,"
            " char_count=?, block_reason=?, ocr_confidence=? WHERE id=?",
            (file_type, engine, "success", page_count, len(full_text), block_reason,
             ocr_avg, doc["id"]),
        )

    detected_biz = _detect_biz_type(full_text)
    fields: dict = {
        "meta_json": _json(meta),
        "biz_type": c["biz_type"] if c["biz_type"] != "other" else detected_biz,
        "risk_summary": "",
    }
    flat = {i["key"]: i["value"] for i in meta["items"]}
    if flat.get("contract_name"):
        fields["name"] = flat["contract_name"]
    for role in ("甲方", "乙方", "供方", "需方", "委托方", "受托方"):
        key = f"party_{role}"
        if flat.get(key) and not c.get("counterparty"):
            if role in ("乙方", "供方", "卖方", "受托方"):
                fields["counterparty"] = flat[key]
                break
    if meta.get("amount_value"):
        fields["amount"] = float(meta["amount_value"])
    if flat.get("currency"):
        fields["currency"] = flat["currency"]
    if flat.get("uscc"):
        fields["uscc"] = flat["uscc"]
    if flat.get("sign_date"):
        fields["sign_date"] = flat["sign_date"]
    if flat.get("perform_start"):
        fields["perform_start"] = flat["perform_start"]
    if flat.get("perform_end"):
        fields["perform_end"] = flat["perform_end"]
    _update_contract(contract_id, **fields)

    audit.log(contract_id, "parse.done", tool=engine,
              input_value={"file": doc["file_name"]},
              output_value={"blocks": len(classified), "clauses": len(clauses),
                            "chars": len(full_text), "pages": page_count},
              cost_ms=cost_ms)
    bus.publish(contract_id, "parsing",
                f"解析完成：{page_count} 页 / {len(clauses)} 项条款 / {len(full_text)} 字",
                progress=50, extra={"engine": engine, "clauses": len(clauses)})
    return {"blocked": False, "engine": engine, "page_count": page_count,
            "clauses": len(clauses), "chars": len(full_text), "meta": meta,
            "quality_note": block_reason}


def _detect_biz_type(text: str) -> str:
    table = {
        "采购": ["采购", "供货", "供方", "采购方", "供应商"],
        "销售": ["销售", "经销", "买方", "客户"],
        "劳动": ["劳动", "员工", "用人单位", "劳动者", "薪酬", "岗位"],
        "服务": ["服务", "委托", "咨询", "运维", "技术服务"],
    }
    scores = {k: sum(1 for w in v if w in text) for k, v in table.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "other"


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _apply_block(contract_id: int, document_id: int, code: str, reason: str, *,
                 engine: str = "", page_count: int = 0, cost_ms: int = 0,
                 char_count: int = 0) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE documents SET parse_status='blocked', block_reason=?, parse_engine=?,"
            " page_count=?, char_count=? WHERE id=?",
            (reason, engine, page_count, char_count, document_id),
        )
    _update_contract(contract_id, status="blocked", block_code=code, block_reason=reason)
    audit.log(contract_id, "parse.blocked", tool=engine, level="error",
              output_value={"code": code, "reason": reason}, cost_ms=cost_ms)


# ------------------------------------------------------------------ 审查
def run_review(contract_id: int) -> dict:
    c = _contract(contract_id)
    if not c:
        raise ValueError(f"合同不存在：{contract_id}")

    with db.tx() as conn:
        crow = db.rows(conn.execute(
            "SELECT * FROM clauses WHERE contract_id=? ORDER BY char_start", (contract_id,)))
        drow = conn.execute(
            "SELECT * FROM documents WHERE contract_id=? ORDER BY id DESC LIMIT 1",
            (contract_id,)).fetchone()
    if not crow:
        raise ValueError("尚无解析结果，请先执行解析")

    doc = dict(drow) if drow else {}
    full_text = Path(doc.get("stored_path") or "").exists() and "" or ""
    clauses = crow
    # full_text 由条款文本按同一分隔规则重建（与解析阶段一致，保证坐标语义不变）
    full_text = _rebuild_text(clauses, int(doc.get("char_count") or 0))

    _update_contract(contract_id, status="reviewing")
    bus.publish(contract_id, "reviewing", "规则引擎开始执行确定性判定", progress=58)

    rv = rule_engine.rule_version()
    started = time.time()
    # 视角解析：把"归甲方所有"这类表述还原成"对我方是否有利"，避免采购/销售互相误报
    meta_raw: dict = {}
    try:
        meta_raw = json.loads(c.get("meta_json") or "{}")
    except Exception:
        meta_raw = {}
    flat_meta = {i["key"]: i["value"] for i in meta_raw.get("items", [])}
    ctx = rule_engine.build_ctx(
        c.get("biz_type"),
        flat_meta.get("party_甲方", ""), flat_meta.get("party_乙方", ""),
    )
    findings = rule_engine.evaluate(clauses, full_text, ctx=ctx)
    rule_ms = int((time.time() - started) * 1000)
    stats = writer.count_levels(findings)
    bus.publish(contract_id, "reviewing",
                f"规则引擎命中 {len(findings)} 项风险"
                f"（高 {stats.get('high', 0)} / 中 {stats.get('medium', 0)} / 低 {stats.get('low', 0)}）",
                progress=70, extra={"high": stats.get("high", 0)})

    # ---- LLM 语义增强（增强路径，失败即降级，不影响主判定）----
    meta = meta_raw
    enriched = llm.enrich(findings, {**c, **{"biz_type": c.get("biz_type")}})
    explanations = llm.template_explanations(findings)
    llm_items = {}
    if enriched.used_llm:
        for it in enriched.data.get("items", []):
            llm_items[it.get("rule_id")] = it
        bus.publish(contract_id, "reviewing", "大模型语义增强完成（解释与建议润色）",
                    progress=80, level="info", extra={"engine": "llm"})
    else:
        bus.publish(contract_id, "reviewing", f"大模型未启用/不可用，已降级模板生成器：{enriched.reason}",
                    progress=80, level="warn", extra={"engine": "template"})

    for f in findings:
        key = f"{f['rule_id']}:{f['char_start']}:{f['char_end']}"
        f["explanation"] = explanations.get(key, f.get("reason", ""))
        hint = llm_items.get(f["rule_id"])
        if hint:
            if hint.get("explanation"):
                f["explanation"] = str(hint["explanation"])[:800]
            if hint.get("advice_polish"):
                f["advice"] = str(hint["advice_polish"])[:1200]
                f["advice_source"] = "llm"

    # ---- 跨条款矛盾检测 ----
    conflict = llm.detect_conflicts(clauses, full_text)
    conflicts = conflict.data.get("conflicts", []) if conflict.used_llm else []
    if conflicts:
        bus.publish(contract_id, "reviewing", f"跨条款矛盾检测发现 {len(conflicts)} 处条款冲突",
                    progress=84)

    summary = writer.build_summary(stats, findings, {**meta, "biz_type": c.get("biz_type")},
                                   rule_count=rule_engine.stats()["total"])
    profile = writer.build_profile(clauses, findings)
    overall = writer.overall_level(stats)

    with db.tx() as conn:
        conn.execute("DELETE FROM findings WHERE contract_id=?", (contract_id,))
        cid_map: dict[tuple, int] = {}
        for f in findings:
            cur = conn.execute(
                "INSERT INTO findings(contract_id, clause_id, rule_id, rule_version, title, level,"
                " clause_type, judge_mode, evidence_text, page, para_index, char_start, char_end,"
                " reason, legal_basis, advice, advice_source, confidence, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (contract_id, None, f["rule_id"], f.get("rule_version", rv), f["title"],
                 f["level"], f["clause_type"], f.get("judge_mode", "rule"), f["evidence_text"],
                 f["page"], f["para_index"], f["char_start"], f["char_end"],
                 f.get("explanation") or f.get("reason", ""), f.get("legal_basis", ""),
                 f.get("advice", ""), f.get("advice_source", "template"),
                 f.get("confidence", 1.0), _now()),
            )
            cid_map[(f["rule_id"], f["char_start"])] = int(cur.lastrowid)

        conn.execute("DELETE FROM reviews WHERE contract_id=?", (contract_id,))
        conn.execute(
            "INSERT INTO reviews(contract_id, risk_summary, conclusion, focus_points, high_cnt,"
            " medium_cnt, low_cnt, reviewer_note, generated_by, rule_version, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (contract_id, summary["risk_summary"], summary["conclusion"],
             _json(summary["focus_points"]), stats.get("high", 0), stats.get("medium", 0),
             stats.get("low", 0), "", summary["generated_by"], rv, _now()),
        )

    _update_contract(contract_id, status="completed", risk_level=overall,
                     risk_summary=summary["risk_summary"], conclusion=summary["conclusion"],
                     profile_json=_json(profile), block_reason="", block_code="")

    audit.log(contract_id, "review.done", tool="rule_engine",
              input_value={"rules": rule_engine.stats()["total"], "rule_version": rv},
              output_value={"findings": len(findings), "level": overall,
                            "generated_by": summary["generated_by"]},
              cost_ms=rule_ms)
    bus.publish(contract_id, "completed",
                f"审查完成：综合风险等级 {config.RISK_LABEL.get(overall, overall)}，"
                f"结论「{summary['conclusion']}」",
                progress=100, extra={"risk_level": overall, "findings": len(findings)})

    return {"findings": findings, "stats": stats, "summary": summary,
            "profile": profile, "risk_level": overall, "conflicts": conflicts,
            "rule_ms": rule_ms}


def _rebuild_text(clauses: list[dict], total_chars: int) -> str:
    """按 clauses 的字符区间重建 full_text。

    条款区间之间用单个 '\n' 连接（与 coordinator.normalize_blocks 的规则一致），
    因此重建结果与解析阶段的字符串逐字符等价，坐标语义不变。
    """
    if not clauses:
        return ""
    size = max(total_chars, max(c["char_end"] for c in clauses) + 1)
    buf = [" "] * size
    for c in clauses:
        text = c["text"]
        start = c["char_start"]
        for i, ch in enumerate(text):
            pos = start + i
            if 0 <= pos < size:
                buf[pos] = ch
    return "".join(buf).rstrip()


# ------------------------------------------------------------------ 全流程
def run_pipeline(contract_id: int) -> dict:
    """同步跑完整条流水线（解析 + 审查）。异常不会外抛，统一转为 blocked 状态。"""
    try:
        res = run_parse(contract_id)
        if res.get("blocked"):
            return {"status": "blocked", "reason": res.get("reason", "")}
        review = run_review(contract_id)
        return {"status": "completed", **review}
    except Exception as exc:
        tb = traceback.format_exc(limit=3)
        audit.log(contract_id, "pipeline.error", level="error",
                  output_value=str(exc), detail=tb)
        bus.publish(contract_id, "error", f"流水线异常：{exc}", progress=100, level="error")
        cur = _contract(contract_id) or {}
        if cur.get("status") not in ("blocked",):
            _update_contract(contract_id, status="blocked", block_code="PIPELINE_ERROR",
                             block_reason=f"流水线异常：{exc}")
        return {"status": "blocked", "reason": str(exc)}


def run_pipeline_async(contract_id: int) -> None:
    """后台线程执行，用于"上传后立即返回、前端看实时进度"的体验。"""
    with _jobs_lock:
        old = _jobs.get(contract_id)
        if old and old.is_alive():
            return
        t = threading.Thread(target=run_pipeline, args=(contract_id,),
                             name=f"hj-pipeline-{contract_id}", daemon=True)
        _jobs[contract_id] = t
    t.start()


def retry(contract_id: int) -> dict:
    """阻塞重试：清空历史解析/审查产物后重跑（回写记录保留，幂等键不变）。"""
    c = _contract(contract_id)
    if not c:
        raise ValueError(f"合同不存在：{contract_id}")
    bus.clear(contract_id)
    with db.tx() as conn:
        conn.execute("DELETE FROM clauses WHERE contract_id=?", (contract_id,))
        conn.execute("DELETE FROM findings WHERE contract_id=?", (contract_id,))
        conn.execute("DELETE FROM reviews WHERE contract_id=?", (contract_id,))
        conn.execute("UPDATE documents SET parse_status='pending', block_reason=''"
                     " WHERE contract_id=?", (contract_id,))
    _update_contract(contract_id, status="pending", block_code="", block_reason="",
                     risk_level="none")
    audit.log(contract_id, "task.retry", tool="pipeline",
              input_value={"previous_block": c.get("block_code", "")})
    bus.publish(contract_id, "pending", "已重置任务状态，开始重新解析", progress=2, level="warn")
    return run_pipeline(contract_id)


# ------------------------------------------------------------------ 查询
def overview() -> dict:
    with db.tx() as conn:
        status_rows = db.rows(conn.execute(
            "SELECT status, COUNT(*) AS n FROM contracts GROUP BY status"))
        risk_rows = db.rows(conn.execute(
            "SELECT risk_level, COUNT(*) AS n FROM contracts GROUP BY risk_level"))
        total = conn.execute("SELECT COUNT(*) AS n FROM contracts").fetchone()["n"]
        findings = conn.execute("SELECT COUNT(*) AS n FROM findings").fetchone()["n"]
        highs = conn.execute(
            "SELECT COUNT(*) AS n FROM findings WHERE level='high'").fetchone()["n"]
        writes = db.rows(conn.execute(
            "SELECT status, COUNT(*) AS n FROM writebacks GROUP BY status"))
        blocked = conn.execute(
            "SELECT COUNT(*) AS n FROM contracts WHERE status='blocked'").fetchone()["n"]
        completed = conn.execute(
            "SELECT COUNT(*) AS n FROM contracts WHERE status='completed'").fetchone()["n"]
        avg = conn.execute(
            "SELECT AVG(cost_ms) AS a FROM audit_logs WHERE step='parse.done'").fetchone()["a"]
    return {
        "total": total,
        "completed": completed,
        "blocked": blocked,
        "parse_success_rate": round(completed / total, 4) if total else 0.0,
        "findings": findings,
        "high_findings": highs,
        "avg_parse_ms": int(avg or 0),
        "by_status": {r["status"]: r["n"] for r in status_rows},
        "by_risk": {r["risk_level"]: r["n"] for r in risk_rows},
        "by_writeback": {r["status"]: r["n"] for r in writes},
        "rule_version": rule_engine.rule_version(),
    }
