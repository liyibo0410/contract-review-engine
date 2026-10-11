# -*- coding: utf-8 -*-
"""一键跑齐全部验证并把输出落成 UTF-8 文本（宿主的 PowerShell 重定向会写成 UTF-16，
   且命令 stdout 不回传，因此统一由本脚本用 subprocess 捕获后自行落盘）。

用法：
    python tools/run_all.py             # 跑全部
    python tools/run_all.py http bench  # 只跑指定项（selftest|http|bench|devcheck）
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OUT = os.path.join(ROOT, "verify")
os.makedirs(OUT, exist_ok=True)

TASKS = {
    "consistency": ("tools/check_consistency.py", "改动一致性核查"),
    "js": ("tools/check_js.py", "前端脚本语法校验"),
    "noise": ("tools/check_conn_noise.py", "客户端早断连的日志噪音检查"),
    "twin": ("tools/check_twin_data.py", "孪生看板数据真实性校验"),
    "assistant": ("tools/check_assistant.py", "AI 助手：意图/数据/引导校验"),
    "front": ("tools/audit_frontend.py", "前端逐页体检（脚本异常/空容器/溢出/重叠）"),
    "deep": ("tools/audit_frontend_deep.py", "前端深检（对比度/触控目标/可访问性/越界/小字）"),
    "inter": ("tools/audit_interactive.py", "前端交互态体检（弹窗/面板/引导/工作台）"),
    "cfx": ("tools/probe_cfx.py", "卡片动效专项（入场/常驻/不重播/降级）"),
    "cfxcover": ("tools/probe_cfx_coverage.py", "卡片动效覆盖率（逐页核对全部卡片）"),
    "perf": ("tools/probe_frontend_perf.py", "前端优化体检（体积/缓存/时间线/圆角一致性）"),
    "wb": ("tools/probe_workbench.py", "工作台专项（滚动可见性/点风险跳转/顶栏尺寸）"),
    "selftest": ("tools/run_selftest.py", "验收自检"),
    "http": ("tools/verify_http.py", "HTTP 全链路冒烟"),
    "render": ("tools/verify_render.py", "页面渲染与截图校验"),
    "remote": ("deploy/verify_remote_v2.py", "线上实例巡检（公网）"),
    "bench": ("tools/bench.py", "性能基准"),
    "devcheck": ("tools/devcheck.py", "开发期编译/导入/关键路径自检"),
}

want = [a for a in sys.argv[1:] if a in TASKS] or list(TASKS)
summary = []
for key in want:
    script, label = TASKS[key]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        p = subprocess.run([PY, "-u", os.path.join(ROOT, script)],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=900)
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    log_path = os.path.join(OUT, "run_%s.txt" % key)
    with io.open(log_path, "w", encoding="utf-8") as f:
        f.write("$ python %s\n" % script)
        f.write("exit=%s\n" % rc)
        f.write("-" * 88 + "\n")
        f.write(out)
        if err.strip():
            f.write("\n[stderr]\n" + err)
    last = [x for x in out.strip().splitlines() if x.strip()]
    summary.append("%-9s exit=%-3s %s ｜ %s" % (
        key, rc, label, (last[-1][:96] if last else "(无输出)")))

with io.open(os.path.join(OUT, "run_all.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(summary) + "\n")
print("\n".join(summary))
