#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v2.0 上线脚本（在 deploy_to_server.py 基础上补齐「上线前备份 + 运行时配置保留」）

与旧脚本的关键差异 —— 这些都是**上线不可逆**场景下的必要动作：

  1. **先备份**：把服务器上的 `/opt/contract-review` 整体打成 tar 包留底，
     systemd unit 单独再留一份。出问题可原样回滚。
  2. **保留服务器上的运行时配置**：`notify`（webhook/secret/keyword 等）与
     `approval`（mode/base_url/write_mode）以**服务器现有值为准**，
     本机配置只负责带过去新增的键（auth / scheduler / queue / review.strip_* / notify.mask）。
     否则一上传就把线上配好的机器人通知打回默认值。
  3. **保留 systemd unit 的 Environment 行**：通知凭据是以 `Environment=` 注入的，
     重写 unit 时只改 Description 的版本号，其余原样保留。
  4. **data/ 全程不动**：数据库、附件、解析缓存、报告都留在服务器上。
     新增的 `app_users` / `app_sessions` 由 store 的 `CREATE TABLE IF NOT EXISTS` 自动建。
  5. **D-31 的收敛值强制生效**：`notify.events` 与 `notify.mask` 用 v2.0 的脱敏口径覆盖，
     不受服务器旧值影响（这是本次上线的安全收口项，不能被旧配置带回明文外发）。

用法：
    set CR_SSH_PASS=...            # Windows
    export CR_SSH_PASS=...         # Linux/macOS
    python deploy/deploy_v2.py
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import sys
import time

try:
    import paramiko
except ImportError:                                            # noqa: BLE001
    sys.exit("缺少 paramiko：pip install paramiko（仅部署脚本需要，被部署的系统本身零依赖）")

HOST = os.environ.get("CR_HOST", "39.96.200.45")
PORT = os.environ.get("CR_PORT", "8160")
REMOTE = os.environ.get("CR_REMOTE", "/opt/contract-review")
SSH_USER = os.environ.get("CR_SSH_USER", "root")
PYTHON = os.environ.get("CR_PYTHON", "/usr/bin/python3.11")
SERVICE = "contract-review"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "verify", "deploy_v2.txt")

SKIP_DIRS = {"data", "verify", "__pycache__", ".workbuddy", ".git", ".idea"}
SKIP_EXT = {".pyc", ".pyo", ".log"}

log = []


def L(s=""):
    log.append(str(s))


def flush():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(log))


