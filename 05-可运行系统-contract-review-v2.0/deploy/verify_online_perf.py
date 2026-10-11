# -*- coding: utf-8 -*-
"""线上前端优化复验（**只走公网 HTTP，不需要 SSH**）。

回答一个具体问题：这次的前端优化（ETag 条件请求 / 去 charts.js / logo 瘦身 / 圆角令牌）
是不是真的到了线上 —— 用响应头与字节数就能证明，不必登服务器。
"""
from __future__ import annotations

import gzip as _gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CR_PUBLIC", "http://39.96.200.45:8160")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "verify", "online_perf.txt")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

L = ["线上前端优化复验（公网 HTTP，无 SSH）", "=" * 96,
     "目标 %s ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
ISSUES = []


def get(path, extra=None, cookie=None, tries=3):
    """返回 (状态, 响应头, 线上实际传输的字节数)。

    **必须带退避重试**：公网出口偶发 `URLError`（连接层失败，状态按 0 计）很常见，
    实测一轮复验里就出现过 `/assets/app.css` 状态 0 —— 那是网络抖动，不是服务端问题，
    不重试就会把它报成「没启用 ETag」。只重试「没拿到响应」；
    4xx/5xx 是服务端明确答复，不重试。
    """
    h = dict(extra or {})
    h["Accept-Encoding"] = "gzip"
    if cookie:
        h["Cookie"] = "cr_session=" + cookie
    last = ""
    for i in range(tries):
        rq = urllib.request.Request(BASE + path, headers=h)
        try:
            with OP.open(rq, timeout=25) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        except Exception as e:                                    # noqa: BLE001
            last = str(e)
            time.sleep(1.2 * (i + 1))
    return 0, {}, last.encode()


def body_of(raw, hdrs):
    """解压响应体。

    必须做这一步 —— 带了 `Accept-Encoding: gzip` 却拿压缩字节去 json.loads
    会解析失败（版本读成 '?'），而去 gzip 二进制里搜字符串则**必然搜不到**，
    「不含某脚本」会变成假通过。
    """
    if (hdrs.get("content-encoding") or "").lower() == "gzip":
        try:
            return _gzip.decompress(raw)
        except Exception:                                          # noqa: BLE001
            return raw
    return raw


def note(ok, text):
    if not ok:
        ISSUES.append(text)
    L.append("%s %s" % ("[OK ]" if ok else "[!! ]", text))


def login(user, pwd):
    """登录拿会话 Cookie。

    必须做：这些页面是登录后才有的，未登录时 / 与 /workbench 都会 302 到登录页，
    于是「页面里含不含某脚本」的检查会**在登录页上通过** —— 看着全绿，其实没测到东西。
    """
    d = json.dumps({"username": user, "password": pwd}).encode()
    rq = urllib.request.Request(BASE + "/api/auth/login", data=d,
                               headers={"Content-Type": "application/json",
                                        "Accept-Encoding": "gzip"}, method="POST")
    try:
        with OP.open(rq, timeout=25) as r:
            sc = r.headers.get("Set-Cookie") or ""
            return sc.split("=", 1)[-1].split(";")[0]
    except Exception as e:                                        # noqa: BLE001
        L.append("   [WARN] 登录失败：%s" % e)
        return ""


COOKIE = login(os.environ.get("CR_USER", "admin"),
               os.environ.get("CR_PASS", "Admin@2026"))
L.append("会话：%s" % ("已获取（%s）" % os.environ.get("CR_USER", "admin") if COOKIE
                    else "**未获取 —— 后续页面检查不可信**"))
note(bool(COOKIE), "已登录取到会话 Cookie")
L.append("")


# ---------------------------------------------------------------- 版本
code, h, body = get("/api/health")
try:
    ver = json.loads(body_of(body, h))["data"]["version"]
except Exception:                                                 # noqa: BLE001
    ver = "?"
local_ver = "?"
try:
    sys.path.insert(0, ROOT)
    from app import __version__ as local_ver                       # noqa: E402
except Exception:                                                  # noqa: BLE001
    pass
L.append("① 版本口径")
note(code == 200 and ver == local_ver,
     "线上 /api/health 报 version=%s ｜ 交付版本=%s" % (ver, local_ver))

# ---------------------------------------------------------------- 缓存与 304
L.append("")
L.append("② 静态资源缓存策略与条件请求（本次优化的重点）")
ASSETS = ["/assets/app.css", "/assets/app.js", "/assets/fx.css",
          "/assets/logo-sm.png", "/assets/favicon.png", "/assets/charts.js"]
saved = 0
asset_bad = 0
for p in ASSETS:
    c1, h1, b1 = get(p)
    cc = h1.get("cache-control", "")
    etag = h1.get("etag")
    L.append("   %-24s %s ｜ %8s ｜ cache-control=%s ｜ etag=%s"
             % (p, c1, "%.1f KB" % (len(b1) / 1024.0), cc or "—", etag or "—"))
    if c1 != 200 or "no-store" in cc.lower() or not etag:
        asset_bad += 1
        ISSUES.append("%s 未启用 ETag 条件请求（状态 %s，cache-control=%s）" % (p, c1, cc))
        continue
    c2, h2, b2 = get(p, extra={"If-None-Match": etag})
    saved += max(0, len(b1) - len(b2))
    bad = not (c2 == 304 and len(b2) == 0)
    if bad:
        asset_bad += 1
        ISSUES.append("%s 条件请求未返回 304 空响应（实际 %s / %d 字节）" % (p, c2, len(b2)))
    L.append("       条件请求：%s → %s（%s）%s"
             % (c1, c2, "%.1f KB" % (len(b2) / 1024.0), "   ← 异常" if bad else ""))
L.append("   合计：%d 个静态资源全部命中 304 时，单次导航省 ≈ %.1f KB（线上传输口径）"
         % (len(ASSETS), saved / 1024.0))
note(asset_bad == 0, "静态资源可缓存且全部支持 304 条件请求")

# ---------------------------------------------------------------- 页面与接口仍是 no-store
L.append("")
L.append("③ 安全口径未被放松（页面与接口仍应 no-store）")
for p in ("/", "/login", "/api/health"):
    c, hh, _b = get(p, cookie=COOKIE)
    cc = hh.get("cache-control", "")
    L.append("   %-16s %s ｜ cache-control=%s" % (p, c, cc or "—"))
    if "no-store" not in cc.lower():
        ISSUES.append("%s 的 Cache-Control 不是 no-store（安全口径被放松）" % p)
note("no-store" in get("/", cookie=COOKIE)[1].get("cache-control", "").lower(),
     "页面 HTML 与接口响应仍为 no-store")

# ---------------------------------------------------------------- logo 体积
L.append("")
L.append("④ logo 瘦身是否生效（优化前 23.3 KB / 优化后应 ≈ 11.5 KB）")
_c, _h, b = get("/assets/logo-sm.png")
kb = len(b) / 1024.0
L.append("   /assets/logo-sm.png 实际 %d 字节（%.1f KB）" % (len(b), kb))
note(kb < 16, "logo 已瘦身（%.1f KB%s）" % (kb, "" if kb < 16 else " —— 仍是旧的大图"))

# ---------------------------------------------------------------- workbench 不再引 charts.js
L.append("")
L.append("⑤ 按页裁剪：workbench 不应再引 charts.js")
_c1, _h1, dash = get("/", cookie=COOKIE)
_c2, h2w, wb = get("/workbench?id=1", cookie=COOKIE)
txt_dash = body_of(dash, _h1).decode("utf-8", "ignore")
txt_wb = body_of(wb, h2w).decode("utf-8", "ignore")
dash_ok = "审查大盘" in txt_dash
wb_ok = "智能审查工作台" in txt_wb
L.append("   大盘落点校验：含「审查大盘」=%s（说明带会话拿到了真页面，不是登录页）" % dash_ok)
L.append("   工作台落点校验：含「智能审查工作台」=%s" % wb_ok)
note(dash_ok and wb_ok, "两份页面都确认为**登录后**的真实页面（不是被 302 到登录页）")
L.append("   /（大盘）          返回 %6d 字节 → 解压 %6d 字节 ｜ 含 charts.js：%s"
         % (len(dash), len(txt_dash.encode("utf-8")), "/assets/charts.js" in txt_dash))
L.append("   /workbench?id=1   返回 %6d 字节 → 解压 %6d 字节 ｜ 含 charts.js：%s"
         % (len(wb), len(txt_wb.encode("utf-8")), "/assets/charts.js" in txt_wb))
note("/assets/charts.js" not in txt_wb,
     "workbench 已去除未使用的 charts.js")
note("/assets/charts.js" in txt_dash,
     "大盘仍引用 charts.js（它的 CH.donut / CH.hbars 是真在用，没被误删）")
# logo 路径不在页面 HTML 里 —— 顶栏那个 <img> 是 app.js 运行时注入的，
# 所以要去 app.js 里查（写断言前先确认「这段文本到底该出现在哪个文件里」）。
_cj, hj, js = get("/assets/app.js")
txt_js = body_of(js, hj).decode("utf-8", "ignore")
note("/assets/logo-sm.png" in txt_js,
     "app.js 仍引用 logo 路径（静态资源注入点未被改坏）")

L.append("")
L.append("=" * 96)
L.append("复验结果：%d 项未通过" % len(ISSUES))
for i, t in enumerate(ISSUES, 1):
    L.append("   %d. %s" % (i, t))
L.append("=" * 96)

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("online perf: %d issues" % len(ISSUES))
sys.exit(0 if not ISSUES else 1)
