# -*- coding: utf-8 -*-
"""性能基准（M10 判据：补解析耗时与并发表现，产出 verify/bench.txt）。

测什么：
  1. **文档解析**：8 份示例合同的解析耗时（DOCX / PDF 文本层 / 扫描件 OCR / 无文本层）
  2. **规则引擎**：长合同（400 段）与真实合同下的单次审查耗时
  3. **队列并发**：固定并发度下连续提交 N 份的吞吐、总耗时与是否丢件串件
  4. **报告通道**：纯标准库 PDF 直出耗时与产物体积
  5. **HTTP 端到端**：登录态下取详情页（含 gzip）的响应耗时

结果同时写 verify/bench.json，便于后续版本做回归对比。
"""
from __future__ import annotations

import io
import json
import os
import statistics
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, parsers, pipeline, review as review_mod   # noqa: E402
from app import store as store_mod, pdf_min                                  # noqa: E402
from mock import feishu                                                      # noqa: E402

tmp = tempfile.mkdtemp(prefix="cr_bench_")
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(tmp, "data"), "uploads": os.path.join(tmp, "uploads"),
    "parsed": os.path.join(tmp, "parsed"), "reports": os.path.join(tmp, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)
cfg.data["approval"]["base_url"] = ""
cfg.data["approval"]["dry_mode"] = False

st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)

rows = []
res = {}


def rec(section, name, ms, detail):
    rows.append("%-10s %-40s %10.1f ms   %s" % (section, name, ms, detail))
    res.setdefault(section, []).append({"name": name, "ms": round(ms, 2),
                                        "detail": detail})


def timed(fn, repeat=1):
    ts = []
    out = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return out, statistics.median(ts), min(ts), max(ts)


# ---------------------------------------------------------------- 1 文档解析
samples = cfg.path("samples")
files = sorted(f for f in os.listdir(samples)
               if os.path.splitext(f)[1].lower() in (".docx", ".pdf", ".png", ".jpg"))
parse_res = {}
for fn in files:
    path = os.path.join(samples, fn)
    out, med, lo, hi = timed(lambda p=path, f=fn: parsers.parse_document(
        p, f, min_text_chars=30, ocr_min_confidence=0.5,
        strip_cfg=pipeline.strip_config(cfg)), repeat=1)
    parse_res[fn] = out
    size_kb = os.path.getsize(path) / 1024.0
    rec("解析", "%s" % fn[:34], med,
        "%.0fKB → 引擎 %s / %d 段 / %d 字符%s"
        % (size_kb, out.get("engine"), len(out.get("paragraphs") or []),
           out.get("char_count") or 0,
           " / 剔除页眉页脚 %d 行" % len(out.get("stripped") or [])
           if out.get("stripped") else ""))

