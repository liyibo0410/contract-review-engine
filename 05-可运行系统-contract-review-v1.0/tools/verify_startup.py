# -*- coding: utf-8 -*-
"""启动入口验证：以用户真实使用方式运行 run.py，确认服务可用后关闭。

    python tools/verify_startup.py
结果写入 verify/startup.txt
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8795
log = []

proc = subprocess.Popen(
    [sys.executable, "-u", "run.py", "--port", str(PORT)],
    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = "http://127.0.0.1:%d" % PORT

ok = False
body = b""
for _ in range(40):
    time.sleep(0.5)
    try:
        with opener.open(base + "/api/health", timeout=5) as r:
            body = r.read()
            ok = True
            break
    except Exception:                                            # noqa: BLE001
        if proc.poll() is not None:
            break

if ok:
    h = json.loads(body)["data"]
    log.append("OK  run.py 启动成功，/api/health HTTP 200")
    log.append("    版本 %s ｜ PDF 通道 %s ｜ 规则类型 %s" % (
        h["version"], "可用" if h["browser_for_pdf"] else "降级", ",".join(h["rules_types"])))
    for path, needle in [("/", "合同审查大盘"), ("/workbench?id=1", "workbench"),
                         ("/rules", "rules")]:
        try:
            with opener.open(base + path, timeout=10) as r:
                txt = r.read().decode("utf-8", "ignore")
            log.append("%s  GET %s  %d 字节%s" % (
                "OK " if (needle in txt) else "ERR", path, len(txt),
                "" if needle in txt else "（未找到 %s）" % needle))
        except Exception as exc:                                 # noqa: BLE001
            log.append("ERR  GET %s  %s" % (path, exc))
else:
    log.append("ERR  run.py 未能在 20 秒内响应 /api/health")
    try:
        out = proc.stdout.read(4000).decode("utf-8", "ignore")
        log.append("----- run.py 输出 -----")
        log.append(out)
    except Exception:                                            # noqa: BLE001
        pass

proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:                                                # noqa: BLE001
    proc.kill()
log.append("已关闭启动进程")

os.makedirs(os.path.join(ROOT, "verify"), exist_ok=True)
with io.open(os.path.join(ROOT, "verify", "startup.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("startup verify done: %s" % ok)
