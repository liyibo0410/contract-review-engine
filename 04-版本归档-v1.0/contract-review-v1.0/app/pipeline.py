# -*- coding: utf-8 -*-
"""审查流水线编排（接入与流转模块）

负责把「拉取/上传 → 附件校验 → 解析 → 条款切分 → 规则审查 → 摘要与建议 →
状态落库 → 评论回写 → 报告导出」串成一条可观测、可重试的链路，
并按审批单 / 文件指纹做幂等控制。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time

from . import llm as llm_mod
from . import output
from . import parsers
from . import review as review_mod
from . import store
from . import writeback

# 通知器按「配置对象」缓存一份（同一进程内复用，避免每次审查都新建线程池）
_NOTIFIERS = {}
_NOTIFIERS_LOCK = threading.RLock()


def get_notifier(cfg, st=None):
    with _NOTIFIERS_LOCK:
        key = id(cfg)
        n = _NOTIFIERS.get(key)
        if n is None:
            from . import notify as notify_mod
            n = notify_mod.Notifier(cfg)
            _NOTIFIERS[key] = n
        if st is not None and getattr(n, "store", None) is None:
            n.store = st
        return n


# ------------------------------------------------------------------ 工具
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str, fallback: str = "contract") -> str:
    """把上传的文件名规整为安全的纯文件名。

    只取 basename，去掉路径分隔与 Windows 保留字符，剔除以点开头的隐藏名，
    并限制长度 —— 防止 ".."/绝对路径/超长名写穿上传目录。
    """
    name = os.path.basename(str(name or "").replace("\\", "/"))
    bad = '<>:"/\\|?*'
    name = "".join("_" if c in bad else c for c in name).strip()
    name = name.lstrip(".").strip()
    if not name:
        name = fallback
    stem, ext = os.path.splitext(name)
    if len(stem) > 120:
        stem = stem[:120]
    return stem + ext[:12]


def parsed_path(cfg, case_id: int) -> str:
    return os.path.join(cfg.path("parsed"), "case_%d.json" % case_id)


# 解析结果缓存：详情页每次都要读，加一层按 mtime 失效的内存缓存
_PARSED_CACHE = {}
_PARSED_LOCK = threading.RLock()


def save_parsed(cfg, case_id: int, payload: dict):
    path = parsed_path(cfg, case_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)                       # 原子替换，避免读到半个文件
    with _PARSED_LOCK:
        _PARSED_CACHE[case_id] = (os.path.getmtime(path), payload)


def load_parsed(cfg, case_id: int):
    path = parsed_path(cfg, case_id)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _PARSED_LOCK:
            _PARSED_CACHE.pop(case_id, None)
        return None
    with _PARSED_LOCK:
        hit = _PARSED_CACHE.get(case_id)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    with _PARSED_LOCK:
        _PARSED_CACHE[case_id] = (mtime, payload)
    return payload


# ------------------------------------------------------------------ 接入：审批拉取
def pull_from_approval(cfg, st: store.Store, auto_review: bool = True):
    items, err = writeback.fetch_todo(cfg)
    if err:
        st.audit(None, "pull", "approval.todo", "failed", err)
        return {"ok": False, "error": err, "created": [], "skipped": []}

    created, skipped = [], []
    for it in items:
        iid = it.get("instance_id")
        exist = st.case_by_approval(iid)
        if exist:
            skipped.append({"instance_id": iid, "case_id": exist["id"],
                            "reason": "该审批单已建任务（幂等控制）"})
            continue

        src = os.path.join(cfg.path("samples"), it.get("attachment") or "")
        if not it.get("attachment") or not os.path.exists(src):
            skipped.append({"instance_id": iid, "reason": "附件缺失，无法下载",
                            "attachment": it.get("attachment")})
            st.audit(None, "pull", "attachment.download", "failed",
                     "审批单 %s 附件不存在：%s" % (iid, it.get("attachment")))
            continue

        dst = os.path.join(cfg.path("uploads"),
                           "%s_%s" % (safe_name(iid), safe_name(it["attachment"])))
        shutil.copyfile(src, dst)
        sha = sha256_file(dst)

        # 文件指纹去重：同一份文档不重复解析
        dup = st.case_by_sha(sha)
        if dup and dup.get("review_status") != store.S_BLOCKED:
            skipped.append({"instance_id": iid, "case_id": dup["id"],
                            "reason": "文件指纹重复，复用既有审查结果"})
            st.audit(dup["id"], "pull", "attachment.sha256", "skipped",
                     "审批单 %s 附件与既有案件指纹一致" % iid)
            continue

        cid = st.create_case(
            title=it.get("title") or "待审合同",
            applicant=it.get("applicant") or "",
            send_dept=it.get("send_dept") or "",
            business_type=it.get("business_type") or "purchase",
            counterparty=it.get("counterparty") or "",
            credit_code=it.get("credit_code") or "",
            amount=it.get("amount"),
            perform_period=it.get("perform_period") or "",
            source="approval",
            approval_instance=iid,
            doc_sha256=sha,
        )
        st.add_document(cid, filename=it["attachment"],
                        doc_type=parsers.detect_type(dst, it["attachment"]),
                        sha256=sha, file_path=dst)
        st.audit(cid, "pull", "approval.todo", "ok",
                 "拉取审批单 %s 附件并计算 SHA256=%s" % (iid, sha[:16]))
        created.append({"instance_id": iid, "case_id": cid,
                        "title": it.get("title"), "sha256": sha[:16]})

    st.audit(None, "pull", "pipeline.pull", "ok",
             "新建任务 %d 个，跳过 %d 个" % (len(created), len(skipped)))
    reviewed = []
    if auto_review:
        for c in created:
            r = run_review(cfg, st, c["case_id"])
            row = st.get_case(c["case_id"]) or {}
            reviewed.append({
                "title": row.get("title"), "case_no": row.get("case_no"),
                "risk_level": row.get("risk_level"),
                "blocked": bool(r.get("blocked")) or row.get("review_status") == store.S_BLOCKED,
            })
        # 批量汇总通知：一轮拉取只发一条，避免刷屏
        try:
            from . import notify as notify_mod
            notify_mod.notify_batch_summary(get_notifier(cfg, st), created, skipped, reviewed)
        except Exception:                                        # noqa: BLE001
            pass
    return {"ok": True, "created": created, "skipped": skipped, "reviewed": reviewed}


# ------------------------------------------------------------------ 接入：本地上传
def ingest_upload(cfg, st: store.Store, filename: str, data: bytes,
                  meta: dict | None = None, auto_review: bool = True):
    meta = meta or {}
    limit_mb = int(cfg.review.get("max_upload_mb", 30))
    if len(data) > limit_mb * 1024 * 1024:
        st.audit(None, "upload", "pipeline.ingest", "failed",
                 "上传被拒：%s（%.1f MB > %d MB 上限）" % (
                     safe_name(filename), len(data) / 1048576.0, limit_mb))
        return {"ok": False, "case_id": None, "duplicated": False,
                "error": "文件大小 %.1f MB 超过 %d MB 上限" % (
                    len(data) / 1048576.0, limit_mb)}
    sha = sha256_bytes(data)
    dst = os.path.join(cfg.path("uploads"), safe_name(filename))
    base, ext = os.path.splitext(dst)
    n = 1
    while os.path.exists(dst):
        try:
            if sha256_file(dst) == sha:
                break
        except OSError:
            break
        dst = "%s_%d%s" % (base, n, ext)
        n += 1
    with open(dst, "wb") as f:
        f.write(data)

    dup = st.case_by_sha(sha)
    if dup:
        st.audit(dup["id"], "upload", "pipeline.dedupe", "skipped",
                 "上传文件指纹与既有案件一致，复用既有任务")
        return {"ok": True, "case_id": dup["id"], "duplicated": True,
                "sha256": sha, "message": "文件指纹重复，已复用既有审查任务"}

    title = meta.get("title") or os.path.splitext(os.path.basename(filename))[0]
    cid = st.create_case(
        title=title,
        applicant=meta.get("applicant") or "李奕波",
        send_dept=meta.get("send_dept") or "法务部",
        business_type=meta.get("business_type") or "purchase",
        counterparty=meta.get("counterparty") or "",
        amount=meta.get("amount"),
        perform_period=meta.get("perform_period") or "",
        source="local",
        doc_sha256=sha,
    )
    st.add_document(cid, filename=os.path.basename(filename),
                    doc_type=parsers.detect_type(dst, filename),
                    sha256=sha, file_path=dst)
    st.audit(cid, "upload", "pipeline.ingest", "ok",
             "本地上传受理，文件 %s，SHA256=%s" % (os.path.basename(filename), sha[:16]))
    if auto_review:
        run_review(cfg, st, cid)
    return {"ok": True, "case_id": cid, "duplicated": False, "sha256": sha,
            "message": "上传成功，已完成自动审查"}


# ------------------------------------------------------------------ 通知封装
# 所有通知调用都包在 try/except 里：通知是旁路能力，任何异常都不允许影响审查主流程。
def _case_brief(case, doc=None):
    """给通知模块用的精简案件视图。"""
    return {
        "id": case.get("id"),
        "title": case.get("title"),
        "case_no": case.get("case_no"),
        "applicant": case.get("applicant"),
        "send_dept": case.get("send_dept"),
        "business_type_label": output._btype_label(case.get("business_type")),
        "counterparty": case.get("counterparty"),
        "amount_text": output._amount_text(case),
        "risk_level_label": store.RISK_LABEL.get(case.get("risk_level"), "-"),
        "conclusion_label": review_mod.CONCLUSION_LABEL.get(case.get("conclusion"), "-"),
        "source_label": "审批系统拉取" if case.get("source") == "approval" else "本地上传",
        "approval_instance": case.get("approval_instance"),
        "document_summary": ("%s ｜ 引擎 %s ｜ %s 页 / %s 段" % (
            doc.get("filename"), doc.get("parse_engine"),
            doc.get("page_count"), doc.get("para_count"))) if doc else "",
    }


def _notify_reviewed(nt, case, doc_row, hits, summary):
    try:
        from . import notify as notify_mod
        notify_mod.notify_review_completed(nt, _case_brief(case, doc_row), hits, summary)
    except Exception:                                            # noqa: BLE001
        pass


def _notify_blocked(nt, cfg, st, case_id, reason):
    try:
        from . import notify as notify_mod
        case = st.get_case(case_id)
        if not case:
            return
        notify_mod.notify_blocked(nt, _case_brief(case, st.case_document(case_id)), reason)
    except Exception:                                            # noqa: BLE001
        pass


def notify_writeback_failed(cfg, st, case, error):
    """回写失败通知（供 writeback 调用）。"""
    try:
        from . import notify as notify_mod
        nt = get_notifier(cfg, st)
        notify_mod.notify_writeback_failed(nt, _case_brief(case, st.case_document(case["id"])),
                                           error)
    except Exception:                                            # noqa: BLE001
        pass


# ------------------------------------------------------------------ 解析 + 审查
def run_review(cfg, st: store.Store, case_id: int):
    case = st.get_case(case_id)
    if not case:
        return {"ok": False, "error": "案件不存在"}
    doc = st.case_document(case_id)
    if not doc:
        st.update_case(case_id, review_status=store.S_BLOCKED,
                       blocked_reason="未找到合同附件，无法解析")
        st.audit(case_id, "parse", "parsers.parse_document", "failed", "附件缺失")
        return {"ok": False, "error": "附件缺失"}

    rv = cfg.review if hasattr(cfg, "review") else cfg["review"]
    nt = get_notifier(cfg, st)
    min_text_chars = int(rv.get("min_text_chars", 30))
    ocr_min_conf = float(rv.get("ocr_min_confidence", 0.5))
    block_on_fail = bool(rv.get("block_on_parse_fail", True))

    # ---- 1. 解析中
    st.update_case(case_id, review_status=store.S_PARSING, blocked_reason="")
    st.audit(case_id, "parse", "parsers.parse_document", "running",
             "开始解析 %s（引擎路由：按文件头与扩展名判定；阈值 正文≥%d 字 / OCR 置信度≥%.2f）"
             % (doc["filename"], min_text_chars, ocr_min_conf))
    res = parsers.parse_document(doc["file_path"], doc["filename"],
                                 min_text_chars=min_text_chars,
                                 ocr_min_confidence=ocr_min_conf,
                                 ocr_min_chars=min_text_chars)

    need_manual = bool(res.get("need_manual"))
    st._x("UPDATE contract_documents SET parse_engine=?, parse_status=?, parse_error=?,"
          " page_count=?, para_count=?, char_count=?, ocr_confidence=?, need_manual=?"
          " WHERE id=?",
          (res.get("engine", "-"), "ok" if res.get("ok") else "blocked",
           res.get("error", ""), res.get("page_count", 0), len(res.get("paragraphs") or []),
           res.get("char_count", 0), res.get("ocr_confidence", 0),
           1 if need_manual else 0, doc["id"]))

    if not res.get("ok"):
        if block_on_fail:
            st.update_case(case_id, review_status=store.S_BLOCKED,
                           blocked_reason=res.get("error") or "解析失败",
                           risk_level=store.R_NONE, conclusion="")
            st.audit(case_id, "parse", "parsers.parse_document", "blocked",
                     res.get("error") or "解析失败")
            _notify_blocked(nt, cfg, st, case_id, res.get("error") or "解析失败")
            return {"ok": False, "blocked": True, "reason": res.get("error"),
                    "case_id": case_id}
        # 关闭硬阻塞时：记录问题但继续往下走，只对已抽取到的少量文本做审查
        st.audit(case_id, "parse", "parsers.parse_document", "degraded",
                 "block_on_parse_fail=false，按降级继续：%s" % (res.get("error") or "解析质量不足"))

    st.audit(case_id, "parse", "parsers.parse_document", "ok",
             "解析成功：引擎=%s 页=%d 段=%d 字符=%d%s" % (
                 res.get("engine"), res.get("page_count", 0),
                 len(res.get("paragraphs") or []), res.get("char_count", 0),
                 (" OCR 置信度=%.2f" % res.get("ocr_confidence"))
                 if str(res.get("engine", "")).startswith("ocr") else ""))

    paras = res["paragraphs"]
    text = res["text"]

    # ---- 2. 审查中
    st.update_case(case_id, review_status=store.S_REVIEWING)
    st.audit(case_id, "review", "review.extract_metadata", "running", "抽取合同元数据")

    ct = review_mod.load_clause_types(cfg.root)
    meta = review_mod.extract_metadata(paras, text, doc["filename"])

    upd = {}
    if not case.get("counterparty") and meta.get("party_b"):
        upd["counterparty"] = meta["party_b"]
    if not case.get("credit_code") and meta.get("credit_code"):
        upd["credit_code"] = meta["credit_code"]
    if not case.get("amount") and meta.get("amount"):
        upd["amount"] = meta["amount"]
    if not case.get("currency") and meta.get("currency"):
        upd["currency"] = meta["currency"]
    if not case.get("perform_period") and meta.get("perform_period"):
        upd["perform_period"] = meta["perform_period"]
    if not case.get("effective_condition") and meta.get("effective_condition"):
        upd["effective_condition"] = meta["effective_condition"]
    if upd:
        st.update_case(case_id, **upd)
    st.audit(case_id, "review", "review.extract_metadata", "ok",
             "元数据：主体=%s 信用代码=%s 金额=%s %s 期限=%s" % (
                 meta.get("party_b") or "未识别", meta.get("credit_code") or "未载明",
                 meta.get("amount"), meta.get("currency"), meta.get("perform_period") or "未识别"))

    st.audit(case_id, "review", "review.split_clauses", "running", "条款层级切分")
    clauses = review_mod.split_clauses(paras, ct)
    coverage = review_mod.clause_coverage(paras, ct)
    st.audit(case_id, "review", "review.split_clauses", "ok",
             "切分条款 %d 段，识别条款类型 %d/%d 类" % (
                 len(clauses), len(coverage["present"]), coverage["total_types"]))

    st.audit(case_id, "review", "review.run_rules", "running", "规则引擎合规审查")
    rules_data = review_mod.load_rules(cfg.root, case.get("business_type") or "purchase")
    rule_diag = []
    hits = review_mod.run_rules(text, paras, rules_data, ct, diag=rule_diag)
    library = review_mod.load_clause_library(cfg.root)
    sug_map = {}
    for h in hits:
        primary, alts = review_mod.build_suggestion(h, library)
        h["suggestion"] = primary or h.get("suggestion", "")
        h["suggestion_alternatives"] = alts
        sug_map[h["rule_id"]] = {"primary": h["suggestion"], "alternatives": alts}
        if h.get("extra"):
            h["reason"] = (h.get("reason") or "") + h["extra"]
    st.audit(case_id, "review", "review.run_rules", "ok",
             "规则库 %s（%s）命中 %d 项：%s" % (
                 rules_data.get("contract_type"), rules_data.get("version"),
                 len(hits), "、".join(h["rule_id"] for h in hits) or "无"))
    if rule_diag:
        # 规则页支持在线编辑，写坏的正则必须被看见而不是静默失效
        st.audit(case_id, "review", "review.run_rules", "warning",
                 "发现 %d 条无效正则表达式：%s" % (
                     len(rule_diag),
                     "；".join("%s → %s" % (d["where"], d["error"]) for d in rule_diag[:5])))

    st.clear_results(case_id)
    st.add_results(case_id, hits)

    risk = st.overall_risk(case_id)
    highs = [h for h in hits if h["risk_level"] == "high"]
    conclusion = review_mod.decide_conclusion(risk, len(highs), len(hits), rv)
    st.update_case(case_id, risk_level=risk, conclusion=conclusion,
                   review_status=store.S_COMPLETED, blocked_reason="")

    # ---- 3. 摘要
    case = st.get_case(case_id)
    rows = st.results(case_id)
    doc_row = st.get_document(doc["id"])
    summary = output.build_summary(case, doc_row, rows, coverage)
    polished, source = llm_mod.polish_summary(cfg, summary["headline"], case, rows)
    if source == "llm":
        summary["headline_llm"] = polished
    summary["summary_source"] = source
    st.audit(case_id, "summary", "llm.polish_summary", "ok" if source == "llm" else "skipped",
             "摘要生成方式：%s" % source)

    save_parsed(cfg, case_id, {
        "case_id": case_id,
        "doc_id": doc["id"],
        "engine": res.get("engine"),
        "doc_type": res.get("doc_type"),
        "page_count": res.get("page_count", 0),
        "ocr_confidence": res.get("ocr_confidence", 0),
        "paragraphs": paras,
        "text_length": len(text),
        "clauses": clauses,
        "coverage": coverage,
        "meta": meta,
        "summary": summary,
        "suggestions": sug_map,
        "rules_version": rules_data.get("version"),
        "rules_loaded": [r["id"] for r in rules_data.get("rules", [])],
        "rule_diag": rule_diag,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    st.audit(case_id, "review", "pipeline.run_review", "ok",
             "审查完成：综合风险=%s 结论=%s" % (store.RISK_LABEL.get(risk),
                                            review_mod.CONCLUSION_LABEL.get(conclusion)))

    # ---- 4. 通知（异步，失败不影响审查结果）
    _notify_reviewed(nt, case, doc_row, rows, summary)
    return {"ok": True, "case_id": case_id, "risk_level": risk,
            "conclusion": conclusion, "hits": len(hits),
            "rule_diag": rule_diag}


def retry(cfg, st: store.Store, case_id: int):
    case = st.get_case(case_id)
    if not case:
        return {"ok": False, "error": "案件不存在"}
    st.audit(case_id, "retry", "pipeline.retry", "running",
             "管理员触发重新审查，上一状态=%s 阻塞原因=%s" % (
                 case.get("review_status"), case.get("blocked_reason") or "-"))
    st.update_case(case_id, review_status=store.S_PENDING, blocked_reason="",
                   writeback_status=store.WB_NOT_WRITTEN)
    out = run_review(cfg, st, case_id)
    return out


# ------------------------------------------------------------------ 详情聚合
def detail(cfg, st: store.Store, case_id: int):
    case = st.get_case(case_id)
    if not case:
        return None
    doc = st.case_document(case_id)
    rows = st.results(case_id)
    parsed = load_parsed(cfg, case_id) or {}
    coverage = parsed.get("coverage") or {"present": [], "missing": [],
                                          "coverage_rate": 0.0, "total_types": 0,
                                          "counts": {}}
    summary = parsed.get("summary") or output.build_summary(case, doc, rows, coverage)
    wb = st.writeback_list(case_id)
    comments, cerr = ([], "")
    if case.get("approval_instance"):
        comments, cerr = writeback.fetch_comments(cfg, case["approval_instance"])

    sug_map = parsed.get("suggestions") or {}
    results_vm = []
    for r in rows:
        vm = _result_vm(r)
        vm["suggestion_alternatives"] = (sug_map.get(r["rule_id"]) or {}).get(
            "alternatives", [])
        results_vm.append(vm)

    # 段落暴露上限：正常合同（几十到几百段）不受影响，异常超长文档不会打爆浏览器
    all_paras = parsed.get("paragraphs") or []
    cap = int(cfg.review.get("max_paragraphs_expose", 2000))
    truncated = len(all_paras) > cap
    para_view = all_paras[:cap]

    return {
        "case": _case_vm(case),
        "document": _doc_vm(doc),
        "results": results_vm,
        "summary": summary,
        "clauses": parsed.get("clauses") or [],
        "coverage": coverage,
        "paragraphs": para_view,
        "paragraphs_total": len(all_paras),
        "paragraphs_truncated": truncated,
        "rule_diag": parsed.get("rule_diag") or [],
        "meta": parsed.get("meta") or {},
        "audit": st.audit_list(case_id, limit=120),
        "writeback_logs": [_wb_vm(w) for w in wb],
        "approval_comments": comments,
        "approval_comments_error": cerr,
        "rules_version": parsed.get("rules_version"),
        "rules_loaded": parsed.get("rules_loaded") or [],
    }


def _case_vm(c):
    return {
        "id": c["id"], "case_no": c["case_no"], "title": c["title"],
        "applicant": c["applicant"], "send_dept": c["send_dept"],
        "business_type": c["business_type"],
        "business_type_label": output._btype_label(c["business_type"]),
        "counterparty": c["counterparty"], "credit_code": c["credit_code"],
        "amount": c["amount"], "amount_text": output._amount_text(c),
        "currency": c["currency"], "perform_period": c["perform_period"],
        "effective_condition": c["effective_condition"],
        "source": c["source"],
        "source_label": "审批系统拉取" if c["source"] == "approval" else "本地上传",
        "approval_instance": c["approval_instance"],
        "document_id": c["document_id"], "doc_sha256": c["doc_sha256"],
        "review_status": c["review_status"],
        "review_status_label": store.STATUS_LABEL.get(c["review_status"], c["review_status"]),
        "writeback_status": c["writeback_status"],
        "writeback_status_label": store.WB_LABEL.get(c["writeback_status"]),
        "risk_level": c["risk_level"],
        "risk_level_label": store.RISK_LABEL.get(c["risk_level"], c["risk_level"]),
        "conclusion": c["conclusion"],
        "conclusion_label": review_mod.CONCLUSION_LABEL.get(c["conclusion"], "-"),
        "blocked_reason": c["blocked_reason"],
        "lawyer_opinion": c["lawyer_opinion"],
        "created_at": c["created_at"], "updated_at": c["updated_at"],
    }


def _doc_vm(d):
    if not d:
        return None
    return {
        "id": d["id"], "filename": d["filename"], "doc_type": d["doc_type"],
        "parse_engine": d["parse_engine"], "parse_status": d["parse_status"],
        "parse_error": d["parse_error"], "page_count": d["page_count"],
        "para_count": d["para_count"], "char_count": d["char_count"],
        "ocr_confidence": d["ocr_confidence"], "need_manual": bool(d["need_manual"]),
        "sha256": d["sha256"], "created_at": d["created_at"],
    }


def _result_vm(r):
    return {
        "id": r["id"], "rule_id": r["rule_id"], "rule_name": r["rule_name"],
        "risk_level": r["risk_level"],
        "risk_level_label": store.RISK_LABEL.get(r["risk_level"], r["risk_level"]),
        "clause_type": r["clause_type"], "match_mode": r["match_mode"],
        "evidence": r["evidence"], "page_no": r["page_no"],
        "para_index": r["para_index"], "char_start": r["char_start"],
        "char_end": r["char_end"], "reason": r["reason"],
        "legal_basis": r["legal_basis"], "suggestion": r["suggestion"],
        "suggestion_final": r["suggestion_final"] or r["suggestion"],
        "adopted": bool(r["adopted"]), "lawyer_note": r["lawyer_note"],
    }


def _wb_vm(w):
    return {
        "id": w["id"], "mode": w["mode"], "channel": w["channel"],
        "status": w["status"], "status_label": store.WB_LABEL.get(w["status"]),
        "comment_id": w["comment_id"], "error": w["error"],
        "created_at": w["created_at"],
        "payload_chars": len(w["payload"] or ""),
    }


# ------------------------------------------------------------------ 报告
def build_report(cfg, st: store.Store, case_id: int, fmt: str = "md",
                 autoprint: bool = False):
    case = st.get_case(case_id)
    if not case:
        return None, None
    doc = st.get_document(case["document_id"]) if case.get("document_id") else None
    rows = st.results(case_id)
    parsed = load_parsed(cfg, case_id) or {}
    coverage = parsed.get("coverage") or {"present": [], "missing": [],
                                          "coverage_rate": 0.0, "total_types": 0}
    summary = parsed.get("summary") or output.build_summary(case, doc, rows, coverage)
    hits = [_result_vm(r) for r in rows]
    wb = st.writeback_list(case_id)
    wb_last = wb[0] if wb else None
    case_vm = _case_vm(case)

    if fmt == "md":
        text = output.build_report_md(case_vm, _doc_vm(doc), parsed.get("meta") or {},
                                      hits, summary, coverage,
                                      parsed.get("clauses") or [],
                                      case.get("lawyer_opinion") or "", wb_last)
        return "md", text

    html = output.build_report_html(case_vm, _doc_vm(doc), parsed.get("meta") or {},
                                    hits, summary, coverage,
                                    parsed.get("clauses") or [],
                                    case.get("lawyer_opinion") or "", wb_last,
                                    autoprint=autoprint)
    if fmt == "html":
        return "html", html

    # PDF：先落 HTML，再用系统浏览器无头通道打印
    rep_dir = cfg.path("reports")
    html_path = os.path.join(rep_dir, "case_%d.html" % case_id)
    pdf_path = os.path.join(rep_dir, "case_%d.pdf" % case_id)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    ok, info = output.html_to_pdf(html_path, pdf_path)
    if ok:
        st.audit(case_id, "report", "output.html_to_pdf", "ok",
                 "生成 PDF 报告：%s" % os.path.basename(pdf_path))
        return "pdf", pdf_path
    st.audit(case_id, "report", "output.html_to_pdf", "failed", info)
    return "pdf_fallback_html", html_path


def export_audit(cfg, st: store.Store):
    path = os.path.join(cfg.path("reports"),
                        "audit_export_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(st.dumps_export())
    return path