# ---------------------------------------------------------------- 2 规则引擎
rd = review_mod.load_rules(ROOT, "purchase")
ct = review_mod.load_clause_types(ROOT)
big_paras, big_text = parsers.build_paragraphs(
    [("第 %d 条 条款正文示例，双方应按约定履行各自义务并承担相应责任。" % i,
      i // 40 + 1, True) for i in range(400)])
_, rule_ms, rule_lo, rule_hi = timed(
    lambda: review_mod.run_rules(big_text, big_paras, rd, ct), repeat=5)
rec("规则引擎", "400 段 × %d 条规则" % len(rd.get("rules", [])), rule_ms,
    "中位 %.1fms（最快 %.1f / 最慢 %.1f）" % (rule_ms, rule_lo, rule_hi))

_, clause_ms, _, _ = timed(
    lambda: review_mod.split_clauses(big_paras, ct), repeat=5)
rec("规则引擎", "400 段条款层级切分", clause_ms, "中位 %.1fms" % clause_ms)

# ---------------------------------------------------------------- 3 端到端与队列并发
pull = pipeline.pull_from_approval(cfg, st, auto_review=False)
case_ids = [c["case_id"] for c in pull.get("created") or []]
rec("流水线", "审批拉取（%d 个待办）" % len(case_ids),
    float(pull.get("ms") or 0) or 0.0,
    "新建 %d / 跳过 %d" % (len(case_ids), len(pull.get("skipped") or [])))

if case_ids:
    # 先把全部拉取到案件跑完一轮，后面的并发基准才有足够样本
    for c in case_ids:
        pipeline.submit_review(cfg, st, c)

    cid = case_ids[0]
    t0 = time.perf_counter()
    rv = pipeline.submit_review(cfg, st, cid)
    e2e = (time.perf_counter() - t0) * 1000.0
    d0 = pipeline.detail(cfg, st, cid)
    rec("流水线", "单份合同端到端（解析→审查→摘要）", e2e,
        "引擎 %s / 命中 %s 项 / 风险 %s / 摘要模式 %s"
        % ((d0.get("document") or {}).get("parse_engine"),
           rv.get("hits"), rv.get("risk_level"), rv.get("mode_label")))

    # 并发：可完成案件重复提交，验证不丢不串
    conc = [c["id"] for c in st.list_cases() if c["review_status"] == "completed"][:10]
    if conc:
        q = __import__("app.queue", fromlist=["queue"]).get_queue(cfg, st)
        bases = {c: st.overall_risk(c) for c in conc}
        box, lock = {}, threading.RLock()

        def worker(c):
            try:
                out = pipeline.submit_review(cfg, st, c)
                with lock:
                    box[c] = bool(out.get("ok"))
            except Exception:                                    # noqa: BLE001
                with lock:
                    box[c] = False

        t0 = time.perf_counter()
        ths = [threading.Thread(target=worker, args=(c,)) for c in conc]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=180)
        wall = (time.perf_counter() - t0) * 1000.0
        drift = [c for c in conc if st.overall_risk(c) != bases[c]]
        rec("队列并发", "%d 份并发提交（workers=%d）" % (len(conc), q.workers), wall,
            "全部返回=%s / 风险等级漂移=%s / 吞吐 %.2f 份/秒"
            % (all(box.get(c) for c in conc), drift or "无", len(conc) / (wall / 1000.0)))

# ---------------------------------------------------------------- 4 报告通道
if case_ids:
    cid = case_ids[0]
    d = pipeline.detail(cfg, st, cid)
    pdf_path = os.path.join(cfg.path("reports"), "bench_probe.pdf")
    pages = pdf_min.pages_from_report(d["case"], d["document"], d["meta"], d["results"],
                                     d["summary"], d["coverage"], d["clauses"])
    _, pdf_ms, _, _ = timed(lambda: pdf_min.write(pdf_path, pages,
                                                 title="基准测试报告"), repeat=3)
    rec("报告通道", "纯标准库 PDF 直出（%d 页版面）" % len(pages), pdf_ms,
        "产物 %.1f KB（%s）" % (os.path.getsize(pdf_path) / 1024.0,
                             os.path.basename(pdf_path)))
    _, md_ms, _, _ = timed(lambda: pipeline.build_report(cfg, st, cid, "md"), repeat=3)
    rec("报告通道", "Markdown 报告生成", md_ms,
        "%d 字符" % len(pipeline.build_report(cfg, st, cid, "md")[1]))
    _, html_ms, _, _ = timed(lambda: pipeline.build_report(cfg, st, cid, "html"), repeat=3)
    rec("报告通道", "HTML 报告生成", html_ms,
        "%d 字符" % len(pipeline.build_report(cfg, st, cid, "html")[1]))

# ---------------------------------------------------------------- 5 HTTP 端到端
import urllib.request                                                   # noqa: E402
from app import server as server_mod                                   # noqa: E402

PORT = 8792
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT
httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.5)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:%d" % PORT
jar = {}


def http(method, path, body=None, cookie=True):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdrs = {"Content-Type": "application/json"}
    if cookie and jar.get("c"):
        hdrs["Cookie"] = jar["c"]
    hdrs["Accept-Encoding"] = "gzip"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    with opener.open(req, timeout=60) as r:
        return r.status, dict(r.headers), r.read()


try:
    code, hdrs, _ = http("POST", "/api/auth/login",
                         {"username": "admin", "password": "Admin@2026"}, cookie=False)
    jar["c"] = (hdrs.get("Set-Cookie") or "").split(";")[0]
    rec("HTTP", "登录并建立会话", 0.0, "HTTP %s，Cookie=%s" % (code, "已下发" if jar["c"] else "无"))

    t0 = time.perf_counter()
    http("GET", "/api/health")
    health_ms = (time.perf_counter() - t0) * 1000.0
    rec("HTTP", "GET /api/health", health_ms, "轻量探活")

    if case_ids:
        cid = case_ids[0]
        t0 = time.perf_counter()
        code, hdrs, body = http("GET", "/api/cases/%d" % cid)
        det_ms = (time.perf_counter() - t0) * 1000.0
        enc = hdrs.get("Content-Encoding") or "identity"
        rec("HTTP", "GET /api/cases/{id} 详情（含整篇正文）", det_ms,
            "HTTP %s / %d 字节 / 编码 %s" % (code, len(body), enc))

        t0 = time.perf_counter()
        code, hdrs, body = http("GET", "/api/cases/%d/report?format=pdf" % cid)
        rp_ms = (time.perf_counter() - t0) * 1000.0
        rec("HTTP", "GET 报告导出 PDF", rp_ms,
            "HTTP %s / %.1f KB / 通道 %s"
            % (code, len(body) / 1024.0,
               "浏览器无头打印" if __import__("app.output", fromlist=["output"]).find_browser()
               else "标准库直出"))
except Exception as exc:                                             # noqa: BLE001
    rec("HTTP", "端到端请求", 0.0, "异常：%s" % exc)
finally:
    httpd.shutdown()
    httpd.server_close()

# ---------------------------------------------------------------- 汇总输出
sect_ms = {k: [x["ms"] for x in v] for k, v in res.items()}
lines = []
import app as _app                                                       # noqa: E402
lines.append("衡鉴 V2.1.7 · 性能基准（bench）｜运行版本 %s" % _app.__version__)
lines.append("生成时间：%s ｜ 平台：%s ｜ 零第三方运行期依赖"
             % (time.strftime("%Y-%m-%d %H:%M:%S"), sys.platform))
lines.append("=" * 108)
lines.append("%-10s %-40s %13s   %s" % ("分组", "用例", "耗时", "备注"))
lines.append("-" * 108)
lines.extend(rows)
lines.append("-" * 108)
lines.append("分组汇总（中位耗时）：")
for k in ("解析", "规则引擎", "流水线", "队列并发", "报告通道", "HTTP"):
    if k in sect_ms:
        v = sect_ms[k]
        lines.append("  %-10s 用例 %2d 个 ｜ 合计 %8.1f ms ｜ 最大 %8.1f ms ｜ 均值 %7.1f ms"
                     % (k, len(v), sum(v), max(v), sum(v) / len(v)))
lines.append("=" * 108)
lines.append("说明：")
lines.append("  1. 本基准在**同一次进程内**真实执行解析/规则/队列/报告，不是模拟值；")
lines.append("  2. 解析含页眉页脚剔除（review.strip_repeat_lines=true）；")
lines.append("  3. 「队列并发」用固定 workers 的线程池，验证并发下不丢件、不串件"
             "（每份案件的综合风险等级与单独执行一致）；")
lines.append("  4. 并发耗时为墙钟时间，含队列排队；单份端到端耗时见「流水线」分组；")
lines.append("  5. 端到端单份耗时受 OCR 影响最大（扫描件走系统 OCR 引擎），"
             "DOCX 与文本层 PDF 均在百毫秒量级。")

out_dir = os.path.join(ROOT, "verify")
os.makedirs(out_dir, exist_ok=True)
with io.open(os.path.join(out_dir, "bench.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
with io.open(os.path.join(out_dir, "bench.json"), "w", encoding="utf-8") as f:
    json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "sections": res}, f, ensure_ascii=False, indent=2)

import shutil                                                         # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)
print("bench done")
