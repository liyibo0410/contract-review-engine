# -*- coding: utf-8 -*-
"""给全部页面挂载 AI 助手（pet.js），并把它并入验证流水线。"""
import io
import os
import re

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []

PAGES = ["index.html", "workbench.html", "rules.html", "twin.html",
         "mock.html", "login.html"]

for fn in PAGES:
    p = os.path.join(ROOT, "ui", fn)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    if 'assets/pet.js' in c:
        log.append("  skip %s（已挂载）" % fn)
        continue
    # 挂在 app.js 之后（pet.js 依赖 CR）；登录页没有 app.js 之外的依赖
    m = re.search(r'<script src="/assets/app\.js"></script>\n', c)
    if not m:
        log.append("  MISS %s：找不到 app.js 引入点" % fn)
        continue
    c = c[:m.end()] + '<script src="/assets/pet.js"></script>\n' + c[m.end():]
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)
    log.append("  ok   %s：已挂载 pet.js" % fn)

# ---------------- run_all 增加 assistant 检查
p = os.path.join(ROOT, "tools", "run_all.py")
with io.open(p, "r", encoding="utf-8") as f:
    c = f.read()
if '"assistant"' not in c:
    c = c.replace(
        '    "twin": ("tools/check_twin_data.py", "孪生看板数据真实性校验"),',
        '    "twin": ("tools/check_twin_data.py", "孪生看板数据真实性校验"),\n'
        '    "assistant": ("tools/check_assistant.py", "AI 助手：意图/数据/引导校验"),')
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)
    log.append("  ok   tools/run_all.py：并入 assistant 检查")
else:
    log.append("  skip tools/run_all.py")

with io.open(os.path.join(ROOT, "verify", "pet_mount.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("pet mount done")
