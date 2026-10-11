# -*- coding: utf-8 -*-
"""探 /api/selftest 为什么返回非 JSON（verify_http 里这一步挂了）。"""
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

PORT = 8804
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
time.sleep(0.7)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:%d" % PORT


def call(method, path, body=None, cookie=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with op.open(r, timeout=timeout) as rr:
            return rr.status, dict(rr.headers), rr.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:                                            # noqa: BLE001
        return 0, {}, str(e).encode()


L = []
try:
    code, hdrs, body = call("POST", "/api/auth/login",
                            {"username": "admin", "password": "Admin@2026"})
    ck = (hdrs.get("Set-Cookie") or "").split(";")[0]
    L.append("登录 %s" % code)

    t0 = time.time()
    code, hdrs, body = call("GET", "/api/selftest", cookie=ck)
    ms = int((time.time() - t0) * 1000)
    L.append("GET /api/selftest → HTTP %s，%d 字节，耗时 %d ms" % (code, len(body), ms))
    L.append("Content-Type=%s ｜ Content-Encoding=%s"
             % (hdrs.get("Content-Type"), hdrs.get("Content-Encoding") or "无"))
    L.append("响应体前 400 字节：")
    L.append(repr(body[:400]))
    if body[:1] not in (b"{", b"["):
        L.append("→ 不是 JSON（首字节 %r）" % body[:1])
    try:
        j = json.loads(body)
        L.append("可解析：code=%s，data 类型=%s" % (j.get("code"), type(j.get("data")).__name__))
        if isinstance(j.get("data"), dict):
            L.append("  自检结果：%s / %s 项通过，%s ms"
                     % (j["data"].get("passed_count"), j["data"].get("total"),
                        j["data"].get("elapsed_ms")))
    except Exception as e:                                            # noqa: BLE001
        L.append("JSON 解析失败：%s" % e)
        # 直接在本进程里跑一次自检做对照
        import selftest
        r = selftest.run(ROOT)
        L.append("进程内直接跑自检：%s / %s 项通过"
                 % (r.get("passed_count"), r.get("total")))
except Exception as exc:                                              # noqa: BLE001
    import traceback
    L.append("异常：%s\n%s" % (exc, traceback.format_exc()[-800:]))
finally:
    httpd.shutdown()
    httpd.server_close()

with io.open(os.path.join(ROOT, "verify", "selftest_http_probe.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(L))
print("selftest http probe done")
