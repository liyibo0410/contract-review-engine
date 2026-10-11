# -*- coding: utf-8 -*-
"""线上日志噪音复验：确认「客户端早断连」不会在服务端打出 traceback。

方法：不靠时间戳（systemd append 日志没有逐行时间），改用量差法 ——
  ① 记下日志里 "Traceback" 的出现次数与总行数；
  ② 从公网**故意制造**若干次早断连（连上就断 / 只发半个请求头就断）；
  ③ 等几秒再数一次。次数不变 = 服务端确实把它吞掉了；增加 = 噪音回归。

用法：set CR_SSH_PASS=... 后运行。
"""
from __future__ import annotations

import io
import os
import socket
import sys
import time

try:
    import paramiko
except ImportError:                                            # noqa: BLE001
    sys.exit("缺少 paramiko")

HOST = os.environ.get("CR_HOST", "39.96.200.45")
PORT = int(os.environ.get("CR_PORT", "8160"))
SSH_USER = os.environ.get("CR_SSH_USER", "root")
LOGFILE = "/var/log/contract-review.log"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "verify", "remote_noise.txt")

L = ["线上日志噪音复验（客户端早断连）", "=" * 96,
     "目标 %s:%s ｜ %s" % (HOST, PORT, time.strftime("%Y-%m-%d %H:%M:%S")), ""]

pw = os.environ.get("CR_SSH_PASS")
if not pw:
    sys.exit("需要 CR_SSH_PASS")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
# 只用口令认证（见 deploy_v2.py 同名注释：默认会先读 ~/.ssh/ 下的私钥，
# 在受限环境里会触发文件访问拦截，而口令登录并不需要它）
ssh.connect(HOST, username=SSH_USER, password=pw, look_for_keys=False,
            allow_agent=False, timeout=30, banner_timeout=30, auth_timeout=30)


def run(cmd):
    _i, o, e = ssh.exec_command(cmd, timeout=60)
    return (o.read().decode("utf-8", "ignore").strip(),
            e.read().decode("utf-8", "ignore").strip())


def counts():
    tb, _ = run("grep -c 'Traceback' %s || true" % LOGFILE)
    ln, _ = run("wc -l < %s || true" % LOGFILE)
    rst, _ = run("grep -c 'ConnectionResetError' %s || true" % LOGFILE)
    return int(tb or 0), int(ln or 0), int(rst or 0)


tb0, ln0, rst0 = counts()
L.append("① 复验前：Traceback %d 行 ｜ ConnectionResetError %d 行 ｜ 日志共 %d 行"
         % (tb0, rst0, ln0))
L.append("   （部署时 tail 看到的那段 if 是**重启前**写入的旧行 —— 本脚本会证明这一点）")

# ② 制造早断连（三种典型形态）
aborts = 0
errors = []
for i in range(8):
    try:
        s = socket.create_connection((HOST, PORT), timeout=8)
        s.close()                                    # 形态 A：连上就断
        aborts += 1
    except Exception as exc:                         # noqa: BLE001
        errors.append("A%d:%s" % (i, exc))
for i in range(6):
    try:
        s = socket.create_connection((HOST, PORT), timeout=8)
        s.sendall(b"GET / HTTP/1.")                  # 形态 B：半个请求行
        s.close()
        aborts += 1
    except Exception as exc:                         # noqa: BLE001
        errors.append("B%d:%s" % (i, exc))
for i in range(4):
    try:
        s = socket.create_connection((HOST, PORT), timeout=8)
        s.sendall(b"GET /login HTTP/1.1\r\nHost: %s\r\n" % HOST.encode())
        s.close()                                    # 形态 C：完整请求行但没读响应
        aborts += 1
    except Exception as exc:                         # noqa: BLE001
        errors.append("C%d:%s" % (i, exc))
L.append("")
L.append("② 已从公网制造早断连 %d 次（连上即断 / 半个请求行 / 不读响应）" % aborts)
if errors:
    L.append("   其中 %d 次客户端侧异常：%s" % (len(errors), errors[:3]))

time.sleep(4)
tb1, ln1, rst1 = counts()
L.append("")
L.append("③ 复验后：Traceback %d 行 ｜ ConnectionResetError %d 行 ｜ 日志共 %d 行"
         % (tb1, rst1, ln1))
L.append("")
L.append("   日志新增 %d 行 ｜ 其中新增 Traceback %d 行 ｜ 新增 ConnectionResetError %d 行"
         % (ln1 - ln0, tb1 - tb0, rst1 - rst0))

ok = (tb1 - tb0) == 0 and (rst1 - rst0) == 0
L.append("")
L.append("结论：%s" % ("PASS —— 服务端已吞掉早断连，日志无新增噪音"
                   if ok else "FAIL —— 有新增噪音，需要排查"))
L.append("")
L.append("参考：修复位置在 app/server.py 的 handle_one_request（捕获 "
         "ConnectionResetError / BrokenPipeError / ConnectionAbortedError / TimeoutError）。")

ssh.close()
with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("remote noise: %s (delta traceback=%d)" % ("PASS" if ok else "FAIL", tb1 - tb0))
sys.exit(0 if ok else 1)
