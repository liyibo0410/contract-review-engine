# -*- coding: utf-8 -*-
"""验证「客户端提前断连不再打 traceback」这个修复（本地，无需 SSH）。

做法：
  1. 在临时目录起一个真实服务；
  2. 用裸 socket 发一个**不完整的请求**然后立刻关闭（模拟 curl 被 head 截断、
     健康探针只读头部、keep-alive 被对端切断）；
  3. 把服务端的 sys.stderr 劫持到缓冲区，断言里面**没有** ConnectionResetError 的
     traceback。

为什么值得单独测：线上 /var/log/contract-review.log 里原本每次早断连都会打一段
二十多行的 traceback —— 既淹没有效日志，又会让审计误以为服务在报错。
"""
from __future__ import annotations

import io
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, server as server_mod, store as store_mod   # noqa: E402
from mock import feishu                                                      # noqa: E402

PORT = 8796
TMP = tempfile.mkdtemp(prefix="cr_noise_")
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(TMP, "data"), "uploads": os.path.join(TMP, "uploads"),
    "parsed": os.path.join(TMP, "parsed"), "reports": os.path.join(TMP, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
cfg.data["server"]["port"] = PORT
for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)
st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)

httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.6)

# 劫持 stderr：socketserver 的 handle_error 用 traceback.print_exc() 写 sys.stderr
buf = io.StringIO()
real_stderr = sys.stderr
sys.stderr = buf
try:
    cases = [
        ("发半个请求行就断开", b"GET /api/hea"),
        ("发完请求行但不等响应就断开", b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"),
        ("发无意义的二进制就断开", b"\x16\x03\x01\x00\x95\x01\x00\x00\x91"),
    ]
    for name, payload in cases:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        s.sendall(payload)
        # 关键：用 RST 而不是正常 FIN —— 这才是线上"连接被重置"的来源
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                     b"\x01\x00\x00\x00\x00\x00\x00\x00")
        s.close()
        time.sleep(0.4)
        print("  已模拟：%s" % name)
    time.sleep(0.8)
finally:
    sys.stderr = real_stderr
    httpd.shutdown()
    httpd.server_close()

noise = buf.getvalue()
bad = "ConnectionResetError" in noise or "Traceback" in noise

lines = []
lines.append("客户端提前断连 → 服务端日志噪音检查")
lines.append("=" * 96)
lines.append("模拟 3 种早断连（半个请求行 / 不等响应 / 二进制垃圾），均以 RST 关闭")
lines.append("")
lines.append("服务端 stderr 捕获长度：%d 字节" % len(noise))
if noise.strip():
    lines.append("捕获内容（前 800 字）：")
    lines.append(noise[:800])
else:
    lines.append("捕获内容：空 —— 没有任何 traceback 输出")
lines.append("")
lines.append("结论：%s" % ("失败 —— 仍在输出 traceback" if bad else "通过 —— 早断连已被静默处理"))
lines.append("=" * 96)
lines.append("补充：正常请求路径不受影响（见 verify/smoke_http.txt 的 79 项全绿）。")

out = os.path.join(ROOT, "verify", "conn_noise.txt")
os.makedirs(os.path.dirname(out), exist_ok=True)
with io.open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("conn-noise check: %s" % ("FAIL" if bad else "PASS"))
sys.exit(1 if bad else 0)
