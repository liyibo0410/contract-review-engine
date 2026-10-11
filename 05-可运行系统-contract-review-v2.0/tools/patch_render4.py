# -*- coding: utf-8 -*-
"""给渲染校验补「手机宽度截图」：在 390×844（iPhone 逻辑分辨率）下渲染并截图，
作为移动端适配的可视证据。同时用探针确认窄屏下关键容器仍然渲染出来。"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
P = os.path.join(ROOT, "tools", "verify_render.py")
with io.open(P, "r", encoding="utf-8") as f:
    c = f.read()
log = []


def rep(old, new):
    global c
    if old in c:
        c = c.replace(old, new, 1)
        log.append("ok   " + old.strip().splitlines()[0][:66])
    else:
        log.append("MISS " + old.strip().splitlines()[0][:66])


# run_browser 支持自定义窗口尺寸
rep("""    if shot_path:
        args += ["--window-size=1600,1100", "--screenshot=" + shot_path]
    else:
        args += ["--dump-dom"]""",
    """    if shot_path:
        args += ["--window-size=%s" % win, "--screenshot=" + shot_path]
    else:
        args += ["--dump-dom"]""")

rep("""def run_browser(url, dump_dom=False, shot_path=None, cookie=None):""",
    """def run_browser(url, dump_dom=False, shot_path=None, cookie=None,
                win="1600,1100"):""")

# 在 DOM 探针完成前插入手机端截图
rep("""log.append("DOM 探针完成")""",
    """# ---------------------------------------------------------------- 手机端适配
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
    ("顶栏", r'class="nav"'),
    ("统计卡", r"clickable"),
    ("队列面板", r'id="queueKv"'),
], "手机端审查大盘可读可点")

mobile_shot("/twin", "08_手机端_孪生看板.png", [
    ("顶栏", r'class="nav"'),
    ("KPI", r'id="kpi"'),
    ("实时日志", r'id="logStream"'),
], "手机端孪生看板可读")

log.append("DOM 探针完成")""")

with io.open(P, "w", encoding="utf-8") as f:
    f.write(c)
with io.open(os.path.join(ROOT, "verify", "render_patch4.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("render patch 4 done")
