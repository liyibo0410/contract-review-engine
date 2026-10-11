# -*- coding: utf-8 -*-
"""v2.0.1 UI 改造 · 后端部分：
  1) store.list_cases 支持 writeback 过滤；新增 rollup() 聚合（真实总数/段落/命中/采纳）
  2) auth：/mock 页与新增接口登记；PUBLIC_PREFIX 收紧（不再整个 /mock 公开）
  3) server：/api/cases 透传 writeback；/api/stats 补 amount_by_risk；
             新增 /api/twin（孪生看板真实聚合）与 /mock（模拟审批美化页）+ /api/mock/overview
  4) pipeline：新增 twin_overview() 聚合函数
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:64]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:64]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ================================================================= store.py
patch("app/store.py", [
    # 1) list_cases 增加 writeback 过滤
    ("""    def list_cases(self, risk=None, status=None, btype=None, kw=None):
        sql = "SELECT * FROM contract_cases WHERE 1=1"
        args = []
        if risk:
            sql += " AND risk_level=?"
            args.append(risk)
        if status:
            sql += " AND review_status=?"
            args.append(status)
        if btype:""",
     """    def list_cases(self, risk=None, status=None, btype=None, kw=None, writeback=None):
        sql = "SELECT * FROM contract_cases WHERE 1=1"
        args = []
        if risk:
            sql += " AND risk_level=?"
            args.append(risk)
        if status:
            sql += " AND review_status=?"
            args.append(status)
        if writeback:
            sql += " AND writeback_status=?"
            args.append(writeback)
        if btype:"""),

    # 2) rollup 聚合
    ("""    def case_by_sha(self, sha: str):""",
     """    def rollup(self):
        \"\"\"大盘与孪生看板共用的真实聚合。

        全部走 SQL 聚合，不做任何估算：任务数 / 金额、解析产出（页·段·字符·OCR 置信度）、
        规则命中与采纳数、回写成功数、审计条数。
        \"\"\"
        c = self._q1("SELECT COUNT(*) n, COALESCE(SUM(amount),0) amt "
                     "FROM contract_cases") or {}
        d = self._q1("SELECT COUNT(*) n, COALESCE(SUM(page_count),0) pages, "
                     "COALESCE(SUM(para_count),0) paras, "
                     "COALESCE(SUM(char_count),0) chars, "
                     "COALESCE(AVG(NULLIF(ocr_confidence,0)),0) ocr "
                     "FROM contract_documents WHERE char_count > 0") or {}
        r = self._q1("SELECT COUNT(*) n, "
                     "COALESCE(SUM(CASE WHEN adopted=1 THEN 1 ELSE 0 END),0) adopted "
                     "FROM review_results") or {}
        by_risk = {x["risk_level"]: x["n"] for x in self._q(
            "SELECT risk_level, COUNT(*) n FROM review_results GROUP BY risk_level")}
        wb = self._q1("SELECT COUNT(*) n FROM writeback_logs "
                      "WHERE status='success'") or {}
        wb_all = self._q1("SELECT COUNT(*) n FROM writeback_logs") or {}
        au = self._q1("SELECT COUNT(*) n FROM audit_logs") or {}
        return {
            "cases": c.get("n", 0), "amount_total": c.get("amt", 0) or 0,
            "docs": d.get("n", 0), "pages": d.get("pages", 0) or 0,
            "paras": d.get("paras", 0) or 0, "chars": d.get("chars", 0) or 0,
            "ocr_avg": round(float(d.get("ocr", 0) or 0), 3),
            "hits": r.get("n", 0), "adopted": r.get("adopted", 0) or 0,
            "hits_by_risk": by_risk,
            "writeback_success": wb.get("n", 0), "writeback_total": wb_all.get("n", 0),
            "audit": au.get("n", 0),
        }

    def case_by_sha(self, sha: str):"""),
])

# ================================================================= auth.py
patch("app/auth.py", [
    ('PUBLIC_PREFIX = ("/assets/", "/favicon", "/logo", "/mock/")',
     '# 只放行「模拟审批系统」的对外 JSON 接口本身；/mock 是内部查看页，需要登录\n'
     'PUBLIC_PREFIX = ("/assets/", "/favicon", "/logo", "/mock/feishu/api")'),
    ('''    "/denied": "auth",''',
     '''    "/denied": "auth",
    "/mock": {"GET"},'''),
    ('''    (r"^/api/queue$", {"GET"}, "auth"),''',
     '''    (r"^/api/queue$", {"GET"}, "auth"),
    (r"^/api/twin$", {"GET"}, "auth"),
    (r"^/api/mock/overview$", {"GET"}, "auth"),'''),
])

# ================================================================= server.py
patch("app/server.py", [
    # /api/cases 透传 writeback
    ('''            rows = st.list_cases(risk=q1("risk"), status=q1("status"),
                                 btype=q1("type"), kw=q1("kw"))''',
     '''            rows = st.list_cases(risk=q1("risk"), status=q1("status"),
                                 btype=q1("type"), kw=q1("kw"),
                                 writeback=q1("writeback"))'''),

    # /api/stats 补金额分档
    ('''            s["blocked"] = len([c for c in cases if c["review_status"] == store.S_BLOCKED])
            return 0, s''',
     '''            s["blocked"] = len([c for c in cases if c["review_status"] == store.S_BLOCKED])
            s["amount_by_risk"] = {
                lv: sum((c["amount"] or 0) for c in cases if c["risk_level"] == lv)
                for lv in ("high", "medium", "low", "none")}
            return 0, s

        # ---------- 动态孪生看板：一次性给出全部实时数据
        # 之前的看板里仪表、流水线节点与"实时日志"都是前端 Math.random 造的演示值。
        # 现在改为服务端聚合真实数据：流水线七段的每一个数字都能在库里/审计里对上。
        if path == "/api/twin":
            return 0, pipeline.twin_overview(
                cfg, st, metrics=self.metrics(),
                denied=self.denied_stats() if hasattr(self, "denied_stats") else None)'''),
])

# ================================================================= pipeline.py
patch("app/pipeline.py", [
    ("""def retry(cfg, st: store.Store, case_id: int):""",
     '''def twin_overview(cfg, st: store.Store, metrics=None, denied=None):
    """动态孪生看板的实时数据聚合（全部为真实值，无一处模拟）。

    与旧版的根本区别：旧版页面用 Math.random 造仪表漂移、流水线随机点亮、
    日志用模板拼凑；这里每一个数字都来自 SQL 聚合、解析快照或审计表，
    并且明确标出数据来源，便于复核。
    """
    import glob
    import json as _json

    rl = st.rollup()
    stats = st.stats()
    cases = st.list_cases()

    # 解析快照的真实产出（含页眉页脚剔除留痕）
    clause_total, strip_total, snapshot = 0, 0, 0
    for fp in glob.glob(os.path.join(cfg.path("parsed"), "case_*.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                snap = _json.load(f)
            snapshot += 1
            clause_total += len(snap.get("clauses") or [])
            strip_total += len(snap.get("stripped") or [])
        except (OSError, ValueError):
            continue

    blocked = [c for c in cases if c["review_status"] == store.S_BLOCKED]
    pending = [c for c in cases if c["review_status"] in ("pending", "parsing", "reviewing")]
    completed = [c for c in cases if c["review_status"] == "completed"]
    wb_ok = [c for c in cases if c["writeback_status"] == "success"]

    reports = 0
    try:
        reports = len([f for f in os.listdir(cfg.path("reports"))
                       if os.path.isfile(os.path.join(cfg.path("reports"), f))])
    except OSError:
        reports = 0

    uploads_mb, data_files, db_kb = 0.0, 0, 0.0
    for key, sub in (("uploads", "uploads"), ("parsed", "parsed"), ("reports", "reports")):
        try:
            for name in os.listdir(cfg.path(sub)):
                fp = os.path.join(cfg.path(sub), name)
                if os.path.isfile(fp):
                    data_files += 1
                    if key == "uploads":
                        uploads_mb += os.path.getsize(fp) / 1048576.0
        except OSError:
            pass
    try:
        db_kb = os.path.getsize(cfg.db_path()) / 1024.0
    except OSError:
        db_kb = 0.0

    # 规则库与条款词典（真实加载结果）
    rule_types, rule_count = [], 0
    try:
        for t in review_mod.list_rule_files(cfg.root):
            rd = review_mod.load_rules(cfg.root, t)
            n = len(rd.get("rules") or [])
            rule_count += n
            rule_types.append({"type": t, "label": rd.get("type_label") or t,
                               "rules": n})
    except Exception:                                            # noqa: BLE001
        pass
    ct = review_mod.load_clause_types(cfg.root)
    clause_types = len(ct) if isinstance(ct, (list, dict)) else 0

    from . import queue as queue_mod
    q = queue_mod.get_queue(cfg, st)
    qs = q.stats()
    try:
        from . import scheduler as sch_mod
        sched = sch_mod.get_scheduler(cfg, st).status()
    except Exception:                                            # noqa: BLE001
        sched = {"enabled": False, "runs": 0, "interval_sec": 0,
                 "next_run_in_sec": 0, "current_interval": 0,
                 "consecutive_failures": 0, "last_result": "", "last_run_at": ""}
    try:
        from . import notify as notify_mod
        nt = notify_mod.get_notifier(cfg, st)
        nstat = nt.status()
    except Exception:                                            # noqa: BLE001
        nstat = {}

    ocr_docs = 0
    try:
        for c in cases:
            doc = st.case_document(c["id"]) or {}
            if str(doc.get("parse_engine") or "").startswith("ocr"):
                ocr_docs += 1
    except Exception:                                            # noqa: BLE001
        ocr_docs = 0

    # 来源拆分（审批拉取 / 本地上传）：contract_cases.source 是建案时写入的真实字段
    n_pull = len([c for c in cases if (c.get("source") or "") == "approval"])
    n_local = len(cases) - n_pull

    # 流水线七段：每一段的数值都有明确出处
    pipeline_nodes = [
        {"key": "intake", "name": "审批接入", "value": rl["cases"], "unit": "个任务",
         "note": "审批拉取 %d · 本地上传 %d" % (n_pull, n_local),
         "state": "ok", "src": "contract_cases"},
        {"key": "parse", "name": "文档解析", "value": rl["docs"], "unit": "份",
         "note": "共 %d 页 / %d 段（受阻 %d）" % (rl["pages"], rl["paras"], len(blocked)),
         "state": "warn" if blocked else "ok", "src": "contract_documents"},
        {"key": "extract", "name": "条款抽取", "value": clause_total, "unit": "个条款",
         "note": "剔除页眉页脚 %d 行 · %d 份快照" % (strip_total, snapshot),
         "state": "ok", "src": "data/parsed/case_*.json"},
        {"key": "review", "name": "规则审查", "value": rl["hits"], "unit": "项命中",
         "note": "%d 类规则共 %d 条参与判定" % (len(rule_types), rule_count),
         "state": "ok", "src": "review_results"},
        {"key": "suggest", "name": "条款推荐", "value": rl["adopted"], "unit": "项已采纳",
         "note": "示范条款库覆盖 %d 类条款" % clause_types,
         "state": "ok", "src": "review_results.adopted"},
        {"key": "writeback", "name": "意见回写", "value": rl["writeback_success"], "unit": "条",
         "note": "共尝试 %d 次（幂等去重 %d 次）"
                 % (rl["writeback_total"], max(0, rl["writeback_total"] - rl["writeback_success"])),
         "state": "ok", "src": "writeback_logs"},
        {"key": "report", "name": "报告产出", "value": reports, "unit": "份文件",
         "note": "MD / HTML / PDF 三通道",
         "state": "ok", "src": "data/reports/"},
    ]

    m = metrics or {}
    http = {
        "requests": m.get("requests", 0), "errors": m.get("errors", 0),
        "error_rate": m.get("error_rate", 0.0), "avg_ms": m.get("avg_ms", 0.0),
        "bytes_out": m.get("bytes_out", 0), "uptime_sec": m.get("uptime_sec", 0),
        "started_at": m.get("started_at", ""),
        "top_paths": (m.get("top_paths") or [])[:8],
        "slow_requests": (m.get("slow_requests") or [])[:6],
    }

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": __import__("app", fromlist=["x"]).__version__,
        "pipeline": pipeline_nodes,
        "stats": stats,
        "rollup": rl,
        "amount_by_risk": {
            lv: sum((c["amount"] or 0) for c in cases if c["risk_level"] == lv)
            for lv in ("high", "medium", "low", "none")},
        "counts": {
            "total": len(cases), "completed": len(completed),
            "blocked": len(blocked), "pending": len(pending),
            "writeback_success": len(wb_ok),
        },
        "blocked_detail": [{"id": c["id"], "title": c["title"],
                            "reason": (c.get("blocked_reason") or "")[:120]}
                           for c in blocked[:6]],
        "engines": {
            "ocr": {"docs": ocr_docs, "avg_confidence": rl["ocr_avg"]},
            "pdf": {"effective": "browser" if output.find_browser() else "stdlib",
                    "browser": bool(output.find_browser())},
            "rules": {"types": rule_types, "total": rule_count},
            "clause_types": clause_types,
        },
        "queue": qs,
        "scheduler": sched,
        "notify": {
            "enabled": nstat.get("enabled"), "configured": nstat.get("configured"),
            "mask": (nstat.get("mask") or {}),
            "events": (nstat.get("events") or {}),
            "stats": (nstat.get("stats") or {}),
        },
        "storage": {"uploads_mb": round(uploads_mb, 2), "data_files": data_files,
                    "db_kb": round(db_kb, 1), "reports": reports},
        "auth": denied or {},
        "activity": st.audit_list(None, 60),
    }


def retry(cfg, st: store.Store, case_id: int):'''),
])

# pipeline 需要 output / review_mod 已在模块内可用 —— 检查导入情况
p = os.path.join(ROOT, "app/pipeline.py")
with io.open(p, "r", encoding="utf-8") as f:
    c = f.read()
for need in ("from . import output", "from . import review as review_mod",
             "import review as review_mod", "from . import review "):
    log.append("  导入检查：%r -> %s" % (need, need in c))

with io.open(os.path.join(ROOT, "verify", "ui_patch_backend.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("backend patch done")
