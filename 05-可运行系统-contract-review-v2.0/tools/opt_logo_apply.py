# -*- coding: utf-8 -*-
"""logo 缩小并做端到端视觉验证（截图逐像素比对）。

流程：① 先截「优化前」顶栏/登录页 logo 区域（2x 截图）→ ② 缩图
      （原图备份到工作区临时目录）→ ③ 截「优化后」同区域
      → ④ 逐像素比对 + 存图供人眼复核。
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image, ImageChops                                          # noqa: E402

from app import config as cfg_mod, pipeline, server as server_mod, store as store_mod  # noqa: E402
from mock import feishu                                                               # noqa: E402
from tools.cdp import Browser                                                         # noqa: E402

BACKUP = r"D:\workbuddy work\2026-09-19-08-12-06\.workbuddy\_logo_orig"
TW, TH = 76, 78                # 覆盖最大显示（登录页 38x39）的 2 倍
SCALE = 2                      # 2x 截图，便于看出缩放损失
PORT = 8798
TMP = tempfile.mkdtemp(prefix="cr_logo_")
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
_rq = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with _op.open(_rq, timeout=30) as r:
    TOKEN = (r.headers.get("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]

TARGETS = [("topbar", "/", ".brand-logo"), ("login", "/login", "#loginLogo")]
SHOT = os.path.join(ROOT, "verify")
os.makedirs(SHOT, exist_ok=True)
L = ["logo 缩小 · 端到端视觉验证", "=" * 96,
     "目标尺寸 %dx%d（登录页 38x39 的 2 倍）｜ 截图 %dx 缩放" % (TW, TH, SCALE), ""]


def capture(br, path, sel, tag):
    """截取目标元素周围的区域（元素矩形外扩 12 CSS px，含边框与发光）。"""
    pg = br.new_page()
    pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
             deviceScaleFactor=SCALE, mobile=False)
    pg._send("Emulation.setEmulatedMedia",
             features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
    pg.navigate(BASE + path, cookie=("cr_session", TOKEN))
    pg.wait_load()
    br.pump(0.8)
    rect = pg.eval("(function(){var e=document.querySelector(%s);if(!e)return null;"
                   "var r=e.getBoundingClientRect();"
                   "return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height,"
                   "iw:e.naturalWidth||0,ih:e.naturalHeight||0,"
                   "dw:e.width||0,dh:e.height||0});})()" % json.dumps(sel))
    if not rect:
        L.append("   [ERR] %-14s 找不到 %s" % (tag, sel))
        return None, None
    d = json.loads(rect)
    r = pg._send("Page.captureScreenshot", format="png")
    data = (r.get("result") or {}).get("data") or r.get("data")
    im = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    m = 12 * SCALE
    box = (max(0, int(d["x"] * SCALE) - m), max(0, int(d["y"] * SCALE) - m),
           min(im.width, int((d["x"] + d["w"]) * SCALE) + m),
           min(im.height, int((d["y"] + d["h"]) * SCALE) + m))
    crop = im.crop(box)
    out = os.path.join(SHOT, "logo_%s.png" % tag)
    crop.save(out)
    L.append("   %-16s 显示 %sx%s CSS px ｜ 位图 %sx%s ｜ 裁图 %sx%s"
             % (tag, round(d["dw"]), round(d["dh"]), d["iw"], d["ih"],
                crop.width, crop.height))
    return crop, d


before, after, info = {}, {}, {}
try:
    ud = tempfile.mkdtemp(prefix="cdp_logo_b_")
    with Browser(ud=ud) as br:
        for t, p, s in TARGETS:
            before[t], info[t] = capture(br, p, s, "topbar_before" if t == "topbar"
                                        else "login_before")
    shutil.rmtree(ud, ignore_errors=True)

    # ---- 应用优化（原图先备份到工作区，不进交付物）
    os.makedirs(BACKUP, exist_ok=True)
    saved = []
    for name in ("logo-sm.png", "logo-sm-light.png"):
        src = os.path.join(ROOT, "ui", "assets", name)
        raw = open(src, "rb").read()
        with open(os.path.join(BACKUP, name), "wb") as f:
            f.write(raw)
        im = Image.open(src).convert("RGBA").resize((TW, TH), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        with open(src, "wb") as f:
            f.write(buf.getvalue())
        saved.append((name, len(raw), len(buf.getvalue())))

    ud = tempfile.mkdtemp(prefix="cdp_logo_a_")
    with Browser(ud=ud) as br:
        for t, p, s in TARGETS:
            after[t], _ = capture(br, p, s, "topbar_after" if t == "topbar"
                                  else "login_after")
    shutil.rmtree(ud, ignore_errors=True)

    L.append("")
    L.append("① 体积")
    for name, b, a in saved:
        L.append("   %-22s %.1f KB → %.1f KB（省 %.1f KB / %.0f%%）"
                 % (name, b / 1024.0, a / 1024.0, (b - a) / 1024.0,
                    (1 - a / float(b)) * 100))

    L.append("")
    L.append("② 显示区域逐像素差异（%dx 截图，含边框与发光/阴影）" % SCALE)
    for t, _p, _s in TARGETS:
        b, a = before.get(t), after.get(t)
        if b is None or a is None:
            L.append("   %-14s 缺图，跳过" % t)
            continue
        if b.size != a.size:
            a = a.resize(b.size)
        diff = ImageChops.difference(b, a)
        px = list(diff.getdata())
        n = len(px)
        mx = max((max(p) for p in px), default=0)
        mean = sum(sum(p) for p in px) / float(n * 3)
        over16 = sum(1 for p in px if max(p) > 16) / float(n) * 100
        over32 = sum(1 for p in px if max(p) > 32) / float(n) * 100
        L.append("   %-14s 最大差 %3d ｜ 平均差 %5.2f ｜ >16 的像素 %.3f%% ｜ >32 的像素 %.3f%%"
                 % (t, mx, mean, over16, over32))
        L.append("        裁图：verify/logo_%s_before.png ↔ verify/logo_%s_after.png" % (t, t))
finally:
    httpd.shutdown()
    httpd.server_close()
    shutil.rmtree(TMP, ignore_errors=True)

L.append("")
L.append("原图备份：%s（工作区目录，不随交付物走）" % BACKUP)
with io.open(os.path.join(SHOT, "logo_apply.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("logo apply done")
