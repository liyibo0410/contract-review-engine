# -*- coding: utf-8 -*-
"""HTTP 服务与路由（零第三方依赖，标准库 http.server）

统一风格：所有接口返回 JSON：
    {"code": 0, "data": {...}, "msg": ""}  成功
    {"code": <非0>, "data": null, "msg": "..."} 失败
"""
from __future__ import annotations

import base64
import gzip
import json
import mimetypes
import os
import posixpath
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import output
from . import pipeline
from . import review as review_mod
from . import store
from . import writeback

STATIC_UI = "ui"
_START_TS = time.time()


class App:
    def __init__(self, cfg, st: store.Store):
        self.cfg = cfg
        self.store = st
        self.lock = threading.RLock()
        # 轻量运行指标：不引第三方库，够用即可（QPS、错误率、慢请求、热点接口）
        self._m_lock = threading.RLock()
        self._requests = 0
        self._errors = 0
        self._bytes_out = 0
        self._per_path = {}
        self._slow = []

    # ---------------------------------------------------------- 运行指标
    def record(self, method, path, status, elapsed_ms, size):
        with self._m_lock:
            self._requests += 1
            if status >= 400:
                self._errors += 1
            self._bytes_out += size
            key = "%s %s" % (method, path)
            ent = self._per_path.setdefault(
                key, {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0})
            ent["count"] += 1
            if status >= 400:
                ent["errors"] += 1
            ent["total_ms"] += elapsed_ms
            ent["max_ms"] = max(ent["max_ms"], elapsed_ms)
            if elapsed_ms >= 1000:
                self._slow.append({"path": key, "ms": int(elapsed_ms),
                                   "at": time.strftime("%H:%M:%S")})
                del self._slow[:-20]

    def metrics(self):
        with self._m_lock:
            top = sorted(self._per_path.items(),
                         key=lambda kv: -kv[1]["total_ms"])[:12]
            return {
                "uptime_sec": int(time.time() - _START_TS),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_START_TS)),
                "requests": self._requests,
                "errors": self._errors,
                "error_rate": round(self._errors / self._requests, 4) if self._requests else 0.0,
                "bytes_out": self._bytes_out,
                "avg_ms": round(
                    sum(v["total_ms"] for v in self._per_path.values()) / self._requests, 1)
                if self._requests else 0.0,
                "slow_requests": list(self._slow)[::-1][:10],
                "top_paths": [
                    {"path": k, "count": v["count"], "errors": v["errors"],
                     "avg_ms": round(v["total_ms"] / v["count"], 1),
                     "max_ms": round(v["max_ms"], 1)}
                    for k, v in top],
            }

    def access_log_line(self, method, path, status, elapsed_ms, size):
        line = '%s - "%s %s" %s %d %.0fms\n' % (
            time.strftime("%Y-%m-%d %H:%M:%S"), method, path, status, size, elapsed_ms)
        try:
            with open(os.path.join(self.cfg.path("data"), "access.log"), "a",
                      encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    # ---------------------------------------------------------- 静态资源
    def static(self, rel: str):
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith("..") or os.path.isabs(rel):
            return None, None
        path = os.path.join(self.cfg.root, STATIC_UI, rel)
        if not os.path.exists(path) or os.path.isdir(path):
            return None, None
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            return f.read(), ctype

    # ---------------------------------------------------------- API
    def api(self, method: str, path: str, query: dict, body, raw: bytes):
        st = self.store
        cfg = self.cfg
        q1 = lambda k, d=None: (query.get(k) or [d])[0] if isinstance(query.get(k), list) else (query.get(k, d))

        # ---------- 健康检查 / 概览
        if path == "/api/health":
            from . import ocr_win
            nt = pipeline.get_notifier(cfg, st)
            return 0, {
                "app": "合同智能审查系统", "version": "1.0.0",
                "root": cfg.root, "db": os.path.basename(cfg.db_path()),
                "approval_mode": cfg.approval.get("mode"),
                "approval_base": cfg.approval.get("base_url"),
                "approval_channel": writeback.channel_of(cfg),
                "llm_enabled": cfg.section("llm").get("enabled"),
                "browser_for_pdf": bool(output.find_browser()),
                "ocr_backend": ocr_win.backend_name() or "none",
                "gzip": bool(cfg.section("server").get("gzip", True)),
                "access_log": bool(cfg.section("server").get("access_log")),
                "notify": nt.status(),
                "rules_types": review_mod.list_rule_files(cfg.root),
                "samples": sorted(os.listdir(cfg.path("samples")))
                if os.path.isdir(cfg.path("samples")) else [],
                "config_warnings": getattr(cfg, "warnings", []),
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        # ---------- 前端可见配置（不含密钥）
        if path == "/api/config":
            return 0, cfg.public()

        # ---------- 运行指标
        if path == "/api/metrics":
            m = self.metrics()
            m["db"] = os.path.basename(cfg.db_path())
            m["task_total"] = len(st.list_cases())
            return 0, m

        # ---------- 通知（钉钉机器人）
        if path == "/api/notify/status":
            return 0, pipeline.get_notifier(cfg, st).status()

        if path == "/api/notify/recent":
            limit = int(q1("limit", 20) or 20)
            return 0, {"items": pipeline.get_notifier(cfg, st).recent(limit)}

        if path == "/api/notify/test" and method == "POST":
            nt = pipeline.get_notifier(cfg, st)
            if not (nt.conf().get("enabled") and nt.conf().get("webhook")):
                return 400, None, ("通知未启用或未配置 Webhook —— "
                                   "请在 config/app.json 的 notify 中填写 enabled 与 webhook")
            body = body or {}
            msg = body.get("message") or "这是一条来自合同智能审查系统的连通性测试消息。"
            res = nt.send_now("test", body.get("title") or "通知连通性测试", [
                "- 渠道：%s" % nt.conf().get("channel"),
                "- 加签：%s" % ("已配置" if nt.conf().get("secret") else "未配置"),
                "- 消息：%s" % msg,
                "",
                "收到这条消息说明钉钉机器人配置正确。",
            ])
            if res.get("status") == "sent":
                return 0, res
            return 500, res, "测试通知发送失败：%s" % res.get("detail", "")

        if path == "/api/stats":
            s = st.stats()
            cases = st.list_cases()
            highs = [c for c in cases if c["risk_level"] == "high"]
            s["high_risk_amount"] = sum((c["amount"] or 0) for c in highs)
            s["amount_total"] = sum((c["amount"] or 0) for c in cases)
            s["blocked"] = len([c for c in cases if c["review_status"] == store.S_BLOCKED])
            return 0, s

        # ---------- 案件列表
        if path == "/api/cases":
            rows = st.list_cases(risk=q1("risk"), status=q1("status"),
                                 btype=q1("type"), kw=q1("kw"))
            return 0, {"items": [pipeline._case_vm(c) for c in rows],
                       "total": len(rows)}

        # ---------- 拉取待办（审批触发自动审查）
        if path == "/api/pull":
            auto = str((body or {}).get("auto_review", True)).lower() not in ("false", "0")
            return 0, pipeline.pull_from_approval(cfg, st, auto_review=auto)

        # ---------- 本地上传
        if path == "/api/upload":
            body = body or {}
            filename = body.get("filename") or "contract.bin"
            content = body.get("content") or ""
            try:
                data = base64.b64decode(content)
            except Exception:                                  # noqa: BLE001
                return 400, None, "文件内容不是合法的 base64"
            if not data:
                return 400, None, "文件内容为空"
            if len(data) > int(cfg.review.get("max_upload_mb", 30)) * 1024 * 1024:
                return 400, None, "文件大小 %.1f MB 超过 %d MB 上限" % (
                    len(data) / 1048576.0, int(cfg.review.get("max_upload_mb", 30)))
            out = pipeline.ingest_upload(cfg, st, filename, data,
                                        meta=body.get("meta") or {},
                                        auto_review=body.get("auto_review", True))
            if not out.get("ok"):
                return 400, out, out.get("error") or "上传失败"
            return 0, out

        # ---------- 示例合同集
        if path == "/api/samples":
            with open(os.path.join(cfg.root, "config", "samples.json"),
                      "r", encoding="utf-8") as f:
                return 0, json.load(f)

        # ---------- 案件详情 / 动作（/report 交由下方报告路由处理，此处排除）
        m = re.match(r"^/api/cases/(\d+)(?:/(?!report\b)(\w+))?$", path)
        if m:
            cid = int(m.group(1))
            action = m.group(2)
            if method == "GET" and not action:
                d = pipeline.detail(cfg, st, cid)
                if not d:
                    return 404, None, "案件不存在"
                return 0, d
            if method == "POST" and action == "review":
                return 0, pipeline.run_review(cfg, st, cid)
            if method == "POST" and action == "retry":
                return 0, pipeline.retry(cfg, st, cid)
            if method == "POST" and action == "opinion":
                if not st.get_case(cid):
                    return 404, None, "案件不存在"
                text = (body or {}).get("opinion") or ""
                if len(text) > 20000:
                    return 400, None, "法务批注过长（上限 20000 字）"
                st.update_case(cid, lawyer_opinion=text)
                st.audit(cid, "collab", "store.update_case", "ok",
                         "法务批注已保存（%d 字）" % len(text))
                return 0, {"ok": True, "opinion": text}
            if method == "POST" and action == "writeback":
                case = st.get_case(cid)
                if not case:
                    return 404, None, "案件不存在"
                if case["review_status"] != store.S_COMPLETED:
                    return 409, None, "合同尚未完成审查，无法回写审查意见"
                d = pipeline.detail(cfg, st, cid)
                text = output.compose_comment(
                    d["case"], d["document"], d["results"], d["summary"],
                    d["meta"], case.get("lawyer_opinion") or "",
                    d["summary"].get("summary_source", "rule"))
                mode = (body or {}).get("mode") or (
                    "dry_run" if (cfg.get("approval") or {}).get("dry_run_default") else "live")
                case_vm = d["case"]
                res = writeback.send(cfg, st, case, text, mode=mode)
                res["comment_text"] = text
                return 0, res
            return 404, None, "接口不存在"

        # ---------- 审查结果（法务复核）
        m = re.match(r"^/api/results/(\d+)$", path)
        if m:
            rid = int(m.group(1))
            r = st.get_result(rid)
            if not r:
                return 404, None, "风险项不存在"
            if method in ("POST", "PATCH"):
                body = body or {}
                upd = {}
                if "suggestion_final" in body:
                    upd["suggestion_final"] = body["suggestion_final"]
                if "adopted" in body:
                    upd["adopted"] = 1 if body["adopted"] else 0
                if "lawyer_note" in body:
                    upd["lawyer_note"] = body["lawyer_note"]
                if upd:
                    st.update_result(rid, **upd)
                    st.audit(r["case_id"], "collab", "review_results.update", "ok",
                             "规则 %s 复核更新：%s" % (r["rule_id"],
                                                   "、".join(upd.keys())))
                return 0, pipeline._result_vm(st.get_result(rid))
            return 0, pipeline._result_vm(r)

        # ---------- 报告导出
        m = re.match(r"^/api/cases/(\d+)/report$", path)
        if m:
            cid = int(m.group(1))
            fmt = q1("format", "md")
            download = q1("download") in ("1", "true", "yes")
            case = st.get_case(cid)
            if not case:
                return 404, None, "案件不存在"
            kind, payload = pipeline.build_report(cfg, st, cid, fmt,
                                                 autoprint=(q1("print") in ("1", "true")))
            if payload is None:
                return 404, None, "案件不存在"
            if kind == "md":
                text = payload
                if download:
                    name = "审查意见报告_%s.md" % case["case_no"]
                    return ("RAW", text.encode("utf-8"),
                            "text/markdown; charset=utf-8",
                            "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name))
                return 0, {"format": "md", "content": text,
                           "filename": "审查意见报告_%s.md" % case["case_no"]}
            if kind == "html":
                return ("RAW", payload.encode("utf-8"), "text/html; charset=utf-8", "")
            if kind == "pdf":
                with open(payload, "rb") as f:
                    data = f.read()
                name = "审查意见报告_%s.pdf" % case["case_no"]
                return ("RAW", data, "application/pdf",
                        "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name))
            # pdf 失败 → 返回可打印 HTML，由浏览器另存为 PDF
            html = pipeline.build_report(cfg, st, cid, "html", autoprint=True)[1]
            st.audit(cid, "report", "output.html_to_pdf", "fallback",
                     "服务端 PDF 通道不可用，已降级为浏览器打印导出")
            return ("RAW", html.encode("utf-8"), "text/html; charset=utf-8",
                    "X-Report-Fallback: 1")

        # ---------- 规则库
        if path == "/api/rules":
            btype = q1("type", "purchase")
            data = review_mod.load_rules(cfg.root, btype)
            ct = review_mod.load_clause_types(cfg.root)
            lib = review_mod.load_clause_library(cfg.root)
            return 0, {"types": review_mod.list_rule_files(cfg.root),
                       "current": btype, "rules": data,
                       "clause_types": ct,
                       "library": lib,
                       "stats": _rule_stats(data)}

        m = re.match(r"^/api/rules/([a-z_]+)$", path)
        if m and method in ("POST", "PUT"):
            btype = m.group(1)
            if btype not in review_mod.list_rule_files(cfg.root):
                return 400, None, "未知合同类型：%s" % btype
            body = body or {}
            if "rules" not in body:
                return 400, None, "缺少 rules 字段"
            data = review_mod.load_rules(cfg.root, btype)
            data["rules"] = body["rules"]
            data["version"] = body.get("version") or data.get("version") or "1.0"
            path_saved = review_mod.save_rules(cfg.root, btype, data)
            st.audit(None, "rules", "review.save_rules", "ok",
                     "更新规则库 %s（%d 条规则），已备份至 %s.bak" % (
                         btype, len(data["rules"]), os.path.basename(path_saved)))
            return 0, {"ok": True, "saved": os.path.basename(path_saved),
                       "count": len(data["rules"]), "stats": _rule_stats(data)}

        # ---------- 审计 / 导出
        if path == "/api/audit":
            cid = q1("case_id")
            return 0, {"items": st.audit_list(int(cid) if cid else None,
                                             limit=int(q1("limit", 200) or 200))}

        if path == "/api/export":
            p = pipeline.export_audit(cfg, st)
            return 0, {"file": os.path.basename(p), "path": p,
                       "cases": len(st.list_cases())}

        # ---------- 模拟审批系统
        if path == "/api/mock/comments":
            iid = q1("instance_id") or ""
            items, err = writeback.fetch_comments(cfg, iid)
            return 0, {"items": items, "error": err}
        if path == "/api/mock/fault":
            from mock import feishu
            return 0, {"fault_rate": feishu.set_fault_rate(
                float((body or {}).get("fault_rate", 0)))}
        if path == "/api/mock/reset":
            from mock import feishu
            if q1("full") in ("1", "true"):
                st._x("DELETE FROM contract_cases")
                st._x("DELETE FROM contract_documents")
                st._x("DELETE FROM review_results")
                st._x("DELETE FROM audit_logs")
                st._x("DELETE FROM writeback_logs")
                st._x("DELETE FROM sqlite_sequence WHERE name IN "
                      "('contract_cases','contract_documents','review_results',"
                      "'audit_logs','writeback_logs')")
                # 解析结果文件同样清掉，否则旧案件的段落树会残留在磁盘上
                pd = cfg.path("parsed")
                for fn in os.listdir(pd):
                    if fn.endswith(".json"):
                        try:
                            os.remove(os.path.join(pd, fn))
                        except OSError:
                            pass
                pipeline._PARSED_CACHE.clear()
            feishu.reset()
            return 0, {"ok": True}

        # ---------- 自检
        if path == "/api/selftest":
            try:
                import selftest
                return 0, selftest.run(cfg.root)
            except Exception as exc:                            # noqa: BLE001
                return 500, None, "自检执行失败：%s" % exc

        return 404, None, "接口不存在：%s" % path


def _rule_stats(data):
    rules = data.get("rules", [])
    return {
        "count": len(rules),
        "high": len([r for r in rules if r.get("risk_level") == "high"]),
        "medium": len([r for r in rules if r.get("risk_level") == "medium"]),
        "low": len([r for r in rules if r.get("risk_level") == "low"]),
        "types": sorted({r.get("clause_type") for r in rules}),
        "modes": sorted({r.get("mode") for r in rules}),
    }


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ContractReview/1.0"
        protocol_version = "HTTP/1.1"
        # 读请求超时：HTTP/1.1 keep-alive 下，半开或失联的连接会一直占着工作线程，
        # 设一个上限让 socketserver 主动回收（超时后关闭连接而不是无限等待）。
        timeout = 30
        disable_nagle_algorithm = True

        def log_message(self, fmt, *args):                       # noqa: A003
            pass

        # ---------------- 输出工具
        def _accepts_gzip(self):
            return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

        def _send(self, status, body: bytes, ctype="application/json; charset=utf-8",
                  extra_headers=None):
            headers = dict(extra_headers or {})
            # 大响应压缩：详情页含整篇正文，未压缩时动辄数百 KB
            try:
                use_gzip = bool(app.cfg.get("server", {}).get("gzip", True))
            except Exception:                                    # noqa: BLE001
                use_gzip = True
            if (use_gzip and len(body) >= 1024 and self._accepts_gzip()
                    and not ctype.startswith(("image/", "application/pdf"))):
                gz = gzip.compress(body, 6)
                if len(gz) < len(body):
                    body = gz
                    headers["Content-Encoding"] = "gzip"
                    headers.setdefault("Vary", "Accept-Encoding")
            headers.setdefault("X-Content-Type-Options", "nosniff")
            headers.setdefault("Referrer-Policy", "same-origin")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass
            self._resp_status = status
            self._resp_size = len(body)

        def _json(self, code, data=None, msg="", status=200):
            body = json.dumps({"code": code, "data": data, "msg": msg},
                              ensure_ascii=False).encode("utf-8")
            self._send(status, body)

        def _read_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return None, b""
            ctype = self.headers.get("Content-Type") or ""
            if "application/json" in ctype or raw.lstrip()[:1] in (b"{", b"["):
                try:
                    return json.loads(raw.decode("utf-8")), raw
                except Exception:                                # noqa: BLE001
                    return None, raw
            return None, raw

        # ---------------- 分发（带指标采集：耗时、状态码、响应体大小、慢请求）
        def _handle(self, method):
            t0 = time.time()
            try:
                self._dispatch(method)
            finally:
                ms = (time.time() - t0) * 1000.0
                status = getattr(self, "_resp_status", 500)
                size = getattr(self, "_resp_size", 0)
                try:
                    app.record(method, urllib.parse.urlparse(self.path).path,
                               status, ms, size)
                    if app.cfg.section("server").get("access_log"):
                        app.access_log_line(method, self.path, status, ms, size)
                except Exception:                                # noqa: BLE001
                    pass

        def _dispatch(self, method):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            body, raw = self._read_body()

            try:
                # 模拟审批系统（按真实平台形态暴露 HTTP 接口）
                if path.startswith("/mock/feishu/api"):
                    from mock import feishu
                    code, payload = feishu.handle(method, path, body)
                    return self._send(code, json.dumps(payload, ensure_ascii=False)
                                      .encode("utf-8"))

                if path.startswith("/api/"):
                    if path == "/api/upload" and raw and not body:
                        # 兼容 curl 直传二进制：?filename=xxx
                        fn = (query.get("filename") or ["upload.bin"])[0]
                        out = pipeline.ingest_upload(
                            app.cfg, app.store, fn, raw,
                            meta={"business_type": (query.get("type") or ["purchase"])[0]},
                            auto_review=True)
                        return self._json(0, out)
                    result = app.api(method, path, query, body, raw)
                    if result is None:
                        return self._json(404, None, "接口不存在")
                    if isinstance(result, tuple) and len(result) == 2:
                        try:
                            code, data = result
                        except Exception:                            # noqa: BLE001
                            return self._json(500, None, "内部错误")
                        return self._json(code, data)
                    if isinstance(result, tuple) and len(result) == 3:
                        code, data, msg = result
                        return self._json(code, data, msg, status=_http_status(code))
                    if isinstance(result, tuple) and len(result) == 4:
                        kind, payload, ctype, disp = result
                        headers = {}
                        if disp:
                            if disp.startswith("X-"):
                                k, _, v = disp.partition(": ")
                                headers[k] = v
                            else:
                                headers["Content-Disposition"] = disp
                        return self._send(200, payload, ctype, headers)
                    return self._json(500, None, "内部错误")

                # 静态页面
                if method != "GET":
                    return self._json(405, None, "方法不允许")
                page_map = {"/": "index.html", "/workbench": "workbench.html",
                            "/rules": "rules.html", "/report": "report.html"}
                rel = page_map.get(path) or path.lstrip("/")
                data, ctype = app.static(rel)
                if data is None:
                    return self._send(404, b"404 Not Found",
                                      "text/plain; charset=utf-8")
                return self._send(200, data, ctype)
            except Exception as exc:                                # noqa: BLE001
                import traceback
                tb = traceback.format_exc()
                app.store.audit(None, "api", "server.dispatch", "failed",
                                "%s %s -> %s" % (method, path, exc))
                return self._json(500, {"traceback": tb.splitlines()[-6:]},
                                  "服务端异常：%s" % exc, status=500)

        def do_GET(self):                                        # noqa: N802
            self._handle("GET")

        def do_POST(self):                                       # noqa: N802
            self._handle("POST")

        def do_PUT(self):                                        # noqa: N802
            self._handle("PUT")

        def do_PATCH(self):                                      # noqa: N802
            self._handle("PATCH")

        def do_DELETE(self):                                     # noqa: N802
            self._handle("DELETE")

    return Handler


def _http_status(code):
    return {0: 200, 400: 400, 404: 404, 409: 409, 500: 500}.get(code, 200)


def serve(cfg, st: store.Store):
    app = App(cfg, st)
    host = (cfg.get("server") or {}).get("host", "127.0.0.1")
    port = int((cfg.get("server") or {}).get("port", 8770))

    # 内置的模拟审批系统与主服务同端口提供接口。换了监听端口后必须把自调用地址
    # 对齐到实际端口，否则回写/拉取会打到旧端口并被拒绝（Connection refused）。
    ap = cfg.data.setdefault("approval", {})
    if (ap.get("mode") or "mock") == "mock":
        ap["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % port

    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
