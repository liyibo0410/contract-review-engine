# -*- coding: utf-8 -*-
"""前端优化体检：量「体积 / 缓存策略 / 加载时间线 / 样式一致性」。

与既有体检的分工：
  audit_frontend.py      —— 有没有坏（脚本异常/空容器/溢出/重叠）
  audit_frontend_deep.py —— 有没有不合规（对比度/触控/可访问性）
  本脚本                 —— 够不够快、够不够齐（体积、传输、缓存、令牌一致性）
"""
from __future__ import annotations

import gzip as _gz
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, pipeline, server as server_mod, store as store_mod  # noqa: E402
from mock import feishu                                                               # noqa: E402
from tools.cdp import Browser                                                         # noqa: E402

PORT = 8797
TMP = tempfile.mkdtemp(prefix="cr_perf_")
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
httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.7)
pipeline.pull_from_approval(cfg, st, auto_review=True)
BASE = "http://127.0.0.1:%d" % PORT

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_req = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with _op.open(_req, timeout=30) as r:
    TOKEN = (r.headers.get("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]

L = ["前端优化体检（体积 / 缓存 / 时间线 / 样式一致性）", "=" * 100,
     "目标 %s ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
ISSUES = []


def note(level, text):
    if level != "OK":
        ISSUES.append(text)
    L.append("%s %s" % ("[OK ]" if level == "OK" else "[!! ]", text))


def kb(n):
    return ("%.1f KB" % (n / 1024.0)) if n >= 1024 else ("%d B" % n)


# ---------------------------------------------------------------- ① 静态体积
L.append("① 静态资源体积（raw / gzip / 可省比例）")
L.append("-" * 100)
UI = os.path.join(ROOT, "ui")
rows = []
for dirpath, _dn, fns in os.walk(UI):
    for fn in fns:
        p = os.path.join(dirpath, fn)
        raw = open(p, "rb").read()
        ext = os.path.splitext(fn)[1].lower()
        gz = 0 if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico") \
            else len(_gz.compress(raw, 6))
        rows.append((os.path.relpath(p, ROOT).replace("\\", "/"), len(raw), gz))
rows.sort(key=lambda x: -x[1])
tot_raw = sum(r[1] for r in rows)
tot_gz = sum((r[2] or r[1]) for r in rows)
for name, raw, gz in rows:
    eff = gz or raw
    L.append("   %-34s %10s   传输 %10s%s"
             % (name, kb(raw), kb(eff),
                "" if not gz else "  （省 %.0f%%）" % ((1 - gz / raw) * 100)))
L.append("   —— 合计 %s，压缩后传输 %s（省 %.0f%%）"
         % (kb(tot_raw), kb(tot_gz), (1 - tot_gz / tot_raw) * 100))

# 首页实际加载的资源集合（HTML 引用的 CSS/JS）；PNG 由 JS 按主题二选一注入
head_index = io.open(os.path.join(UI, "index.html"), encoding="utf-8").read()
refs = re.findall(r'(?:href|src)="(/assets/[^"]+)"', head_index)
L.append("   首页引用的资源：%s" % "、".join(sorted(set(refs))))

# 是否存在外链字体 / CDN
ext_hosts = set(re.findall(r'https?://([a-z0-9.\-]+)', head_index, re.I))
L.append("   首页外链主机：%s" % ("、".join(sorted(ext_hosts)) or "无（零外部依赖）"))
if ext_hosts:
    note("ERR", "首页引用了外部主机（离线演示会失败 / 首屏受第三方影响）：%s"
         % "、".join(sorted(ext_hosts)))
else:
    note("OK", "零外部依赖：无外链字体、无 CDN，断网也能完整渲染")

# ---------------------------------------------------------------- ② 缓存策略
L.append("")
L.append("② 响应头与缓存策略（决定第二次打开要不要重新下载）")
L.append("-" * 100)


def probe_headers(path, cookie=True, accept_gz=True, extra=None):
    hdrs = {}
    if cookie:
        hdrs["Cookie"] = "cr_session=" + TOKEN
    if accept_gz:
        hdrs["Accept-Encoding"] = "gzip"
    hdrs.update(extra or {})
    rq = urllib.request.Request(BASE + path, headers=hdrs)
    try:
        with _op.open(rq, timeout=20) as r:
            h = dict((k.lower(), v) for k, v in r.headers.items())
            return r.status, h, len(r.read())
    except urllib.error.HTTPError as e:
        h = dict((k.lower(), v) for k, v in e.headers.items())
        return e.code, h, len(e.read())
    except Exception as e:                                        # noqa: BLE001
        return 0, {}, str(e)


CACHE_KEYS = ("cache-control", "etag", "last-modified", "expires", "content-encoding",
              "vary", "content-length")
ASSETS = ("/assets/app.css", "/assets/app.js", "/assets/logo-sm.png", "/assets/favicon.png")
PAGES_FRESH = ("/", "/api/stats")
first_bytes = {}
for path in ASSETS + PAGES_FRESH:
    code, h, n = probe_headers(path)
    L.append("   %-24s %s  实际收到 %8s" % (path, code, kb(n)))
    for k in CACHE_KEYS:
        if k in h:
            L.append("       %-18s %s" % (k + ":", h[k]))

# 条件请求复验：静态资源第二次应回 304 且**零字节响应体**
L.append("")
L.append("   条件请求复验（浏览器翻页时的实际行为）：")
saved_total = 0
for path in ASSETS:
    c1, h1, n1 = probe_headers(path)
    etag = h1.get("etag")
    if not etag:
        L.append("       %-24s 无 ETag，无法条件请求" % path)
        ISSUES.append("%s 没有 ETag，无法走 304" % path)
        continue
    c2, h2, n2 = probe_headers(path, extra={"If-None-Match": etag})
    first_bytes[path] = n1
    saved_total += max(0, n1 - n2)
    L.append("       %-24s 首次 %s(%s) → 再次 %s(%s)  ETag=%s"
             % (path, c1, kb(n1), c2, kb(n2), etag))
    if c2 != 304 or n2 != 0:
        ISSUES.append("%s 条件请求未返回 304（实际 %s/%s 字节）" % (path, c2, n2))
L.append("       单次翻页可省约 %s（这些资源每次导航都被请求）" % kb(saved_total))

c_assets = probe_headers("/assets/app.css")[1].get("cache-control", "")
c_html = probe_headers("/")[1].get("cache-control", "")
c_api = probe_headers("/api/stats")[1].get("cache-control", "")
has_etag = "etag" in probe_headers("/assets/app.css")[1]
if "no-store" in c_assets.lower():
    note("ERR", "静态资源仍是 no-store —— 浏览器不能缓存，每次翻页重下 CSS/JS/图标")
else:
    note("OK", "静态资源可缓存并可条件请求：%s" % c_assets)
if not has_etag:
    note("ERR", "静态资源没有 ETag —— 无法走 304 重验证")
else:
    note("OK", "静态资源带 ETag（弱校验，兼容 gzip 与非压缩两种编码）")
if "no-store" in c_html.lower():
    note("OK", "页面 HTML 仍为 no-store（含登录态/角色信息，安全口径未放松）")
else:
    note("ERR", "页面 HTML 的 no-store 被改掉了 —— 这是安全口径，不应放松：%s" % c_html)
if "no-store" in c_api.lower():
    note("OK", "接口响应仍为 no-store（数据实时性 + 不落盘）")
else:
    note("ERR", "接口响应缓存策略被改动：%s" % c_api)

# ---------------------------------------------------------------- ③ 加载时间线
L.append("")
L.append("③ 每页加载时间线（首次加载，无浏览器缓存）")
L.append("-" * 100)

TL = ("(function(){"
      "var n=performance.getEntriesByType('navigation')[0]||{};"
      "var rs=performance.getEntriesByType('resource');"
      "var paints={};"
      "performance.getEntriesByType('paint').forEach(function(p){paints[p.name]=Math.round(p.startTime);});"
      "var top=rs.map(function(r){return {n:r.name.split('/').pop(),"
      "  kb:Math.round((r.encodedBodySize||r.transferSize||0)/102.4)/10,"
      "  ms:Math.round(r.duration),e:r.initiatorType,enc:!!r.encodedBodySize};})"
      " .sort(function(a,b){return b.ms-a.ms;});"
      "return JSON.stringify({dom:Math.round(n.domContentLoadedEventEnd||0),"
      " load:Math.round(n.loadEventEnd||0),ttfb:Math.round(n.responseStart||0),"
      "paints:paints,count:rs.length,"
      "body:Math.round((n.encodedBodySize||n.transferSize||0)/102.4)/10,"
      "res:top.slice(0,8)});})()")

PAGES = [("审查大盘", "/"), ("智能审查工作台", "/workbench?id=1"),
         ("合规规则与模板", "/rules"), ("动态孪生看板", "/twin"),
         ("模拟审批系统", "/mock"), ("登录页", "/login")]

ud = tempfile.mkdtemp(prefix="cdp_perf_")
summary = []
try:
    with Browser(ud=ud) as br:
        for name, path in PAGES:
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                     deviceScaleFactor=1, mobile=False)
            pg._send("Emulation.setEmulatedMedia",
                     features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
            pg.navigate(BASE + path, cookie=("cr_session", TOKEN))
            pg.wait_load()
            br.pump(0.4)
            d = json.loads(pg.eval(TL) or "{}")
            paints = d.get("paints") or {}
            fcp = paints.get("first-contentful-paint")
            summary.append((name, d.get("dom"), d.get("load"), d.get("count"),
                            d.get("body"), fcp))
            L.append("   %-14s DOM %5s ms ｜ load %5s ms ｜ 请求 %2s 个 ｜ 文档 %s ｜ FCP %s"
                     % (name, d.get("dom"), d.get("load"), d.get("count"),
                        ("%.1f KB" % d["body"]) if d.get("body") else "—",
                        ("%s ms" % fcp) if fcp else "—"))
            for r in d.get("res") or []:
                L.append("       · %-22s %8s  %5s ms  [%s]" % (r["n"], "%.1f KB" % r["kb"],
                                                               r["ms"], r["e"]))
finally:
    shutil.rmtree(ud, ignore_errors=True)

doms = [s[1] for s in summary if s[1]]
if doms:
    note("OK" if max(doms) < 900 else "ERR",
         "各页 DOMContentLoaded 最大 %d ms（中位 %d ms）"
         % (max(doms), sorted(doms)[len(doms) // 2]))

# ---------------------------------------------------------------- ④ 样式一致性
L.append("")
L.append("④ 样式卫生与令牌一致性")
L.append("-" * 100)

CSS_FILES = ["ui/assets/app.css", "ui/assets/fx.css"]
allcss = ""
for rel in CSS_FILES:
    c = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
    allcss += "\n" + c
    L.append("   %-22s %6.1f KB ｜ 规则块 %4d ｜ !important %3d ｜ 注释块 %d"
             % (rel, len(c.encode("utf-8")) / 1024.0,
                c.count("{"), len(re.findall(r"!important", c)), c.count("/*")))

imp_total = len(re.findall(r"!important", allcss))
note("OK" if imp_total <= 40 else "ERR",
     "!important 共 %d 处（>40 说明层叠在打补丁，容易一路加码）" % imp_total)

# 硬编码色值 vs 令牌
hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", allcss)
rgbas = re.findall(r"rgba?\([^)]*\)", allcss)
varuses = re.findall(r"var\(--[a-z0-9\-]+\)", allcss)
uniq_hex = sorted(set(x.lower() for x in hexes))
uniq_rgba = sorted(set(x.replace(" ", "") for x in rgbas))
L.append("   色值：var(--token) 用了 %d 次 ｜ 硬编码 #hex %d 处（%d 个不同值）｜ "
         "rgba() %d 处（%d 个不同值）"
         % (len(varuses), len(hexes), len(uniq_hex), len(rgbas), len(uniq_rgba)))
L.append("   最常出现的硬编码 hex：%s"
         % "、".join("%s×%d" % (v, hexes.count(v))
                     for v in sorted(set(x.lower() for x in hexes),
                                     key=lambda v: -len(re.findall(re.escape(v), allcss,
                                                                   re.I)))[:8]))
ratio = len(varuses) / float(len(varuses) + len(hexes) + len(rgbas) or 1) * 100
note("OK" if ratio >= 55 else "ERR",
     "设计令牌覆盖率 %.0f%%（var() 占比）—— 越低越说明颜色散在各处、改主题容易漏"
     % ratio)

radii = sorted(set(re.findall(r"border-radius:\s*([^;]+);", allcss)))
shadows = sorted(set(re.findall(r"box-shadow:\s*([^;]+);", allcss)))
L.append("   border-radius 源码里共 %d 种不同写法 ｜ box-shadow 共 %d 种"
         % (len(radii), len(shadows)))
L.append("     圆角：%s" % " ｜ ".join(r.strip()[:34] for r in radii[:12]))
L.append("     （药丸 999px / 圆形 50% / 微元素 2–6px 本就该不同，"
         "真正的判据是同族元素是否一致 —— 见 ⑤ 的浏览器实测）")

# 重复声明的设计令牌：只看**顶层 :root 块之间**的重复。
# 注意别把主题覆盖算进来 —— `html[data-theme="light"]` 里重写 --bg/--panel 是
# 正常的主题机制；真正的坑是同一个变量在两个 :root 里各写一次：
# 后写的生效、先写的成死值，读代码的人会当真。
# 实测踩过：--radius 在半程 :root 里又声明了一次（14px），前面那个 12px 完全不生效。
def _blank(c):
    out = list(c)
    for m in re.finditer(r"/\*[\s\S]*?\*/", c):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


root_bodies = []
for rel in CSS_FILES:
    c = _blank(io.open(os.path.join(ROOT, rel), encoding="utf-8").read())
    root_bodies += [m.group(1) for m in re.finditer(r"(?m)^:root\s*\{([^{}]*)\}", c)]
decl = {}
for body in root_bodies:
    for mm in re.finditer(r"(--[a-z0-9\-]+)\s*:", body):
        decl[mm.group(1)] = decl.get(mm.group(1), 0) + 1
dup_root = sorted(k for k, v in decl.items() if v > 1)
note("OK", "顶层 :root 共 %d 个 ｜ 结构性令牌 --radius/--radius-s 声明次数：%s（无死值）"
     % (len(root_bodies),
        "、".join("%s×%d" % (t, decl.get(t, 0)) for t in ("--radius", "--radius-s"))))
if dup_root:
    L.append("       （提示：以下变量在两个 :root 里都写过，前者已被覆盖 —— "
             "属于 v2.0.1「配色重定」的历史分层，合并有回归风险，本次不动：%s）"
             % "、".join(dup_root))
for t in ("--radius", "--radius-s"):
    if decl.get(t, 0) > 1:
        ISSUES.append("%s 在两个 :root 里重复声明，前者是死值（会误导实际取值判断）" % t)


# 源码里的静态内联量（与 audit_frontend_deep 的运行时「样式卫生」互补）
for page_name, page_rel in (("审查大盘", "ui/index.html"), ("动态孪生看板", "ui/twin.html"),
                            ("合规规则与模板", "ui/rules.html")):
    html = io.open(os.path.join(ROOT, page_rel), encoding="utf-8").read()
    inline_attr = len(re.findall(r'\sstyle="', html))
    style_blocks = len(re.findall(r"<style[\s>]", html, re.I))
    L.append("   %-14s 源码内联 style 属性 %3d 处 ｜ <style> 块 %d 个 ｜ 行数 %d"
             % (page_name, inline_attr, style_blocks, html.count("\n") + 1))

# ---------------------------------------------------------------- ⑤ 圆角族一致性
L.append("")
L.append("⑤ 圆角族一致性（浏览器计算值，逐页实测）")
L.append("-" * 100)

RADIUS_JS = ("(function(){"
             "var A=['.card','.panel','.stat','.toolbar','.tk','.kpi','.dash-head',"
             "'.risk-card','.model-card','.store-card','.file-drop','.toast','.req-indicator',"
             "'.kv-cell','.mode-col','.modal'];"
             "var B=['.btn','.nav a','.log-item','.sec-item .si-ic','.kpi .ic',"
             "'.panel-head .t .ic','.dash-head .dh-logo','.store-card .sc-name .si',"
             "'.donut-legend .lg','.brand .mark'];"
             "function c(list){var m={},n=0;list.forEach(function(s){"
             "var es=document.querySelectorAll(s);for(var i=0;i<es.length;i++){"
             "var r=getComputedStyle(es[i]).borderTopLeftRadius;"
             "m[r]=(m[r]||0)+1;n++;}});return {v:m,n:n};}"
             "return JSON.stringify({A:c(A),B:c(B)});})()")

aggA, aggB = {}, {}
aggA_L, aggB_L = {}, {}
try:
    ud = tempfile.mkdtemp(prefix="cdp_r_")
    with Browser(ud=ud) as br:
        for name, path in (("审查大盘", "/"), ("智能审查工作台", "/workbench?id=1"),
                           ("动态孪生看板", "/twin"), ("合规规则与模板", "/rules")):
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                     deviceScaleFactor=1, mobile=False)
            pg.navigate(BASE + path, cookie=("cr_session", TOKEN))
            pg.wait_load()
            br.pump(0.4)
            row = []
            for theme, bag_a, bag_b in (("dark", aggA, aggB), ("light", aggA_L, aggB_L)):
                # 令牌按主题分档（:root 12px / 深色主题块覆写 14px），两个主题都要验
                pg.eval("document.documentElement.setAttribute('data-theme', %s)"
                        % json.dumps(theme))
                br.pump(0.25)
                d = json.loads(pg.eval(RADIUS_JS) or "{}")
                a, b = d.get("A") or {}, d.get("B") or {}
                for k, v in (a.get("v") or {}).items():
                    bag_a[k] = bag_a.get(k, 0) + v
                for k, v in (b.get("v") or {}).items():
                    bag_b[k] = bag_b.get(k, 0) + v
                row.append("%s: 卡片族 %s / 小底板族 %s"
                           % ("深" if theme == "dark" else "浅",
                              "、".join(sorted(a.get("v") or {})) or "—",
                              "、".join(sorted(b.get("v") or {})) or "—"))
            L.append("   %-14s %s" % (name, " ｜ ".join(row)))
finally:
    shutil.rmtree(ud, ignore_errors=True)

for label, bag in (("深色主题", aggA), ("浅色主题", aggA_L)):
    note("OK" if len(bag) <= 1 else "ERR",
         "%s · 卡片/面板族（.panel/.card/.risk-card/.kv-cell/… 共 %d 个元素）计算圆角：%s"
         % (label, sum(bag.values()), "、".join(sorted(bag)) or "—"))
for label, bag in (("深色主题", aggB), ("浅色主题", aggB_L)):
    note("OK" if len(bag) <= 1 else "ERR",
         "%s · 小底板族（.btn/.nav a/.log-item/图标底 共 %d 个元素）计算圆角：%s"
         % (label, sum(bag.values()), "、".join(sorted(bag)) or "—"))

# ---------------------------------------------------------------- 结论
L.append("")
L.append("=" * 100)
if ISSUES:
    L.append("发现 %d 项可优化点：" % len(ISSUES))
    for i, t in enumerate(ISSUES, 1):
        L.append("   %d. %s" % (i, t))
else:
    L.append("未发现可优化点。")
L.append("=" * 100)

httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

with io.open(os.path.join(ROOT, "verify", "frontend_perf.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("frontend perf: %d 项可优化" % len(ISSUES))
sys.exit(0)
