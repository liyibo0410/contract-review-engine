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

from . import auth
from . import output
from . import pipeline
from . import review as review_mod
from . import scheduler as scheduler_mod
from . import store
from . import writeback
from . import __version__

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
        # v2.0：鉴权拒绝计数（越权与未登录分开统计，便于看板区分「没登」还是「越权」）
        self._denied = {"unauthorized": 0, "forbidden": 0, "last": []}

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

    # ---------------------------------------------------------- 鉴权计数
    def note_denied(self, kind, detail):
        with self._m_lock:
            self._denied[kind] = self._denied.get(kind, 0) + 1
            self._denied["last"].append({"kind": kind, "detail": detail,
                                         "at": time.strftime("%H:%M:%S")})
            del self._denied["last"][:-15]

    def denied_stats(self):
        with self._m_lock:
            return {"unauthorized": self._denied["unauthorized"],
                    "forbidden": self._denied["forbidden"],
                    "recent": list(self._denied["last"])[::-1][:8]}

    # ---------------------------------------------------------- 静态资源
    def static(self, rel: str):
        """读取 ui/ 下的静态文件，返回 (数据, MIME, ETag)。

        多返回一个 ETag 是为了让静态资源支持**条件请求**：此前所有响应一律
        `Cache-Control: no-store`，意味着翻页时 app.css / fx.css / app.js /
        pet.js / charts.js / logo 每次都要重新下载（实测约 80 KB/次）。
        ETag 用「mtime + 大小」，并标成**弱校验**（W/）：同一文件在 gzip 与
        原样两种编码下字节不同但语义相同，弱校验正合此意。
        """
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith("..") or os.path.isabs(rel):
            return None, None, None
        path = os.path.join(self.cfg.root, STATIC_UI, rel)
        if not os.path.exists(path) or os.path.isdir(path):
            return None, None, None
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            data = f.read()
        try:
            st = os.stat(path)
            etag = 'W/"%x-%x"' % (int(st.st_mtime), st.st_size)
        except OSError:
            etag = None
        return data, ctype, etag

    # ---------------------------------------------------------- 队列快捷入口
    def _queue(self):
        from . import queue as queue_mod
        return queue_mod.get_queue(self.cfg, self.store)

    # ---------------------------------------------------------- API
    def api(self, method: str, path: str, query: dict, body, raw: bytes,
            session: dict | None = None, handler=None):
        st = self.store
        cfg = self.cfg
        q1 = lambda k, d=None: (query.get(k) or [d])[0] if isinstance(query.get(k), list) else (query.get(k, d))

        # ---------- 健康检查 / 概览
        if path == "/api/health":
            from . import ocr_win
            from . import pdf_min
            nt = pipeline.get_notifier(cfg, st)
            sch = scheduler_mod.get_scheduler(cfg, st)
            que = self._queue()
            return 0, {
                "app": "衡鉴 V2.1.7", "version": __version__,
                "root": cfg.root, "db": os.path.basename(cfg.db_path()),
                "approval_mode": cfg.approval.get("mode"),
                "approval_base": cfg.approval.get("base_url"),
                "approval_channel": writeback.channel_of(cfg),
                "llm_enabled": cfg.section("llm").get("enabled"),
                "browser_for_pdf": bool(output.find_browser()),
                "pdf_channels": {
                    "browser": bool(output.find_browser()),
                    "stdlib": bool(pdf_min),
                    "effective": "browser" if output.find_browser() else "stdlib",
                },
                "ocr_backend": ocr_win.backend_name() or "none",
                "gzip": bool(cfg.section("server").get("gzip", True)),
                "access_log": bool(cfg.section("server").get("access_log")),
                "auth": {
                    "enabled": bool(cfg.auth.get("enabled", True)),
                    "demo_switch": bool(cfg.auth.get("demo_switch", True)),
                    "roles": auth.ROLE_LABEL,
                    "accounts": len(st.user_list()),
                    "sessions": st.session_count(),
                    "denied": self.denied_stats(),
                },
                "scheduler": {"enabled": sch.enabled(),
                              "interval_sec": sch.base_interval(),
                              "runs": sch.status().get("runs", 0)},
                "queue": {"enabled": que.enabled(), "workers": que.workers,
                          "queued": que.stats().get("queued", 0)},
                "notify": nt.status(),
                "rules_types": review_mod.list_rule_files(cfg.root),
                "samples": sorted(os.listdir(cfg.path("samples")))
                if os.path.isdir(cfg.path("samples")) else [],
                "config_warnings": getattr(cfg, "warnings", []),
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        # ---------- 角色与权限（需求 2.4.2）
        if path == "/api/auth/me":
            me = session or {}
            role = me.get("role") or ""
            return 0, {
                "authenticated": bool(role),
                "username": me.get("username") or "",
                "display": me.get("display") or "",
                "role": role,
                "role_label": auth.ROLE_LABEL.get(role, ""),
                "role_desc": auth.ROLE_DESC.get(role, ""),
                "caps": auth.caps_of(role) if role else [],
                "caps_label": {c: auth.CAP_LABEL.get(c, c)
                               for c in (auth.caps_of(role) if role else [])},
                "session_expires_at": me.get("expires_at") or "",
                "demo_switch": bool(cfg.auth.get("demo_switch", True)),
                "roles": [{"role": r, "label": auth.ROLE_LABEL[r],
                           "desc": auth.ROLE_DESC[r]} for r in auth.ROLE_ORDER],
                "accounts_url": "/api/auth/accounts",
            }

        if path == "/api/auth/accounts":
            # 演示账号清单（不回传口令哈希）；仅管理员可看
            if (session or {}).get("role") != auth.ROLE_ADMIN:
                return 403, None, "仅系统管理员可查看账号列表"
            return 0, {"items": st.user_list(),
                       "roles": auth.ROLE_LABEL,
                       "sessions": st.session_count()}

        if path == "/api/auth/logout" and method == "POST":
            tok = (session or {}).get("token") or ""
            who = (session or {}).get("username") or ""
            return 0, auth.logout(st, tok, who)

        if path == "/api/auth/switch" and method == "POST":
            role = (body or {}).get("role") or ""
            ok, res = auth.switch_demo(cfg, st, role, client=auth.client_of(handler))
            if not ok:
                return 400, None, res.get("error") or "切换失败"
            # Cookie 只在响应头里下发（HttpOnly），不回吐到 JSON 正文
            res["cookie"] = auth.cookie_header(
                res["token"], int(res.get("ttl_sec") or 28800),
                bool(cfg.auth.get("cookie_secure")))
            return 0, res

        if path == "/api/auth/login" and method == "POST":
            username = (body or {}).get("username") or ""
            password = (body or {}).get("password") or ""
            ok, res = auth.login(cfg, st, username, password,
                                 client=auth.client_of(handler))
            if not ok:
                return 401, None, res.get("error") or "登录失败"
            res["cookie"] = auth.cookie_header(
                res["token"], int(res.get("ttl_sec") or 28800),
                bool(cfg.auth.get("cookie_secure")))
            return 0, res

        # ---------- AI 助手（页面宠物）
        # 三个接口都是只读：ask 只回答、tour 只给引导步骤、kb 只给常见问题。
        # 助手不做任何写操作，也不能替代人做审批决定 —— 它在代码层面就没有写权限路径。
        if path == "/api/assistant/ask":
            from . import assistant
            q = (body or {}).get("q") or (body or {}).get("question") or ""
            page = (body or {}).get("page") or "/"
            out = assistant.answer(cfg, st, q, page=page, sess=session)
            out["page"] = page
            st.audit(None, "assistant", "assistant.ask", "ok",
                     "助手问答（来源 %s，命中 %s）：%s"
                     % (out.get("source"), out.get("matched"),
                        (q or "")[:60].replace("\n", " ")))
            return 0, out

        if path == "/api/assistant/tour":
            from . import assistant
            page = q1("page") or "/"
            return 0, assistant.tour(page, session)

        if path == "/api/assistant/kb":
            from . import assistant
            return 0, {"chips": assistant.chips(),
                       "entries": len(assistant.KB),
                       "pages": list(assistant.TOURS.keys())}

        # ---------- AI 助手（页面宠物）
        # 三个接口都是只读：ask 只回答、tour 只给引导步骤、kb 只给常见问题。
        # 助手不做任何写操作，也不能替代人做审批决定 —— 它在代码层面就没有写权限路径。
        if path == "/api/assistant/ask":
            from . import assistant
            q = (body or {}).get("q") or (body or {}).get("question") or ""
            page = (body or {}).get("page") or "/"
            out = assistant.answer(cfg, st, q, page=page, sess=session)
            out["page"] = page
            st.audit(None, "assistant", "assistant.ask", "ok",
                     "助手问答（来源 %s，命中 %s）：%s"
                     % (out.get("source"), out.get("matched"),
                        (q or "")[:60].replace("\n", " ")))
            return 0, out

        if path == "/api/assistant/tour":
            from . import assistant
            page = q1("page") or "/"
            return 0, assistant.tour(page, session)

        if path == "/api/assistant/kb":
            from . import assistant
            return 0, {"chips": assistant.chips(),
                       "entries": len(assistant.KB),
                       "pages": list(assistant.TOURS.keys())}

        # ---------- 定时拉取调度（P1-01）
        if path == "/api/scheduler":
            sch = scheduler_mod.get_scheduler(cfg, st)
            return 0, sch.status()

        if path == "/api/scheduler/config" and method == "POST":
            sch = scheduler_mod.get_scheduler(cfg, st)
            b = body or {}
            out = sch.set_config(
                enabled=b.get("enabled"), interval_sec=_int_or_none(b.get("interval_sec")),
                max_interval_sec=_int_or_none(b.get("max_interval_sec")),
                backoff_factor=_float_or_none(b.get("backoff_factor")),
                jitter_sec=_int_or_none(b.get("jitter_sec")),
                auto_review=b.get("auto_review"))
            return 0, out

        if path == "/api/scheduler/run" and method == "POST":
            sch = scheduler_mod.get_scheduler(cfg, st)
            res = sch.trigger()
            return 0, {"trigger_result": res, "status": sch.status()}

        # ---------- 审查队列与配额（P1-04）
        if path == "/api/queue":
            q = self._queue()
            s = q.stats()
            s["mode_note"] = ("wait=false 时接口立即返回 task_id，案件状态可轮询 "
                              "/api/cases/{id} 获取")
            return 0, s

        # ---------- 通知事件开关（演示用，D-31 默认收敛）
        if path == "/api/notify/events" and method == "POST":
            nt = pipeline.get_notifier(cfg, st)
            out = nt.set_events((body or {}).get("events") or {})
            out["status"] = nt.status()
            return 0, out

        # ---------- 报告通道自检（M10 判据：服务端 PDF 直出）
        if path == "/api/pdf/check":
            from . import pdf_min
            probe = os.path.join(cfg.path("reports"), "_pdf_channel_probe.pdf")
            info = {"browser": bool(output.find_browser()),
                    "browser_path": output.find_browser() or "",
                    "stdlib": False, "size": 0, "error": ""}
            try:
                pdf_min.write(probe, [[("h1", "PDF 通道自检"),
                                       ("body", "这是一份由纯标准库通道生成的探针文件，"
                                                "用于验证服务端在无浏览器环境下仍可直出 PDF。")]],
                              title="服务端 PDF 通道自检",
                              meta_lines=["衡鉴 V2.1.7 ｜ 运行版本 %s" % __version__])
                info["stdlib"] = True
                info["size"] = os.path.getsize(probe)
                info["file"] = os.path.basename(probe)
            except Exception as exc:                             # noqa: BLE001
                info["error"] = str(exc)
            info["effective"] = "browser" if info["browser"] else (
                "stdlib" if info["stdlib"] else "none")
            return 0, info

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
            # 凭据可能来自环境变量（部署时只把凭据注入 systemd Environment，
            # config/app.json 里的 webhook/secret 保持为空），因此这里必须走
            # 环境变量优先的 configured()/creds()，不能直读 config，否则线上
            # 「发送测试通知」会永远返回未配置。
            if not nt.configured():
                return 400, None, (
                    "通知未启用或未配置 Webhook —— 请把 notify.enabled 设为 true，"
                    "并在 config/app.json 的 notify.webhook 或环境变量 %s 中提供机器人地址"
                    % (nt.conf().get("webhook_env") or "DINGTALK_WEBHOOK"))
            body = body or {}
            msg = body.get("message") or "这是一条来自衡鉴 V2.1.7的连通性测试消息。"
            res = nt.send_now("test", body.get("title") or "通知连通性测试", [
                "- 渠道：%s" % (nt.conf().get("channel") or "dingtalk"),
                "- 加签：%s" % ("已配置" if nt.creds()[1] else "未配置"),
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
                denied=self.denied_stats() if hasattr(self, "denied_stats") else None)

        # ---------- 案件列表
        if path == "/api/cases":
            rows = st.list_cases(risk=q1("risk"), status=q1("status"),
                                 btype=q1("type"), kw=q1("kw"),
                                 writeback=q1("writeback"))
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
                # wait=false 时立即入队并返回 task_id（并发演示用）；默认保持同步语义，
                # 现有调用方与自检的行为完全不变。
                wait = str((body or {}).get("wait", "true")).lower() not in ("false", "0")
                return 0, pipeline.submit_review(cfg, st, cid, wait=wait)
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
        if path == "/api/mock/overview":
            # 给「模拟审批系统」美化页用的聚合：待办单 + 每单的评论 + 本系统的处理结果。
            # 数据全部来自 mock 的 JSON 存储与主库，页面只负责展示，不造假。
            from mock import feishu as _fs
            insts = _fs.instances()
            by_inst = {c.get("approval_instance"): c for c in st.list_cases()}
            rows = []
            for it in insts:
                iid = it.get("instance_id") or ""
                c = by_inst.get(iid) or {}
                cm = _fs.comments(iid)
                rows.append({
                    "instance_id": iid,
                    "title": it.get("title") or "",
                    "applicant": it.get("applicant") or "",
                    "send_dept": it.get("send_dept") or "",
                    "business_type": it.get("business_type") or "",
                    "amount": it.get("amount") or 0,
                    "currency": it.get("currency") or "CNY",
                    "status": it.get("status") or "",
                    "submitted_at": it.get("submitted_at") or "",
                    "attachment": it.get("attachment") or "",
                    "comments": len(cm),
                    "comment_items": cm[-3:],
                    "linked": bool(c),
                    "case_id": c.get("id"),
                    "case_no": c.get("case_no") or "",
                    "risk_level": c.get("risk_level") or "",
                    "review_status": c.get("review_status") or "",
                    "writeback_status": c.get("writeback_status") or "",
                })
            return 0, {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "todo_count": len(_fs.todo()),
                "instances": rows,
                "fault_rate": _fs.fault_rate(),
                "endpoints": [
                    {"method": "GET", "path": "/mock/feishu/api/todo",
                     "desc": "拉取待办审批单列表"},
                    {"method": "GET", "path": "/mock/feishu/api/instances/{id}",
                     "desc": "读取单个审批单详情"},
                    {"method": "GET", "path": "/mock/feishu/api/instances/{id}/comments",
                     "desc": "读取审批单评论区"},
                    {"method": "POST", "path": "/mock/feishu/api/instances/{id}/comments",
                     "desc": "写入审批意见评论（本系统回写即调此接口）"},
                    {"method": "POST", "path": "/api/mock/fault",
                     "desc": "注入故障（要求管理员）"},
                    {"method": "POST", "path": "/api/mock/reset",
                     "desc": "重置演示数据（要求管理员）"},
                ],
            }

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


def _int_or_none(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ContractReview/2.0"
        protocol_version = "HTTP/1.1"
        # 读请求超时：HTTP/1.1 keep-alive 下，半开或失联的连接会一直占着工作线程，
        # 设一个上限让 socketserver 主动回收（超时后关闭连接而不是无限等待）。
        timeout = 30
        disable_nagle_algorithm = True

        def log_message(self, fmt, *args):                       # noqa: A003
            pass

        # 客户端提前断开（curl 管道被 head 截断、健康探针只读头部、keep-alive 被对端
        # 直接切断）会让 readline 抛 ConnectionResetError，并沿 socketserver 冒到
        # process_request_thread 打成一大段 traceback。对外提供的实例日志里不该出现
        # 这种噪音 —— 它既淹没有效日志，又让审计误以为服务在报错。这里就地吞掉。
        def handle_one_request(self):
            try:
                BaseHTTPRequestHandler.handle_one_request(self)
            except (ConnectionResetError, BrokenPipeError,
                    ConnectionAbortedError, TimeoutError):
                self.close_connection = True

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
            # 默认 no-store（接口与页面含登录态/角色信息，必须不落盘）；
            # 静态资源会显式传入可缓存策略覆盖它 —— 注意要 pop 掉，
            # 否则会发出两个 Cache-Control 头，浏览器合并后照样按 no-store 处理。
            cache = headers.pop("Cache-Control", None) or "no-store"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass
            self._resp_status = status
            self._resp_size = len(body)

        def _send_asset(self, body: bytes, ctype: str, etag: str):
            """静态资源（/assets/*）：带 ETag、可缓存但每次重验证，命中则回 304。

            为什么用 `no-cache` 而不是 `max-age=…`：这些文件名没有内容指纹，
            长缓存会在重新部署后让旧 CSS/JS 粘在浏览器里，很难向使用者解释。
            `no-cache` 的语义是「可以存，但每次必须问一句」—— 命中时回 304 空响应，
            省掉的正是整个响应体（app.css 16 KB、logo 23 KB），且永远不会过期失真。
            """
            if etag and self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache, must-revalidate")
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self._resp_status = 304
                self._resp_size = 0
                return
            return self._send(200, body, ctype,
                              {"ETag": etag,
                               "Cache-Control": "no-cache, must-revalidate"})

        def _json(self, code, data=None, msg="", status=200, extra_headers=None):
            body = json.dumps({"code": code, "data": data, "msg": msg},
                              ensure_ascii=False).encode("utf-8")
            self._send(status, body, extra_headers=extra_headers)

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
                # ---------- 鉴权闸门（需求 2.4.2）
                need = auth.resolve(method, path)

                # 「演示身份一键进入」是**登录的替代入口**，必须在未登录时就可达 ——
                # 否则会形成死锁：要先登录才能用免登录入口。旧版即在此被 401 拦下，
                # 表现为点角色按钮没反应、重定向后仍回到登录页。
                # 放行条件严格限定：仅 POST 该方法，且 auth.demo_switch 为真；
                # demo_switch 关闭时仍按普通受保护接口处理（失败关闭，不静默放行）。
                if (path == "/api/auth/switch" and method.upper() == "POST"
                        and bool(app.cfg.auth.get("demo_switch", True))):
                    need = None

                session = None
                if need is not None:
                    session = auth.current(app.cfg, app.store,
                                           self.headers.get("Cookie"))
                    if session is None:
                        app.note_denied("unauthorized",
                                        "未登录请求 %s %s" % (method, path))
                        app.store.audit(None, "auth", "auth.guard", "denied",
                                        "未登录请求 %s %s（来源 %s）"
                                        % (method, path, auth.client_of(self)))
                        if path.startswith("/api/"):
                            return self._json(401, None,
                                              "未登录或会话已过期，请先登录后再操作",
                                              status=401)
                        nxt = urllib.parse.quote(path + (
                            ("?" + parsed.query) if parsed.query else ""))
                        return self._redirect("/login?next=" + nxt)
                    if need != "auth" and not auth.has_cap(session["role"], need):
                        detail = auth.denial_detail(session["role"], need, method, path)
                        app.note_denied("forbidden", detail)
                        app.store.audit(None, "auth", "auth.guard", "denied", detail)
                        if path.startswith("/api/"):
                            return self._json(403, {
                                "role": session["role"],
                                "role_label": auth.ROLE_LABEL.get(session["role"], ""),
                                "required": need,
                                "required_label": auth.CAP_LABEL.get(need, need),
                                "allowed_roles": [{"role": r, "label": auth.ROLE_LABEL[r]}
                                                  for r in auth.ROLE_ORDER
                                                  if r in auth.CAPS.get(need, set())],
                            }, detail, status=403)
                        return self._denied_page(session, need, path)

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
                    result = app.api(method, path, query, body, raw,
                                     session=session, handler=self)
                    if result is None:
                        return self._json(404, None, "接口不存在")
                    if isinstance(result, tuple) and len(result) == 2:
                        try:
                            code, data = result
                        except Exception:                            # noqa: BLE001
                            return self._json(500, None, "内部错误")
                        data, extra = self._session_headers(path, data)
                        return self._json(code, data, extra_headers=extra)
                    if isinstance(result, tuple) and len(result) == 3:
                        code, data, msg = result
                        data, extra = self._session_headers(path, data)
                        return self._json(code, data, msg,
                                          status=_http_status(code), extra_headers=extra)
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

                # ---------- 静态页面
                if method != "GET":
                    return self._json(405, None, "方法不允许")
                if path == "/logout":
                    tok = (session or {}).get("token") or ""
                    if tok:
                        auth.logout(app.store, tok, (session or {}).get("username"))
                    return self._send(302, b"", "text/plain; charset=utf-8",
                                      {"Location": "/login?bye=1",
                                       "Set-Cookie": auth.clear_cookie_header()})
                page_map = {"/": "index.html", "/workbench": "workbench.html",
                            "/rules": "rules.html", "/report": "report.html",
                            "/twin": "twin.html", "/login": "login.html",
                            "/mock": "mock.html"}
                if path == "/login" and session is not None:
                    return self._redirect("/")
                rel = page_map.get(path) or path.lstrip("/")
                data, ctype, etag = app.static(rel)
                if data is None:
                    return self._send(404, b"404 Not Found",
                                      "text/plain; charset=utf-8")
                # /assets/* 走「可缓存 + 条件请求」；页面 HTML 继续 no-store。
                if path.startswith("/assets/") and etag:
                    return self._send_asset(data, ctype, etag)
                return self._send(200, data, ctype)
            except Exception as exc:                                # noqa: BLE001
                import traceback
                tb = traceback.format_exc()
                app.store.audit(None, "api", "server.dispatch", "failed",
                                "%s %s -> %s" % (method, path, exc))
                return self._json(500, {"traceback": tb.splitlines()[-6:]},
                                  "服务端异常：%s" % exc, status=500)

        # ---------------- 会话 Cookie 透传
        def _session_headers(self, path, data):
            """登录/切换/登出接口把 Cookie 从 JSON 里提出来放进响应头。

            Cookie 绝不出现在 JSON 正文里被前端 JS 读取（HttpOnly 的意义就在这），
            所以这里必须搬运到 Set-Cookie。
            """
            if not isinstance(data, dict) or "cookie" not in data:
                if path == "/api/auth/logout":
                    return data, {"Set-Cookie": auth.clear_cookie_header()}
                return data, None
            cookie = data.pop("cookie")
            data.pop("token", None)          # 令牌也不回吐给前端
            return data, {"Set-Cookie": cookie}

        def _redirect(self, location):
            return self._send(302, b"", "text/plain; charset=utf-8",
                              {"Location": location})

        def _denied_page(self, session, need, path):
            """权限受限页：说明「你是谁、缺什么权限、谁能做」，并给一键切换入口。"""
            role = (session or {}).get("role") or ""
            allow = [{"role": r, "label": auth.ROLE_LABEL[r]}
                     for r in auth.ROLE_ORDER if r in auth.CAPS.get(need, set())]
            data, _, _ = app.static("denied.html")
            if data is None:
                return self._json(403, None, auth.denial_detail(role, need, "GET", path),
                                  status=403)
            html = data.decode("utf-8").replace("{{ROLE_LABEL}}",
                                                auth.ROLE_LABEL.get(role, role))
            html = html.replace("{{ROLE}}", role)
            html = html.replace("{{USERNAME}}", (session or {}).get("username") or "")
            html = html.replace("{{PATH}}", path)
            html = html.replace("{{NEED_LABEL}}", auth.CAP_LABEL.get(need, need))
            html = html.replace("{{NEED}}", need)
            html = html.replace("{{ALLOW}}", "、".join(a["label"] for a in allow) or "-")
            html = html.replace("{{ALLOW_ROLES}}",
                                ",".join(a["role"] for a in allow))
            return self._send(403, html.encode("utf-8"), "text/html; charset=utf-8")

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

    boot(cfg, st)

    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd


def boot(cfg, st: store.Store):
    """启动期初始化（幂等）：账号种子 → 会话清理 → 审查队列 → 定时拉取。

    独立成函数，是为了让自检与线上巡检脚本能在不监听端口的前提下
    验证这三条 v2.0 新增能力的初始化过程。
    """
    auth.seed_users(cfg, st, warn=lambda m: None)
    purged = st.session_purge()
    from . import queue as queue_mod
    q = queue_mod.get_queue(cfg, st)
    q.start()
    sch = scheduler_mod.get_scheduler(cfg, st)
    if sch.enabled():
        sch.start()
    st.audit(None, "boot", "server.boot", "ok",
             "启动初始化完成：版本 %s ｜ 账号 %d ｜ 清理过期会话 %d ｜ 队列 %s(%d 线程) "
             "｜ 定时拉取 %s" % (
                 __version__, len(st.user_list()), purged,
                 "已启用" if q.enabled() else "已关闭", q.workers,
                 "已启用(%ds)" % sch.base_interval() if sch.enabled() else "已关闭"))
    return {"version": __version__, "accounts": len(st.user_list()),
            "purged_sessions": purged, "queue_workers": q.workers,
            "scheduler_enabled": sch.enabled()}
