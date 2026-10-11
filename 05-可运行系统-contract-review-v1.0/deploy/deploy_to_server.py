#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本系统部署到 Linux 服务器（paramiko 上传 + systemd 托管）

用法：
    set CR_SSH_PASS=你的root密码        # Windows
    export CR_SSH_PASS=你的root密码     # Linux/macOS
    python deploy/deploy_to_server.py

可选环境变量（均有默认值）：
    CR_HOST      默认 39.96.200.45
    CR_PORT      默认 8160          应用监听端口（需在云安全组放行）
    CR_REMOTE    默认 /opt/contract-review
    CR_SSH_USER  默认 root
    CR_PYTHON    默认 /usr/bin/python3.11

设计原则：
  * 只上传代码与示例数据，**跳过运行时数据 data/ 与验证报告 verify/**，
    不会用本机演示状态覆盖服务器上的数据；
  * 只新增一个 systemd unit 与一个端口，不触碰服务器上既有服务的配置；
  * 幂等：可反复执行，等价于「更新代码 + 重启服务」。

运行期依赖说明（见 README「部署到 Linux 服务器」）：
  * 无需任何 pip 包；需要 Python 3.10+（标注形如 dict | None 的类型注解）
  * 扫描件 OCR：Linux 需 tesseract 与中文语言包；未安装时该能力降级为「解析受阻」
  * 服务端 PDF：需 chromium / chrome；未安装时自动降级为「浏览器打印另存为 PDF」
"""
from __future__ import annotations

import io
import os
import posixpath
import sys
import time

try:
    import paramiko
except ImportError:                                            # noqa: BLE001
    sys.exit("缺少 paramiko：请在用于部署的机器上执行 pip install paramiko（仅部署脚本需要，"
             "被部署的系统本身零依赖）")

HOST = os.environ.get("CR_HOST", "39.96.200.45")
PORT = os.environ.get("CR_PORT", "8160")
REMOTE = os.environ.get("CR_REMOTE", "/opt/contract-review")
SSH_USER = os.environ.get("CR_SSH_USER", "root")
PYTHON = os.environ.get("CR_PYTHON", "/usr/bin/python3.11")
SERVICE = "contract-review"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"data", "verify", "__pycache__", ".workbuddy", ".git", ".idea"}
SKIP_EXT = {".pyc", ".pyo", ".log"}

UNIT = """[Unit]
Description=Contract Review System (合同智能审查系统) v1.0
After=network.target

[Service]
Type=simple
WorkingDirectory={remote}
Environment=PYTHONIOENCODING=utf-8
ExecStart={python} {remote}/run.py --host 0.0.0.0 --port {port}
Restart=always
RestartSec=3
User=root
StandardOutput=append:/var/log/{svc}.log
StandardError=append:/var/log/{svc}.log

[Install]
WantedBy=multi-user.target
"""


def upload_dir(sftp, local, remote):
    n = 0
    try:
        sftp.mkdir(remote)
    except Exception:                                          # noqa: BLE001
        pass
    for name in sorted(os.listdir(local)):
        if name in SKIP_DIRS:
            continue
        lp, rp = os.path.join(local, name), posixpath.join(remote, name)
        if os.path.isdir(lp):
            n += upload_dir(sftp, lp, rp)
        else:
            if os.path.splitext(name)[1] in SKIP_EXT or name.endswith(".bak"):
                continue
            sftp.put(lp, rp)
            n += 1
    return n


def main():
    password = os.environ.get("CR_SSH_PASS")
    if not password:
        import getpass
        password = getpass.getpass("SSH password for %s@%s: " % (SSH_USER, HOST))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=SSH_USER, password=password, timeout=25)
    sftp = ssh.open_sftp()
    print("已连接 %s" % HOST)

    def run(cmd, title):
        _in, out, err = ssh.exec_command(cmd, timeout=300)
        o = out.read().decode("utf-8", "ignore").strip()
        e = err.read().decode("utf-8", "ignore").strip()
        print("\n$ %s" % title)
        if o:
            print(o[:2000])
        if e:
            print("[stderr] %s" % e[:600])
        return o

    print("\n1) 服务器环境")
    run("%s --version" % PYTHON, "%s --version" % PYTHON)
    run("which tesseract || echo '未安装 tesseract（扫描件 OCR 将降级为解析受阻）'", "OCR 引擎检查")
    run("which chromium chromium-browser google-chrome 2>/dev/null || "
        "echo '未安装 chromium（服务端 PDF 将降级为浏览器打印另存）'", "浏览器检查")

    print("\n2) 上传代码 → %s" % REMOTE)
    run("mkdir -p %s" % REMOTE, "mkdir -p %s" % REMOTE)
    print("已上传 %d 个文件" % upload_dir(sftp, ROOT, REMOTE))

    print("\n3) 安装 / 刷新 systemd 服务")
    with sftp.open("/tmp/%s.service" % SERVICE, "w") as f:
        f.write(UNIT.format(remote=REMOTE, port=PORT, svc=SERVICE, python=PYTHON))
    run("mv /tmp/%s.service /etc/systemd/system/%s.service" % (SERVICE, SERVICE), "写入 unit")
    run("systemctl daemon-reload && systemctl enable --now %s && sleep 3" % SERVICE, "启用并启动")
    run("systemctl is-active %s" % SERVICE, "服务状态")

    print("\n4) 自检")
    run("curl -s --max-time 10 http://127.0.0.1:%s/api/health" % PORT, "GET /api/health")
    run("cd %s && %s selftest.py 2>&1 | tail -4" % (REMOTE, PYTHON), "服务端验收自检")

    sftp.close()
    ssh.close()
    print("\n完成。访问地址：http://%s:%s/" % (HOST, PORT))
    print("日志：/var/log/%s.log    服务：systemctl {status,restart} %s" % (SERVICE, SERVICE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
