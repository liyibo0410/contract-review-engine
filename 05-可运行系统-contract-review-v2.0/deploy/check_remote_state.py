# -*- coding: utf-8 -*-
"""上线后状态核查（只读命令，不做任何改动）。

汇报三件事：磁盘余量、备份文件清单（含体积）、服务与端口现状。
"""
from __future__ import annotations

import io
import os
import sys
import time

import paramiko

HOST = os.environ.get("CR_HOST", "39.96.200.45")
USER = os.environ.get("CR_SSH_USER", "root")
PWD = os.environ.get("CR_SSH_PASS", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "verify", "remote_state.txt")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PWD, timeout=30,
            banner_timeout=30, auth_timeout=30)

CMDS = [
    ("磁盘余量", "df -h / | tail -2"),
    ("备份文件", "ls -lh /root/contract-review-backup-*.tar.gz /root/contract-review.service.bak-* 2>/dev/null"),
    ("备份总占用", "du -ch /root/contract-review-backup-*.tar.gz 2>/dev/null | tail -1"),
    ("应用目录占用", "du -sh /opt/contract-review /opt/contract-review/data 2>/dev/null"),
    ("服务状态", "systemctl is-active contract-review; systemctl is-enabled contract-review"),
    ("端口监听", "ss -lntp 2>/dev/null | grep ':8160'"),
    ("运行版本", "curl -s --max-time 10 http://127.0.0.1:8160/api/health "
              "| python3 -c \"import sys,json;d=json.load(sys.stdin)['data'];"
              "print('version=',d['version'],'accounts=',d['auth']['accounts'],"
              "'pdf=',d['pdf_channels']['effective'],'notify=',d['notify']['configured'])\""),
    ("日志尾部（应无 ConnectionReset 噪音）",
     "tail -n 8 /var/log/contract-review.log; echo '--- 噪音计数 ---'; "
     "grep -c 'ConnectionResetError' /var/log/contract-review.log 2>/dev/null || echo 0"),
    ("数据保留情况", "ls -la /opt/contract-review/data | head -8; "
                 "echo '--- parsed ---'; ls /opt/contract-review/data/parsed | wc -l; "
                 "echo '--- reports ---'; ls /opt/contract-review/data/reports | wc -l"),
]

log = ["线上实例核查（只读） ｜ %s ｜ %s" % (HOST, time.strftime("%Y-%m-%d %H:%M:%S")),
       "=" * 96]
for title, cmd in CMDS:
    _i, o, e = ssh.exec_command(cmd, timeout=120)
    out = o.read().decode("utf-8", "ignore").strip()
    err = e.read().decode("utf-8", "ignore").strip()
    log.append("")
    log.append("$ %s" % title)
    if out:
        log.append(out)
    if err:
        log.append("[stderr] %s" % err[:400])
ssh.close()

with io.open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("state check done")
