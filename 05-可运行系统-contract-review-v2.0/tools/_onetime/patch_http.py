# -*- coding: utf-8 -*-
"""verify_http.py 的收尾修正：
  1) 302 检查不能被 urllib 自动跟随（否则永远看到 200）；
  2) 深空控制台改版后 app.css 的令牌名变了（--accent → --brand），断言同步；
  3) 冒烟必须可重复 —— 数据目录改为独立临时目录，不污染交付数据；
  4) 404 用例的权限前置身份切换。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
P = os.path.join(ROOT, "tools", "verify_http.py")
with io.open(P, "r", encoding="utf-8") as f:
    c = f.read()
log = []


def rep(old, new, must=True):
    global c
    if old in c:
        c = c.replace(old, new, 1)
        log.append("ok   " + old.strip().splitlines()[0][:70])
    else:
        log.append(("MISS " if must else "skip ") + old.strip().splitlines()[0][:70])


# ---- 1) 不跟随重定向的 opener，并让数据落独立临时目录
rep("""from app import config as cfg_mod, server as server_mod, store as store_mod   # noqa: E402
from mock import feishu                                                       # noqa: E402

PORT = 8791
cfg = cfg_mod.Config(ROOT)
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT

for k in ("data", "uploads", "parsed", "reports", "samples"):
    cfg.path(k)
""",
    """import tempfile                                                      # noqa: E402

from app import config as cfg_mod, server as server_mod, store as store_mod   # noqa: E402
from mock import feishu                                                       # noqa: E402

PORT = 8791
TMP = tempfile.mkdtemp(prefix="cr_smoke_")      # 独立数据目录：冒烟可重复且不动交付数据
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
""")

# ---- 2) 增加不跟随重定向的 opener
rep("""OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:%d" % PORT""",
    """OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# 不跟随重定向：否则「未登录跳登录页」永远看到的是最终 200，测不出 302 本身
NO_REDIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    type("NR", (urllib.request.HTTPRedirectHandler,), {
        "redirect_request": lambda *a, **k: None})())
BASE = "http://127.0.0.1:%d" % PORT""")

rep("""def call(method, path, body=None, raw=None, ctype=None, headers=None,
         no_cookie=False):""",
    """def call(method, path, body=None, raw=None, ctype=None, headers=None,
         no_cookie=False, no_redirect=False):""")

rep("""    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read()""",
    """    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    op = NO_REDIRECT if no_redirect else OPENER
    try:
        with op.open(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read()""")

rep("""code, hdrs, body = call("GET", "/", no_cookie=True)
rec("未登录访问页面被重定向到登录页",""",
    """code, hdrs, body = call("GET", "/", no_cookie=True, no_redirect=True)
rec("未登录访问页面被重定向到登录页",""")

# ---- 3) 令牌改名后的样式断言
rep("""    ("/assets/app.css", b"--accent", "text/css"),""",
    """    ("/assets/app.css", b"--brand", "text/css"),""")

# ---- 4) 404 用例的身份前置（法务能力）
rep("""# 不存在的案件：批注应 404（原实现会静默成功）
code, _, body = call("POST", "/api/cases/999999/opinion", {"opinion": "x"})""",
    """# 不存在的案件：批注应 404（原实现会静默成功）
login_as("legal")            # opinion 归法务审查人，先切身份再验 404
code, _, body = call("POST", "/api/cases/999999/opinion", {"opinion": "x"})""")

# ---- 5) 权限受限页模板：断言放宽为「含权限受限语义」
rep("""code, _, body = call("GET", "/denied.html", no_cookie=True)
rec("权限受限页模板可渲染（占位符由服务端填充）",
    code == 200 and "{{NEED_LABEL}}".encode("utf-8") in body,
    "HTTP %s，%d 字节" % (code, len(body)))""",
    """code, _, body = call("GET", "/denied.html", no_cookie=True)
rec("权限受限页模板可渲染（占位符由服务端填充）",
    code == 200 and b"{{NEED_LABEL}}" in body and b"{{ALLOW_ROLES}}" in body,
    "HTTP %s，%d 字节，占位符 %s 个" % (
        code, len(body), body.count(b"{{")))""")

# ---- 6) 清理临时目录
rep("""httpd.shutdown()
httpd.server_close()

ok = len([x for x in log if x.startswith("[OK ]")])""",
    """httpd.shutdown()
httpd.server_close()

import shutil                                                          # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)

ok = len([x for x in log if x.startswith("[OK ]")])""")

# 兜底：若末尾结构不同，直接在 shutil 缺失时插入
if "shutil.rmtree(TMP" not in c:
    c = c.replace("httpd.shutdown()", """import shutil
shutil.rmtree(TMP, ignore_errors=True)
httpd.shutdown()""", 1)
    log.append("ok   (fallback) 插入临时目录清理")

with io.open(P, "w", encoding="utf-8") as f:
    f.write(c)
with io.open(os.path.join(ROOT, "verify", "http_patch.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("http patch done")