def main():
    password = os.environ.get("CR_SSH_PASS")
    if not password:
        import getpass
        password = getpass.getpass("SSH password for %s@%s: " % (SSH_USER, HOST))

    ts = time.strftime("%Y%m%d-%H%M%S")
    L("=" * 100)
    L("衡鉴 V2.1.7 上线（目标 %s:%s，目录 %s）" % (HOST, PORT, REMOTE))
    L("执行时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L("=" * 100)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    last = None
    for i in range(3):
        try:
            # 只用口令认证：paramiko 默认 look_for_keys=True / allow_agent=True，
            # 会在口令之前先去读 ~/.ssh/id_ed25519 —— 在受限环境里这一步会触发
            # 文件访问拦截（本次实测被拦），而且对"口令登录"毫无必要。
            ssh.connect(HOST, username=SSH_USER, password=password,
                        look_for_keys=False, allow_agent=False,
                        timeout=30, banner_timeout=30, auth_timeout=30)
            last = None
            break
        except Exception as exc:                                 # noqa: BLE001
            last = exc
            L("  连接尝试 %d 失败：%s" % (i + 1, exc))
            time.sleep(4)
    if last is not None:
        L("[FATAL] SSH 连接失败，终止（未做任何改动）")
        flush()
        return 2

    sftp = ssh.open_sftp()
    L("已连接 %s" % HOST)

    def run(cmd, title, timeout=300):
        _i, o, e = ssh.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "ignore").strip()
        err = e.read().decode("utf-8", "ignore").strip()
        L("")
        L("$ %s" % title)
        if out:
            L(out[:4000])
        if err:
            L("[stderr] %s" % err[:1200])
        return out, err

    def read_remote(path):
        try:
            with sftp.open(path, "r") as f:
                return f.read().decode("utf-8", "ignore")
        except Exception:                                        # noqa: BLE001
            return ""

    # ------------------------------------------------------------ 1 现状
    L("")
    L("--- 1) 上线前现状 ---")
    run("%s --version" % PYTHON, "Python 版本")
    run("systemctl is-active %s; systemctl is-enabled %s" % (SERVICE, SERVICE), "服务状态")
    run("curl -s --max-time 10 http://127.0.0.1:%s/api/health | head -c 400" % PORT,
        "当前 /api/health（记录旧版本号）")
    run("du -sh %s; du -sh %s/data 2>/dev/null" % (REMOTE, REMOTE), "当前占用")
    run("ls -la %s | head -30" % REMOTE, "当前文件列表")
    unit_old = read_remote("/etc/systemd/system/%s.service" % SERVICE)
    L("")
    L("--- 当前 systemd unit ---")
    L(unit_old or "(读不到)")

    # ------------------------------------------------------------ 2 备份
    L("")
    L("--- 2) 上线前备份（可回滚） ---")
    backup = "/root/contract-review-backup-%s.tar.gz" % ts
    # 只打包本系统的目录（-C 到父目录再指定目录名），不要把整个 /opt 拖进来 ——
    # 服务器上 /opt 还有别的服务，全打包既慢又占空间，回滚时也不好挑。
    run("tar czf %s -C %s %s 2>/dev/null; ls -la %s"
        % (backup, os.path.dirname(REMOTE), os.path.basename(REMOTE), backup),
        "打包 %s" % REMOTE)
    run("cp /etc/systemd/system/%s.service /root/%s.service.bak-%s"
        % (SERVICE, SERVICE, ts), "备份 unit")
    L("  回滚命令（如需要）：")
    L("    systemctl stop %s" % SERVICE)
    L("    rm -rf %s && mkdir -p %s && tar xzf %s -C %s"
      % (REMOTE, REMOTE, backup, os.path.dirname(REMOTE)))
    L("    systemctl restart %s" % SERVICE)

    # ------------------------------------------------------------ 3 合并配置
    L("")
    L("--- 3) 生成上线配置（保留服务器运行时设置 + 强制 v2.0 安全口径） ---")
    local_cfg_path = os.path.join(ROOT, "config", "app.json")
    with io.open(local_cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    remote_cfg = read_remote("%s/config/app.json" % REMOTE)
    keep = {}
    try:
        old = json.loads(remote_cfg) if remote_cfg.strip() else {}
    except Exception:                                            # noqa: BLE001
        old = {}
        L("  [WARN] 服务器 config/app.json 解析失败，按无旧值处理")

    # 这些键以服务器现值为准（上线不该把线上配好的东西打回默认）
    KEEP_NOTIFY = ("enabled", "channel", "webhook", "secret", "webhook_env", "secret_env",
                   "keyword", "at_mobiles", "at_all", "timeout", "min_interval_sec")
    KEEP_APPROVAL = ("mode", "base_url", "app_id", "write_mode", "timeout")
    old_notify = (old.get("notify") or {})
    old_approval = (old.get("approval") or {})
    for k in KEEP_NOTIFY:
        if k in old_notify:
            cfg["notify"][k] = old_notify[k]
    for k in KEEP_APPROVAL:
        if k in old_approval:
            cfg["approval"][k] = old_approval[k]
    keep["notify"] = {k: old_notify.get(k) for k in KEEP_NOTIFY if k in old_notify}
    keep["approval"] = {k: old_approval.get(k) for k in KEEP_APPROVAL if k in old_approval}

    # v2.0 安全口径强制覆盖（D-31）：脱敏与事件收敛不受旧配置影响
    cfg["notify"]["events"] = {"review_completed": False, "high_risk": True,
                               "blocked": True, "writeback_failed": True,
                               "batch_summary": False}
    cfg["notify"]["mask"] = {"party": True, "amount": True, "credit_code": True,
                             "case_no": False, "applicant": True,
                             "contract_title": True, "max_evidence_chars": 0}
    # 调度默认关闭（演示数据可控），队列与鉴权开启
    cfg.setdefault("scheduler", {})["enabled"] = False
    cfg.setdefault("queue", {})["enabled"] = True
    cfg.setdefault("auth", {})["enabled"] = True
    L("  保留的服务器既有设置：")
    L("    notify  → %s" % json.dumps({k: ("***" if k in ("secret",) and v else v)
                                    for k, v in keep.get("notify", {}).items()},
                                   ensure_ascii=False))
    L("    approval→ %s" % json.dumps(keep.get("approval", {}), ensure_ascii=False))
    L("  强制生效的 v2.0 口径：")
    L("    notify.events = %s" % json.dumps(cfg["notify"]["events"], ensure_ascii=False))
    L("    notify.mask   = %s" % json.dumps(cfg["notify"]["mask"], ensure_ascii=False))
    L("    auth.enabled  = %s ｜ scheduler.enabled = %s ｜ queue.enabled = %s"
      % (cfg["auth"]["enabled"], cfg["scheduler"]["enabled"], cfg["queue"]["enabled"]))

    # ------------------------------------------------------------ 4 上传
    L("")
    L("--- 4) 上传代码（跳过 data/ 与 verify/） ---")
    run("mkdir -p %s" % REMOTE, "mkdir -p %s" % REMOTE)
    counter = [0]

    def upload_dir(local, remote):
        try:
            sftp.mkdir(remote)
        except Exception:                                        # noqa: BLE001
            pass
        for name in sorted(os.listdir(local)):
            if name in SKIP_DIRS:
                continue
            lp, rp = os.path.join(local, name), posixpath.join(remote, name)
            if os.path.isdir(lp):
                upload_dir(lp, rp)
            else:
                ext = os.path.splitext(name)[1]
                if ext in SKIP_EXT or name.endswith(".bak") or name.endswith(".bak-20260919"):
                    continue
                sftp.put(lp, rp)
                counter[0] += 1

    upload_dir(ROOT, REMOTE)
    L("  已上传 %d 个文件" % counter[0])

    with sftp.open("%s/config/app.json" % REMOTE, "w") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    L("  已写入 %s/config/app.json（合并后的上线配置）" % REMOTE)

    # ------------------------------------------------------------ 5 unit
    L("")
    L("--- 5) 刷新 systemd unit（只改版本号，Environment 原样保留） ---")
    if unit_old.strip():
        n_env = len([x for x in unit_old.splitlines() if x.strip().startswith("Environment=")])
        unit_new = unit_old.replace("v1.0", "v2.0")
        if "v2.0" not in unit_new:
            unit_new = unit_old
        L("  原 unit 含 %d 行 Environment（通知凭据等），已原样保留" % n_env)
    else:
        L("  [WARN] 读不到原 unit，按标准模板重建（可能丢失 Environment）")
        unit_new = ("[Unit]\nDescription=Contract Review System v2.0\n"
                    "After=network.target\n\n[Service]\nType=simple\n"
                    "WorkingDirectory=%s\nEnvironment=PYTHONIOENCODING=utf-8\n"
                    "ExecStart=%s %s/run.py --host 0.0.0.0 --port %s\n"
                    "Restart=always\nRestartSec=3\nUser=root\n"
                    "StandardOutput=append:/var/log/%s.log\n"
                    "StandardError=append:/var/log/%s.log\n\n"
                    "[Install]\nWantedBy=multi-user.target\n"
                    % (REMOTE, PYTHON, REMOTE, PORT, SERVICE, SERVICE))
    with sftp.open("/tmp/%s.service" % SERVICE, "w") as f:
        f.write(unit_new)
    run("mv /tmp/%s.service /etc/systemd/system/%s.service" % (SERVICE, SERVICE), "写入 unit")
    L("")
    L("--- 落地的 unit ---")
    L(unit_new)

    # ------------------------------------------------------------ 6 重启
    L("")
    L("--- 6) 重启服务 ---")
    run("systemctl daemon-reload", "daemon-reload")
    run("systemctl enable %s" % SERVICE, "开机自启")
    run("systemctl restart %s && sleep 4 && systemctl is-active %s" % (SERVICE, SERVICE),
        "重启并确认状态")
    run("systemctl status %s --no-pager | head -14" % SERVICE, "服务详情")
    run("ss -lntp 2>/dev/null | grep ':%s' | head -3" % PORT, "端口监听")

    # ------------------------------------------------------------ 7 服务器自检
    L("")
    L("--- 7) 服务器侧自检 ---")
    run("curl -s --max-time 15 http://127.0.0.1:%s/api/health | head -c 700" % PORT,
        "GET /api/health（应报 2.0.0）")
    run("cd %s && %s selftest.py 2>&1 | tail -5" % (REMOTE, PYTHON), "服务端验收自检（52 项）")
    run("curl -s --max-time 10 -o /dev/null -w 'login=%%{http_code}\\n' "
        "-X POST -H 'Content-Type: application/json' "
        "-d '{\"username\":\"admin\",\"password\":\"Admin@2026\"}' "
        "http://127.0.0.1:%s/api/auth/login" % PORT, "服务器本地登录接口")
    run("curl -s --max-time 10 -o /dev/null -w 'root_no_cookie=%%{http_code}\\n' "
        "http://127.0.0.1:%s/" % PORT, "未登录访问首页（应 302）")
    run("tail -n 20 /var/log/%s.log" % SERVICE, "最近日志")

    sftp.close()
    ssh.close()
    L("")
    L("=" * 100)
    L("上线完成。访问地址：http://%s:%s/（会先跳到登录页）" % (HOST, PORT))
    L("演示账号：legal/Legal@2026 ｜ applicant/Apply@2026 ｜ admin/Admin@2026")
    L("回滚备份：%s" % backup)
    L("=" * 100)
    flush()
    print("deploy done -> %s" % LOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
