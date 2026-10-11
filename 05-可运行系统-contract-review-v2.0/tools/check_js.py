# -*- coding: utf-8 -*-
"""前端脚本语法校验：抽取 HTML 内联脚本 + 外部 js，逐个交给 node --check。

v1.0 的做法（改版时验证 28 个内联脚本）在 v2.0 继续沿用 —— 页面渲染异常
第一件事就是确认有没有语法错误，而不是去猜后端。
"""
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = r"C:\Users\23329\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
UI = os.path.join(ROOT, "ui")
TMP = os.path.join(ROOT, "verify", "_jschk")
os.makedirs(TMP, exist_ok=True)

log = []
bad = 0
total = 0


def check(name, code):
    global bad, total
    total += 1
    p = os.path.join(TMP, re.sub(r"[^\w.-]", "_", name) + ".js")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(code)
    r = subprocess.run([NODE, "--check", p], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        log.append("  ok    %s（%d 字节）" % (name, len(code)))
    else:
        bad += 1
        err = (r.stderr or "").strip().splitlines()
        log.append("  FAIL  %s\n        %s" % (name, "\n        ".join(err[:6])))


# 外部 js
for fn in sorted(os.listdir(os.path.join(UI, "assets"))):
    if fn.endswith(".js"):
        with io.open(os.path.join(UI, "assets", fn), "r", encoding="utf-8") as f:
            check("assets/" + fn, f.read())

# HTML 内联脚本（跳过 src= 的引用与 JSON 数据块）
for fn in sorted(os.listdir(UI)):
    if not fn.endswith(".html"):
        continue
    with io.open(os.path.join(UI, fn), "r", encoding="utf-8") as f:
        html = f.read()
    for i, m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                                      html, re.S | re.I)):
        code = m.group(1)
        if not code.strip():
            continue
        if re.match(r"^\s*\{", code):        # JSON-LD 之类
            continue
        check("%s#inline%d" % (fn, i + 1), code)

head = "前端脚本语法校验：%d 个脚本，失败 %d 个" % (total, bad)
with io.open(os.path.join(ROOT, "verify", "jscheck.txt"), "w", encoding="utf-8") as f:
    f.write(head + "\n" + "=" * 96 + "\n" + "\n".join(log) + "\n")
print(head)
sys.exit(0 if bad == 0 else 1)
