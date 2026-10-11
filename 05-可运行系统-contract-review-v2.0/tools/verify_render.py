# -*- coding: utf-8 -*-
"""页面渲染与截图校验（M11 判据：≥7 项）。

做法：启动真实服务 → 用系统浏览器无头模式打开每个页面 → 对真实渲染后的 DOM 做
元素计数断言（不是抓源码字符串），并把截图落到 docs/screenshots/。

v2.0 新增：登录页、权限受限页（403）两个受控页面，以及大盘的自动流转 / 队列面板、
工作台的模式对照面板。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, server as server_mod, store as store_mod   # noqa: E402
from mock import feishu                                                      # noqa: E402

PORT = 8793
TMP = tempfile.mkdtemp(prefix="cr_shot_")
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(TMP, "data"), "uploads": os.path.join(TMP, "uploads"),
    "parsed": os.path.join(TMP, "parsed"), "reports": os.path.join(TMP, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT
for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)

st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)
from app import pipeline                                                    # noqa: E402

# 顺序很关键：拉取待办是**真的走 HTTP** 打到同端口的模拟审批接口，
# 服务没起来就拉，只会得到 Connection refused 与 0 条任务。
httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.7)

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
BROWSER = next((p for p in CANDIDATES if os.path.exists(p)), "")
SHOTS = os.path.join(ROOT, "docs", "screenshots")
os.makedirs(SHOTS, exist_ok=True)

log = []
ok_count = [0]
SEQ = [0]


def rec(name, ok, detail):
    log.append("%s %-46s %s" % ("[OK ]" if ok else "[ERR]", name, detail))
    if ok:
        ok_count[0] += 1


_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.I)


def body_only(dom):
    """剥掉 <script> 与内联模板，只留真实 DOM。

    不剥的话探针会命中 JS 源码里的模板字符串（'<div class="pipe-node"...'），
    于是「页面其实没渲染」也会被判定为通过 —— 这是最隐蔽的一类假阳性。
    """
    return _SCRIPT_RE.sub("", dom or "")


def run_browser(url, dump_dom=False, shot_path=None, cookie=None,
                win="1600,1100"):
    if not BROWSER:
        return ""
    ud = tempfile.mkdtemp(prefix="vl_")
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "no_proxy")}
    args = [BROWSER, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--no-first-run", "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            "--user-data-dir=" + ud, "--virtual-time-budget=6000"]
    if shot_path:
        args += ["--window-size=%s" % win, "--screenshot=" + shot_path]
    else:
        args += ["--dump-dom"]
    args.append(url)
    try:
        r = subprocess.run(args, capture_output=True, env=env, timeout=150)
        dom = r.stdout.decode("utf-8", "replace")
    except Exception as exc:                                             # noqa: BLE001
        log.append("     浏览器调用失败：%s" % exc)
        dom = ""
    finally:
        shutil.rmtree(ud, ignore_errors=True)
    return dom


def probe(name, path, probes, shot=None, expect_status=200):
    """打开页面 → 断言元素个数 → 可选截图。"""
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    dom = body_only(run_browser(url))
    if not dom:
        rec(name, False, "浏览器不可用，跳过 DOM 探针")
        return
    res = []
    bad = []
    for label, pat in probes:
        n = len(re.findall(pat, dom))
        res.append("%s=%d" % (label, n))
        if n == 0:
            bad.append(label)
    if shot:
        run_browser(url, shot_path=os.path.join(SHOTS, shot))
        res.append("截图=" + shot)
    rec(name, not bad, "  ".join(res) + ("" if not bad else "  ← 缺失：" + "、".join(bad)))


# ---------------------------------------------------------------- 预热数据
# 顺序很关键：拉取待办是**真的走 HTTP** 打到同端口的模拟审批接口，
# 必须在服务起来之后再做，否则只有 Connection refused 与 0 条任务。
PULLED = pipeline.pull_from_approval(cfg, st, auto_review=True)
rec("预热数据：拉取并审查示例待办", bool(PULLED.get("created")),
    "新建 %d 个，其中已完成 %d 个" % (
        len(PULLED.get("created") or []),
        len([c for c in st.list_cases() if c["review_status"] == "completed"])))

# 再做一次真实回写：否则「模拟审批页的评论区」在全新库上是空的，
# 那条探针会误报（数据状态问题被当成功能缺陷）。
_done = [c for c in st.list_cases() if c["review_status"] == "completed"]
if _done:
    _cid = _done[0]["id"]
    _d = pipeline.detail(cfg, st, _cid)
    from app import output as _out, writeback as _wb                    # noqa: E402
    _txt = _out.compose_comment(_d["case"], _d["document"], _d["results"],
                                _d["summary"], _d["meta"], "演示预热：写回意见已生成。")
    _rv = _wb.send(cfg, st, st.get_case(_cid), _txt, mode="live")
    rec("预热数据：真实回写一条审查意见", _rv.get("status") in ("success", "sent"),
        "案件 #%d → 评论 %s" % (_cid, _rv.get("comment_id")))

# ---------------------------------------------------------------- 未登录：登录页
probe("登录页渲染（三类演示身份 + 口令表单）", "/login", [
    ("登录表单", r'id="loginForm"'),
    ("账号输入", r'id="u"'),
    ("口令输入", r'id="p"'),
    ("角色卡", r'class="role-item'),
    ("一键进入", r'一键进入'),
], shot="00_登录页.png")

# ---------------------------------------------------------------- 登录并取 Cookie
import urllib.request                                                    # noqa: E402
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request(
    "http://127.0.0.1:%d/api/auth/login" % PORT,
    data=json.dumps({"username": "admin", "password": "Admin@2026"},
                    ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST")
with op.open(req, timeout=30) as r:
    SET_COOKIE = (r.headers.get("Set-Cookie") or "").split(";")[0]
rec("登录接口下发 HttpOnly 会话 Cookie",
    "cr_session=" in SET_COOKIE and "HttpOnly" in (
        r.headers.get("Set-Cookie") or ""),
    "Set-Cookie 前缀=%s" % SET_COOKIE[:34])

PROXY = tempfile.mkdtemp(prefix="cr_pp_")


def probe_authed(name, path, probes, shot=None):
    """带登录态打开页面：用一个极简反向代理把 Cookie 注入请求。

    浏览器的 --headless 不支持直接带 Cookie 参数，所以起一个同端口转发代理，
    由它给每个请求补上会话 Cookie，从而在不改动服务端的前提下渲染登录后页面。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.error

    class Px(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                                       # noqa: A003
            pass

        def _fwd(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(n) if n else None
            # 关键：不透传 Accept-Encoding。否则服务端返回 gzip 字节，而这里转发时
            # 只带 Content-Type/Content-Length、丢掉了 Content-Encoding，浏览器会拿到
            # 一段被标成 text/html 的压缩数据，DOM 直接为空（探针全 0）。
            hdrs = {"Cookie": SET_COOKIE}
            for h in ("Content-Type",):
                if self.headers.get(h):
                    hdrs[h] = self.headers[h]
            req2 = urllib.request.Request(
                "http://127.0.0.1:%d%s" % (PORT, self.path), data=payload,
                headers=hdrs, method=method)
            try:
                with urllib.request.build_opener(
                        urllib.request.ProxyHandler({})).open(req2, timeout=60) as rr:
                    body = rr.read()
                    code = rr.status
                    ct = rr.headers.get("Content-Type") or "text/html"
            except urllib.error.HTTPError as e:
                body, code = e.read(), e.code
                ct = e.headers.get("Content-Type") or "text/html"
            except Exception as e:                                       # noqa: BLE001
                body, code, ct = str(e).encode(), 502, "text/plain"
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def do_GET(self):                                                # noqa: N802
            self._fwd("GET")

        def do_POST(self):                                               # noqa: N802
            self._fwd("POST")

    px = ThreadingHTTPServer(("127.0.0.1", 0), Px)
    px.daemon_threads = True
    port = px.server_address[1]
    threading.Thread(target=px.serve_forever, daemon=True).start()
    time.sleep(0.2)
    url = "http://127.0.0.1:%d%s" % (port, path)
    raw_dom = run_browser(url)
    dom = body_only(raw_dom)
    if raw_dom:
        # 留证：渲染后的 DOM 落盘，探针失败时可直接查看页面到底渲染成了什么
        SEQ[0] += 1
        with io.open(os.path.join(
                ROOT, "verify", "_dom_%02d_%s.html"
                % (SEQ[0], re.sub(r"[^\w]", "_", path).strip("_"))),
                "w", encoding="utf-8") as f:
            f.write(raw_dom)      # 留证保留完整 DOM（含脚本）
    if not dom:
        rec(name, False, "浏览器不可用，跳过 DOM 探针")
    else:
        res, bad = [], []
        for label, pat in probes:
            n = len(re.findall(pat, dom))
            res.append("%s=%d" % (label, n))
            if n == 0:
                bad.append(label)
        if shot:
            run_browser(url, shot_path=os.path.join(SHOTS, shot))
            res.append("截图=" + shot)
        rec(name, not bad, "  ".join(res) + ("" if not bad else "  ← 缺失：" + "、".join(bad)))
    px.shutdown()
    px.server_close()


probe_authed("审查大盘渲染（KPI 可下钻 + 自动流转 + 队列 + 顶栏身份）", "/", [
    ("KPI 容器", r'id="stats"'),
    ("统计卡可下钻", r'class="stat [^"]*clickable'),
    ("下钻数据绑定", r'data-drill='),
    ("下钻弹窗", r'id="mDrill"'),
    ("自动流转面板", r'id="swSched"'),
    ("调度指标", r'id="schedKv"'),
    ("队列指标", r'id="queueKv"'),
    ("角色标识", r'id="roleChip"'),
    ("权限入口", r'openAuth\(\)'),
    ("顶栏 logo", r'class="brand-logo'),
    ("顶栏导航", r'class="nav'),
], shot="01_审查大盘.png")

FIRST_CASE = (st.list_cases() or [{}])[-1].get("id") or 1
probe_authed("智能审查工作台渲染（正文高亮 + 风险卡 + 模式对照）",
             "/workbench?id=%d" % FIRST_CASE, [
    ("正文段落", r'<p class="doc-para'),
    ("风险高亮", r'<mark class="hl'),
    ("风险卡片", r'id="card-\d+"'),
    ("模式对照", r'class="mode-col'),
    ("剔除留痕或模式说明", r'class="mode-note'),
    ("双栏视窗", r'class="wb'),
], shot="02_智能审查工作台.png")

probe_authed("合规规则与模板渲染（管理员可见）", "/rules", [
    ("规则行", r'class="rule-row'),
    ("类型切换标题", r'id="curTypeLabel"'),
    ("规则版本信息", r'id="verInfo"'),
], shot="03_合规规则与模板.png")

probe_authed("动态孪生看板渲染（全真实实时数据）", "/twin", [
    ("时钟", r'id="clock"'),
    ("KPI 行", r'id="kpi"'),
    ("流水线容器", r'id="pipe"'),
    ("流水线节点", r'class="pipe-node'),
    ("数据来源标注", r'class="src'),
    ("真实仪表", r'class="meter '),
    ("接口响应区", r'id="topPaths"'),
    ("实时日志流", r'id="logStream"'),
    ("日志条目", r'<div class="li[^"]*">'),
    ("队列调度区", r'id="opsKv"'),
    ("规则库条", r'class="rb'),
    ("无模拟值声明", r'本页不构造任何演示数据'),
], shot="04_动态孪生看板.png")

probe_authed("AI 助手：悬浮宠物挂载 + 对话可开 + 引导可跑", "/?pet=open,tour", [
    ("宠物挂载", r'id="petRoot"'),
    ("宠物按钮", r'id="petBtn"'),
    ("对话面板", r'id="petPanel"'),
    ("输入框", r'id="petInput"'),
    ("快捷提问", r'class="pet-chip'),
    ("引导聚光灯", r'id="petSpot"'),
    ("引导气泡", r'id="petCoach"'),
    ("引导进度", r'class="pet-prog'),
    ("引导步骤标题", r'第 1 / \d+ 步'),
    ("上一步/下一步", r'data-t="next"'),
], shot="09_AI助手与新手引导.png")

probe_authed("AI 助手在孪生看板同样挂载", "/twin?pet=open", [
    ("宠物挂载", r'id="petRoot"'),
    ("对话面板展开", r'pet-panel on'),
    ("欢迎语", r'小亦'),
], shot=None)

probe_authed("模拟审批系统美化页渲染（替代裸 JSON）", "/mock", [
    ("待办列表", r'class="mock-item'),
    ("详情区", r'id="detail"'),
    ("接口清单", r'id="eps"'),
    ("接口条目", r'class="ep'),
    ("原始 JSON 入口", r'id="rawPanel"'),
    ("评论区区块", r'class="mock-comment'),
], shot="06_模拟审批系统.png")

# ---------------------------------------------------------------- 受限页（403）
login_legal = urllib.request.Request(
    "http://127.0.0.1:%d/api/auth/login" % PORT,
    data=json.dumps({"username": "legal", "password": "Legal@2026"},
                    ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"}, method="POST")
with op.open(login_legal, timeout=30) as r:
    SET_COOKIE = (r.headers.get("Set-Cookie") or "").split(";")[0]
probe_authed("权限受限页渲染（法务进规则页被拦，含缺权说明与切换入口）", "/rules", [
    ("受限标识", r'403'),
    ("当前身份", r'法务审查人'),
    ("缺少能力", r'维护合规规则'),
    ("归属角色", r'系统管理员'),
    ("切换入口", r'/login\?next='),
], shot="05_权限受限页.png")

# ---------------------------------------------------------------- 早前留痕
# ---------------------------------------------------------------- 手机端适配
# 390×844 是 iPhone 的逻辑分辨率。这里不只看“能不能打开”，还要确认：
#   ① 关键容器仍然渲染；② 顶栏新结构（min-height + 换行）生效；
#   ③ 桌面版的三栏/双栏在窄屏下降为单列（CSS 断点真的命中了）。
def mobile_shot(path, shot, probes, label):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.error

    class Px(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                                   # noqa: A003
            pass

        def _fwd(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(n) if n else None
            hdrs = {"Cookie": SET_COOKIE}
            if self.headers.get("Content-Type"):
                hdrs["Content-Type"] = self.headers["Content-Type"]
            req2 = urllib.request.Request(
                "http://127.0.0.1:%d%s" % (PORT, self.path), data=payload,
                headers=hdrs, method=method)
            try:
                with urllib.request.build_opener(
                        urllib.request.ProxyHandler({})).open(req2, timeout=60) as rr:
                    body, code, ct = rr.read(), rr.status, rr.headers.get("Content-Type") or "text/html"
            except urllib.error.HTTPError as e:
                body, code, ct = e.read(), e.code, "text/html"
            except Exception as e:                                   # noqa: BLE001
                body, code, ct = str(e).encode(), 502, "text/plain"
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def do_GET(self):                                            # noqa: N802
            self._fwd("GET")

    px = ThreadingHTTPServer(("127.0.0.1", 0), Px)
    px.daemon_threads = True
    port = px.server_address[1]
    threading.Thread(target=px.serve_forever, daemon=True).start()
    time.sleep(0.2)
    url = "http://127.0.0.1:%d%s" % (port, path)
    dom = body_only(run_browser(url))
    run_browser(url, shot_path=os.path.join(SHOTS, shot), win="390,844")
    px.shutdown()
    px.server_close()
    res, bad = [], []
    for lab, pat in probes:
        n = len(re.findall(pat, dom))
        res.append("%s=%d" % (lab, n))
        if n == 0:
            bad.append(lab)
    res.append("截图=" + shot)
    rec(label + "（390×844）", not bad,
        "  ".join(res) + ("" if not bad else "  ← 缺失：" + "、".join(bad)))


mobile_shot("/", "07_手机端_审查大盘.png", [
    ("顶栏", r'class="nav'),
    ("统计卡", r"clickable"),
    ("队列面板", r'id="queueKv"'),
], "手机端审查大盘可读可点")

mobile_shot("/?pet=tour", "10_手机端_新手引导.png", [
    ("宠物", r'id="petRoot"'),
    ("引导气泡", r'id="petCoach"'),
], "手机端新手引导可读")

mobile_shot("/twin", "08_手机端_孪生看板.png", [
    ("顶栏", r'class="nav'),
    ("KPI", r'id="kpi"'),
    ("实时日志", r'id="logStream"'),
], "手机端孪生看板可读")

log.append("DOM 探针完成")
httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(PROXY, ignore_errors=True)

total_shots = len([f for f in os.listdir(SHOTS) if f.endswith(".png")])
head = "页面渲染与截图校验：%d 项通过（截图 %d 张，浏览器 %s）" % (
    ok_count[0], total_shots, os.path.basename(BROWSER) or "未找到")
with io.open(os.path.join(ROOT, "verify", "screenshots.txt"), "w", encoding="utf-8") as f:
    f.write(head + "\n" + "=" * 100 + "\n" + "\n".join(log) + "\n")
print(head)
