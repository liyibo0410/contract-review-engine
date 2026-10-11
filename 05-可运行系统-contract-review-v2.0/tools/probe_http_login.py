# -*- coding: utf-8 -*-
"""定位 HTTP 层异常：起服务、打登录、把响应体（含 traceback）写文件。"""
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, server as server_mod, store as store_mod
from mock import feishu

PORT = 8795
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
time.sleep(0.5)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:%d" % PORT
log = []


def hit(method, path, body=None, cookie=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with op.open(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:                                           # noqa: BLE001
        return 0, {}, str(e).encode()


for m, p, b in [("GET", "/api/health", None),
                ("POST", "/api/auth/login", {"username": "admin", "password": "Admin@2026"})]:
    code, h, body = hit(m, p, b)
    log.append("=== %s %s -> %s ===" % (m, p, code))
    log.append("headers: %s" % json.dumps({k: v for k, v in h.items()
                                          if k.lower() in ("set-cookie", "content-type")},
                                         ensure_ascii=False))
    log.append(body.decode("utf-8", "ignore")[:3000])
    log.append("")

httpd.shutdown()
httpd.server_close()
with io.open(os.path.join(ROOT, "verify", "http_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("probe done")
