# -*- coding: utf-8 -*-
"""确认「客户端断连噪音」是否在修复后仍出现（只读）。

方法：取服务最近一次启动时间，再看日志里最后一次 ConnectionResetError 出现在它之前还是之后。
"""
from __future__ import annotations

import io
import os

import paramiko

HOST = "39.96.200.45"
USER = "root"
PWD = os.environ.get("CR_SSH_PASS", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "verify", "remote_log_check.txt")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PWD, timeout=30,
            banner_timeout=30, auth_timeout=30)

CMDS = [
    ("服务启动时刻", "systemctl show -p ActiveEnterTimestamp --value contract-review"),
    ("日志总量", "wc -l /var/log/contract-review.log"),
    ("ConnectionReset 出现次数", "grep -c 'ConnectionResetError' /var/log/contract-review.log || true"),
    ("最后一次出现的行号", "grep -n 'ConnectionResetError' /var/log/contract-review.log | tail -3"),
    ("日志最后 12 行", "tail -n 12 /var/log/contract-review.log"),
    ("日志里有时间戳的行（最后 5 条）",
     "grep -n 'Started Contract Review\\|服务已停止\\|boot' /var/log/contract-review.log | tail -5"),
    ("确认修复代码已上线",
     "grep -n 'handle_one_request' /opt/contract-review/app/server.py | head -5"),
]

out = ["线上日志核查（只读） ｜ %s" % HOST, "=" * 96]
for title, cmd in CMDS:
    _i, o, e = ssh.exec_command(cmd, timeout=120)
    so = o.read().decode("utf-8", "ignore").strip()
    se = e.read().decode("utf-8", "ignore").strip()
    out.append("")
    out.append("$ %s" % title)
    if so:
        out.append(so[:3000])
    if se:
        out.append("[stderr] %s" % se[:300])
ssh.close()

with io.open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("log check done")
