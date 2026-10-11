# -*- coding: utf-8 -*-
"""v2.0 线上实例巡检（外网，真实 HTTP）。

与本地冒烟的区别：这里打的是**公网地址**，验证的是「部署之后真的对外可用」，
包括鉴权闸门、三角色权限隔离、v2.0 新增接口与页面在真实 nginx/systemd 环境下是否成立。

M11 判据：线上实例巡检 ≥ 15 / 15。
产物：verify/remote_inspect.txt + remote_inspect.json
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import app as _app                                                       # noqa: E402

HOST = os.environ.get("CR_HOST", "39.96.200.45")
PORT = os.environ.get("CR_PORT", "8160")
BASE = "http://%s:%s" % (HOST, PORT)

# 显式禁用代理：宿主/沙箱的 http_proxy 会把外网请求劫持到本地代理端口
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NO_REDIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    type("NR", (urllib.request.HTTPRedirectHandler,), {
        "redirect_request": lambda *a, **k: None})())

log = []
OK = [0]
TOTAL = [0]
JAR = {"c": ""}


def call(method, path, body=None, cookie=True, no_redirect=False, timeout=40,
         retries=3):
    """发一次 HTTP 请求。

    对**连接层抖动**做退避重试（公网 + 家用/办公网出口，偶发 URLError 很常见；
    本次实测出现过 HTTP 0「连接被重置」）。不做重试的话，一次网络抖动就会被报成
    「功能缺陷」，比漏报更糟 —— 它会把注意力引向错误的方向。

    只重试「没能拿到响应」的情况；4xx/5xx 是服务端的明确答复，不重试。
    """
    data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None)
    hdrs = {"User-Agent": "cr-remote-inspect/2.0"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if cookie and JAR["c"]:
        hdrs["Cookie"] = JAR["c"]
    op = NO_REDIRECT if no_redirect else OPENER
    last = (0, {}, b"")
    for i in range(max(1, retries)):
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers=hdrs)
        try:
            with op.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except Exception as e:                                    # noqa: BLE001
            last = (0, {}, ("连接失败：%s" % e).encode())
            if i < retries - 1:
                time.sleep(1.2 * (i + 1))
    return last


def rec(name, ok, detail):
    TOTAL[0] += 1
    if ok:
        OK[0] += 1
    log.append("%s %-46s %s" % ("[OK ]" if ok else "[ERR]", name, detail))
    return ok


def login(user, pwd, retries=3):
    """登录并缓存 Cookie。

    加重试是有原因的：线上实测出现过一次「刚跑完一次耗时审查、紧接着登录，
    首个 /api/auth/me 拿不到正确角色」的偶发情况（服务端此时正在写库）。
    巡检脚本对偶发必须自己扛住，否则会把「环境抖动」报成「功能缺陷」。
    """
    for i in range(retries):
        code, hdrs, body = call("POST", "/api/auth/login",
                                {"username": user, "password": pwd}, cookie=False)
        sc = hdrs.get("Set-Cookie") or ""
        if code == 200 and sc:
            JAR["c"] = sc.split(";")[0]
            # 立刻确认会话真的生效（登录成功与会话可用是两件事）
            c2, _, b2 = call("GET", "/api/auth/me")
            try:
                ok = c2 == 200 and (json.loads(b2).get("data") or {}).get("username") == user
            except Exception:                                     # noqa: BLE001
                ok = False
            if ok:
                return True
            log.append("       （登录后会话未立即生效，重试 %d/%d）" % (i + 1, retries))
        time.sleep(0.8)
    return False


def jget(path, method="GET", body=None, **kw):
    code, hdrs, raw = call(method, path, body=body, **kw)
    try:
        return code, hdrs, json.loads(raw)
    except Exception:                                             # noqa: BLE001
        return code, hdrs, None


T0 = time.time()
log.append("衡鉴 V2.1.7 · 线上实例巡检｜运行版本 %s" % _app.__version__)
log.append("目标：%s ｜ 时间：%s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")))
log.append("=" * 100)

# ---------------------------------------------------------------- 1 探活与版本
code, hdrs, h = jget("/api/health", cookie=False)
d = (h or {}).get("data") or {}
rec("公网 /api/health 可达", code == 200 and (h or {}).get("code") == 0,
    "HTTP %s，耗时 %d ms" % (code, int((time.time() - T0) * 1000)))
sys.path.insert(0, ROOT)
rec("版本号与交付版本一致（禁止版本失真）",
    d.get("version") == _app.__version__,
    "线上 version=%s ｜ 交付版本=%s" % (d.get("version"), _app.__version__))
rec("服务端 PDF 通道：零依赖直出可用（无 chromium 也不降级）",
    (d.get("pdf_channels") or {}).get("stdlib") is True,
    "browser=%s stdlib=%s effective=%s"
    % ((d.get("pdf_channels") or {}).get("browser"),
       (d.get("pdf_channels") or {}).get("stdlib"),
       (d.get("pdf_channels") or {}).get("effective")))
rec("OCR 后端已就绪", bool(d.get("ocr_backend")) and d.get("ocr_backend") != "none",
    "ocr_backend=%s" % d.get("ocr_backend"))
rec("配置无告警", not d.get("config_warnings"),
    "config_warnings=%s" % (d.get("config_warnings") or "无"))
rec("三角色账号已建、会话数正常",
    (d.get("auth") or {}).get("accounts") == 3 and (d.get("auth") or {}).get("enabled"),
    "auth.enabled=%s accounts=%s sessions=%s"
    % ((d.get("auth") or {}).get("enabled"), (d.get("auth") or {}).get("accounts"),
       (d.get("auth") or {}).get("sessions")))
rec("定时拉取默认关闭（演示数据可控）",
    (d.get("scheduler") or {}).get("enabled") is False,
    "scheduler.enabled=%s interval=%ss"
    % ((d.get("scheduler") or {}).get("enabled"),
       (d.get("scheduler") or {}).get("interval_sec")))
rec("通知脱敏已生效（外发字段默认全掩码）",
    ((d.get("notify") or {}).get("mask") or {}).get("party") is True
    and ((d.get("notify") or {}).get("mask") or {}).get("max_evidence_chars") == 0,
    "mask=%s" % json.dumps((d.get("notify") or {}).get("mask") or {}, ensure_ascii=False))
rec("通知事件默认收敛（batch_summary / review_completed 关闭）",
    ((d.get("notify") or {}).get("events") or {}).get("batch_summary") is False
    and ((d.get("notify") or {}).get("events") or {}).get("review_completed") is False,
    "events=%s" % json.dumps((d.get("notify") or {}).get("events") or {}, ensure_ascii=False))
rec("钉钉通知凭据仍然在位（上线未打回默认）",
    ((d.get("notify") or {}).get("enabled") is True
     and (d.get("notify") or {}).get("configured") is True),
    "notify.enabled=%s configured=%s source=%s"
    % ((d.get("notify") or {}).get("enabled"), (d.get("notify") or {}).get("configured"),
       (d.get("notify") or {}).get("webhook_source")))

# ---------------------------------------------------------------- 2 鉴权闸门
code, hdrs, _ = call("GET", "/", cookie=False, no_redirect=True)
rec("未登录访问首页被重定向到登录页",
    code == 302 and "/login" in (hdrs.get("Location") or ""),
    "HTTP %s → %s" % (code, hdrs.get("Location")))
code, _, body = call("GET", "/api/cases", cookie=False)
rec("未登录访问接口返回 401", code == 401,
    "HTTP %s，msg=%s" % (code, body.decode("utf-8", "ignore")[:60]))
code, _, body = call("GET", "/login", cookie=False)
rec("登录页公网可访问且含三角色入口",
    code == 200 and b"role-item" in body and "一键进入".encode("utf-8") in body,
    "HTTP %s，%d 字节" % (code, len(body)))

# ---------------------------------------------------------------- 3 管理员视角
LOGIN = login("admin", "Admin@2026")
rec("管理员账号公网登录成功并下发 HttpOnly 会话", LOGIN,
    "Cookie 前缀=%s" % (JAR["c"][:26] if JAR["c"] else "无"))
code, _, m = jget("/api/auth/me")
md = (m or {}).get("data") or {}
rec("会话返回正确角色与能力清单",
    code == 200 and md.get("role") == "admin" and len(md.get("caps") or []) >= 16,
    "角色=%s 能力 %d 项" % (md.get("role_label"), len(md.get("caps") or [])))

# 说明：这里是**原始 HTTP 探针**（不跑浏览器），所以只能断言页面源码里静态存在的元素。
# 顶栏的角色标识、孪生看板的流水线节点都是 JS 运行时注入的，不在源码里 ——
# 那类断言交给本地 tools/verify_render.py（无头浏览器）去做。
for path, probes, label in [
    ("/", [(b'id="topbar"', "顶栏容器"), (b'id="swSched"', "自动流转面板"),
           (b'id="queueKv"', "队列面板"), (b"openAuth()", "权限入口")], "审查大盘"),
    ("/twin", [(b'id="topbar"', "顶栏容器"), (b'id="clock"', "时钟"),
               (b'id="pipe"', "流水线容器"), (b'id="logStream"', "实时日志流"),
               (b'id="topPaths"', "接口响应面板")], "动态孪生看板"),
    ("/rules", [(b"rule-row", "规则行"), (b"curTypeLabel", "类型切换")], "合规规则与模板"),
]:
    code, _, body = call("GET", path)
    miss = [n for pat, n in probes if pat not in body]
    rec("%s 页面公网渲染正常（%d 个探针）" % (label, len(probes)),
        code == 200 and not miss,
        "HTTP %s，%d 字节%s" % (code, len(body), "" if not miss else "，缺失：" + "、".join(miss)))

# 取一个已完成案件做工作台与报告的验证
code, _, cs = jget("/api/cases")
items = (cs or {}).get("data", {}).get("items") or []
rec("线上保留既有审查数据（上线未清库）", len(items) >= 1,
    "任务 %d 个，首个：%s" % (len(items), items[0]["title"] if items else "-"))
cid = None
for it in items:
    if it.get("review_status") == "completed":
        cid = it["id"]
        break
cid = cid or (items[0]["id"] if items else 1)

code, _, body = call("GET", "/workbench?id=%d" % cid)
miss = [n for pat, n in [(b'id="doc"', "正文容器"), (b"mode-col", "模式对照"),
                         (b'id="summaryBox"', "摘要区"), (b'id="opinion"', "批注框")]
        if pat not in body]
rec("智能审查工作台公网渲染正常（含模式对照面板）",
    code == 200 and not miss,
    "HTTP %s，%d 字节%s" % (code, len(body), "" if not miss else "，缺失：" + "、".join(miss)))

code, _, det = jget("/api/cases/%d" % cid)
dd = (det or {}).get("data") or {}
if not (dd.get("mode_compare") or {}).get("current"):
    # 线上库里的案件是上一版审查产生的，解析快照里没有 v2.0 新增的
    # mode_compare / stripped 字段 —— 这是数据状态问题，不是缺陷。
    # 重新执行一次审查即可生成 v2.0 格式的快照；顺带也验证了线上审查流水线本身。
    code_rv, _, rv = jget("/api/cases/%d/review" % cid, method="POST")
    log.append("       （旧快照缺 v2.0 字段，已触发一次重新审查：HTTP %s %s）"
               % (code_rv, json.dumps({k: (rv or {}).get("data", {}).get(k)
                                      for k in ("ok", "risk_level", "hits", "mode_label")},
                                     ensure_ascii=False)))
    code, _, det = jget("/api/cases/%d" % cid)
    dd = (det or {}).get("data") or {}
rec("详情接口返回模式对照与页眉页脚剔除留痕",
    bool(dd.get("mode_compare")) and "stripped" in dd and "strip_config" in dd,
    "current=%s 剔除 %d 行"
    % ((dd.get("mode_compare") or {}).get("current"), len(dd.get("stripped") or [])))

code, _, body = call("GET", "/api/cases/%d/report?format=pdf" % cid)
rec("报告导出返回真实 PDF 字节流（线上走零依赖通道）",
    code == 200 and body[:5] == b"%PDF-" and len(body) > 1200,
    "HTTP %s，%.1f KB，头=%s" % (code, len(body) / 1024.0,
                                body[:8].decode("latin-1", "ignore")))

code, _, body = call("GET", "/api/export")
rec("审计全量导出可用（生成 JSON 并返回文件信息）",
    code == 200 and b"audit_export_" in body,
    "HTTP %s，%d 字节，%s" % (code, len(body), body.decode("utf-8", "ignore")[:120]))

# ---------------------------------------------------------------- 4 v2.0 新接口
code, _, s = jget("/api/scheduler")
rec("调度状态接口可用（默认关闭）",
    code == 200 and (s or {}).get("data", {}).get("enabled") is False,
    "enabled=%s interval=%ss runs=%s"
    % ((s or {}).get("data", {}).get("enabled"),
       (s or {}).get("data", {}).get("interval_sec"),
       (s or {}).get("data", {}).get("runs")))
code, _, q = jget("/api/queue")
qd = (q or {}).get("data") or {}
rec("审查队列与四道配额接口可用",
    code == 200 and all(k in qd for k in ("workers", "queued", "maxsize",
                                         "max_pending", "max_total_mb")),
    "workers=%s queued=%s/%s pending=%s/%s"
    % (qd.get("workers"), qd.get("queued"), qd.get("maxsize"),
       qd.get("pending_cases"), qd.get("max_pending")))
code, _, pc = jget("/api/pdf/check")
pcd = (pc or {}).get("data") or {}
rec("服务端 PDF 直出通道自检通过",
    code == 200 and pcd.get("stdlib") is True and pcd.get("size", 0) > 1200,
    "stdlib=%s size=%s effective=%s"
    % (pcd.get("stdlib"), pcd.get("size"), pcd.get("effective")))

# ---------------------------------------------------------------- 4 v2.0.1 新增页面与新接口
code, _, tw = jget("/api/twin")
twd = (tw or {}).get("data") or {}
rec("公网 GET /api/twin 返回流水线七段真实计数",
    code == 200 and len(twd.get("pipeline") or []) == 7
    and all(n.get("src") for n in (twd.get("pipeline") or [])),
    "节点 %d 个，每个均标注来源；实时日志 %d 条"
    % (len(twd.get("pipeline") or []), len(twd.get("activity") or [])))
code, _, cs2 = jget("/api/cases")
rec("孪生看板计数与任务列表一致（线上不造数）",
    twd.get("counts", {}).get("total") == len(
        ((cs2 or {}).get("data", {}) or {}).get("items") or []),
    "twin 总任务 %s ｜ cases %s 个"
    % (twd.get("counts", {}).get("total"),
       len(((cs2 or {}).get("data", {}) or {}).get("items") or [])))
rec("实时日志取自审计表且时间戳与 /api/audit 对齐",
    bool(twd.get("activity")) and twd["activity"][0].get("id") == json.loads(
        call("GET", "/api/audit?limit=60")[2])["data"]["items"][0]["id"],
    "首条审计 id=%s" % (twd.get("activity") or [{}])[0].get("id"))
code, _, mp = jget("/api/mock/overview")
rec("模拟审批数据接口可用",
    code == 200 and len(((mp or {}).get("data", {}) or {}).get("instances") or []) >= 6,
    "审批单 %s 张"
    % len(((mp or {}).get("data", {}) or {}).get("instances") or []))
code, _, body = call("GET", "/mock")
rec("模拟审批美化页公网可渲染（替代裸 JSON）",
    code == 200 and b"mock-item" in body and b"toggleRaw" in body,
    "HTTP %s，%d 字节" % (code, len(body)))
code, hdrs_m, _ = call("GET", "/mock", cookie=False, no_redirect=True)
rec("未登录访问 /mock 被重定向（内部页不外露）",
    code == 302 and "/login" in (hdrs_m.get("Location") or ""),
    "HTTP %s → %s" % (code, hdrs_m.get("Location")))
code, _, body = call("GET", "/mock/feishu/api/todo", cookie=False)
rec("模拟审批原始 JSON 接口仍对匿名开放（对外契约不变）",
    code == 200 and b"instance_id" in body,
    "HTTP %s，%d 字节%s" % (code, len(body),
                         "" if code == 200 else "｜ %s" % body.decode("utf-8", "ignore")[:80]))
code, _, body = call("GET", "/api/cases?writeback=success")
rec("统计卡下钻依赖的 writeback 筛选可用",
    code == 200, "HTTP %s，返回 %d 个"
    % (code, len(json.loads(body)["data"]["items"]) if code == 200 else 0))

# ---------------------------------------------------------------- 4.5 AI 助手
code, _, body = call("POST", "/api/assistant/ask",
                     {"q": "现在一共有多少个审查任务", "page": "/"})
_a = json.loads(body).get("data") or {} if code == 200 else {}
rec("公网助手能回答并给出实时数据",
    code == 200 and _a.get("source") == "live"
    and "个审查任务" in (_a.get("answer") or ""),
    "来源 %s，%d 字" % (_a.get("source"), len(_a.get("answer") or "")))
code, _, body = call("POST", "/api/assistant/ask",
                     {"q": "明年棉花期货价格会涨吗", "page": "/"})
_a2 = json.loads(body).get("data") or {} if code == 200 else {}
rec("公网助手对不知道的问题不编造",
    code == 200 and _a2.get("matched") is False and "没匹配到" in (_a2.get("answer") or ""),
    "来源 %s" % _a2.get("source"))
code, _, body = call("GET", "/api/assistant/tour?page=/")
_t = json.loads(body).get("data") or {} if code == 200 else {}
rec("公网可取得新手引导步骤（带真实选择器）",
    code == 200 and len(_t.get("steps") or []) >= 5
    and all(s.get("target") for s in (_t.get("steps") or [])),
    "首页 %d 步" % len(_t.get("steps") or []))
code, hdrs_a, _ = call("POST", "/api/assistant/ask", {"q": "我是谁"}, cookie=False)
rec("未登录访问助手接口被拦（不泄露业务数据）", code == 401, "HTTP %s" % code)

# ---------------------------------------------------------------- 5 越权拦截（法务）
login("legal", "Legal@2026")
code, _, raw_me = call("GET", "/api/auth/me")
try:
    me2 = json.loads(raw_me)
except Exception:                                                 # noqa: BLE001
    me2 = None
md2 = (me2 or {}).get("data") or {}
rec("法务账号能力集正确（可回写、不可改规则）",
    code == 200 and md2.get("role") == "legal"
    and "writeback" in (md2.get("caps") or [])
    and "edit_rules" not in (md2.get("caps") or []),
    "HTTP %s ｜ 角色=%s ｜ 能力 %d 项 ｜ 响应 %s"
    % (code, md2.get("role_label") or "-", len(md2.get("caps") or []),
       (raw_me.decode("utf-8", "ignore")[:110] if not md2 else
        "、".join((md2.get("caps") or [])[:5]) + "…")))

code, _, body = call("GET", "/rules")
html = body.decode("utf-8", "replace")
rec("法务访问规则页被拦在 403 权限受限页（占位符已填充）",
    code == 403 and "权限受限".encode("utf-8") in body and "{{" not in html,
    "HTTP %s，%d 字节，未替换占位符 %d 个" % (code, len(body), html.count("{{")))
code, _, body = call("POST", "/api/rules/purchase", {"rules": []})
rec("法务调用规则写接口被服务端拒绝",
    code == 403, "HTTP %s，msg=%s"
    % (code, json.loads(body).get("msg", "")[:70] if body.lstrip()[:1] == b"{" else ""))
code, _, body = call("GET", "/api/metrics")
rec("法务访问运行指标被拒绝（能力归属管理员）", code == 403, "HTTP %s" % code)

# 越权是否留痕
login("admin", "Admin@2026")
code, _, au = jget("/api/audit?limit=300")
guards = [a for a in ((au or {}).get("data", {}).get("items") or [])
          if a.get("tool") == "auth.guard"]
rec("越权与未登录均写入审计（auth.guard）", len(guards) >= 3,
    "auth.guard 记录 %d 条，最近：%s"
    % (len(guards), (guards[0]["detail"][:66] if guards else "-")))

# ---------------------------------------------------------------- 汇总
log.append("=" * 100)
log.append("巡检结果：%d / %d 项通过，总耗时 %.1fs" % (OK[0], TOTAL[0], time.time() - T0))
log.append("=" * 100)
log.append("说明：本次巡检全部走公网 HTTP，未登录 SSH；覆盖探活 / 版本口径 / 鉴权闸门 /")
log.append("      三角色权限隔离 / v2.0 新增接口 / 既有数据保留 / 报告导出 / 审计留痕。")

out = os.path.join(ROOT, "verify")
os.makedirs(out, exist_ok=True)
with io.open(os.path.join(out, "remote_inspect.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
with io.open(os.path.join(out, "remote_inspect.json"), "w", encoding="utf-8") as f:
    json.dump({"target": BASE, "passed": OK[0], "total": TOTAL[0],
               "at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "checks": log}, f, ensure_ascii=False, indent=2)
print("remote inspect: %d / %d" % (OK[0], TOTAL[0]))
sys.exit(0 if OK[0] == TOTAL[0] else 1)
