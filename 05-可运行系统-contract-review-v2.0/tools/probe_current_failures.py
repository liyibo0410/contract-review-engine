# -*- coding: utf-8 -*-
"""定位当前 3 个冒烟失败（在「并行改动」背景下先分清是谁的问题）：
  A) 静态页面 / 找不到 needle
  B) /api/selftest 57/58（哪一项没过）
  C) /api/pdf/check stdlib=False
"""
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

L = []

# ---------------- A) needle 是否真的在文件里
needle = "衡鉴 V2.1.7"
p = os.path.join(ROOT, "ui", "index.html")
raw = io.open(p, "rb").read()
txt = raw.decode("utf-8", "replace")
L.append("=== A) 静态页面 / ===")
L.append("index.html 大小 %d 字节" % len(raw))
L.append("needle=%r 在文件里？%s" % (needle, needle in txt))
if needle not in txt:
    m = re.search(r"<title>(.*?)</title>", txt, re.S)
    L.append("  <title> 实际内容：%r" % (m.group(1) if m else "无"))
    # 看空格是不是 NBSP/全角
    for mm in re.finditer(r"衡鉴(.{0,12})", txt):
        seg = mm.group(0)
        codes = " ".join("U+%04X" % ord(ch) for ch in seg)
        L.append("  片段 %r → %s" % (seg, codes))
        break
else:
    idx = txt.index(needle)
    L.append("  首次出现位置 %d，上下文 %r" % (idx, txt[max(0, idx - 30):idx + 30]))

# ---------------- B/C) 起服务查接口
from app import config as cfg_mod, server as server_mod, store as store_mod
from mock import feishu

PORT = 8805
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
BASE = "http://127.0.0.1:%d" % PORT
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, cookie=None, timeout=240):
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


try:
    code, hdrs, body = call("POST", "/api/auth/login",
                            {"username": "admin", "password": "Admin@2026"})
    ck = (hdrs.get("Set-Cookie") or "").split(";")[0]

    code, hdrs, body = call("GET", "/", cookie=ck)
    L.append("")
    L.append("  服务端返回 /  ：HTTP %s，%d 字节，Content-Encoding=%s"
             % (code, len(body), hdrs.get("Content-Encoding") or "无"))
    L.append("  响应体里含 needle？%s" % (needle.encode() in body))
    L.append("  响应体头 120 字节：%r" % body[:120])

    L.append("")
    L.append("=== B) /api/selftest ===")
    code, _, body = call("GET", "/api/selftest", cookie=ck)
    if body[:1] == b"{":
        d = json.loads(body)["data"]
        L.append("  %s / %s 项通过，%s ms" % (d["passed_count"], d["total"], d["elapsed_ms"]))
        for c in d["checks"]:
            if not c["passed"]:
                L.append("  [未通过] %s\n           %s" % (c["name"], c["detail"][:220]))
    else:
        L.append("  非 JSON：%r" % body[:160])

    L.append("")
    L.append("=== C) /api/pdf/check ===")
    code, _, body = call("GET", "/api/pdf/check", cookie=ck)
    L.append("  HTTP %s → %s" % (code, body.decode("utf-8", "ignore")[:400]))
except Exception as exc:                                              # noqa: BLE001
    import traceback
    L.append("异常：%s\n%s" % (exc, traceback.format_exc()[-900:]))
finally:
    httpd.shutdown()
    httpd.server_close()

with io.open(os.path.join(ROOT, "verify", "current_failures.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(L))
print("probe done")
