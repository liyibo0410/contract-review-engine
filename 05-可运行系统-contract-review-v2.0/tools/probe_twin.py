# -*- coding: utf-8 -*-
"""直接探 /api/twin 的响应结构，确认 http 字段是否存在（定位页面报错的根因）。"""
import io
import json
import os
import sys
import threading
import time
import urllib.request

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, server as server_mod, store as store_mod
from mock import feishu

PORT = 8797
cfg = cfg_mod.Config(ROOT)
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT
for k in ("data", "uploads", "parsed", "reports", "samples"):
    cfg.path(k)
st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)
httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.6)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:%d" % PORT


def req(method, path, body=None, cookie=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    with op.open(r, timeout=60) as rr:
        return rr.status, dict(rr.headers), rr.read()


L = []
try:
    code, hdrs, body = req("POST", "/api/auth/login",
                           {"username": "admin", "password": "Admin@2026"})
    ck = (hdrs.get("Set-Cookie") or "").split(";")[0]
    L.append("登录 HTTP %s，Cookie %s" % (code, "已取" if ck else "无"))
    code, hdrs, body = req("GET", "/api/twin", cookie=ck)
    L.append("/api/twin HTTP %s，%d 字节，Content-Encoding=%s"
             % (code, len(body), hdrs.get("Content-Encoding") or "无"))
    L.append("Content-Type=%s" % hdrs.get("Content-Type"))
    j = json.loads(body)
    L.append("顶层 JSON 键：%s" % list(j))
    d = j.get("data") or {}
    L.append("data 键：%s" % (list(d) if isinstance(d, dict) else type(d).__name__))
    L.append("")
    L.append("关键字段存在性：")
    for k in ("generated_at", "version", "pipeline", "stats", "rollup", "counts",
              "engines", "queue", "scheduler", "notify", "storage", "auth",
              "activity", "http"):
        v = d.get(k) if isinstance(d, dict) else None
        L.append("   %-12s %s" % (k, ("有（%d 项）" % len(v)) if isinstance(v, (list, dict))
                                  else ("有 %r" % (v,) if v is not None else "**缺失**")))
    if isinstance(d, dict) and "http" in d:
        L.append("   http = %s" % json.dumps(d["http"], ensure_ascii=False)[:300])
except Exception as exc:                                          # noqa: BLE001
    import traceback
    L.append("异常：%s" % exc)
    L.append(traceback.format_exc())
finally:
    httpd.shutdown()
    httpd.server_close()

with io.open(os.path.join(ROOT, "verify", "twin_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("twin probe done")
