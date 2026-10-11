# -*- coding: utf-8 -*-
"""收尾修复 ②：手机端保留主题切换（只留图标），并把两个前端体检并入验证流水线。"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:56]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:56]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# 主题按钮：窄屏不隐藏，改成只留图标（font-size:0 只影响文字，SVG 尺寸不受影响）
patch("ui/assets/app.js", [
    ("""'<button class="btn sm ghost hide-xs" id="themeBtn" onclick="CR.toggleTheme()"></button>'""",
     """'<button class="btn sm ghost compact-xs" id="themeBtn" onclick="CR.toggleTheme()"></button>'"""),
])

p = os.path.join(ROOT, "ui/assets/app.css")
with io.open(p, "r", encoding="utf-8") as f:
    css = f.read()
if "compact-xs" not in css:
    css = css.replace(
        """@media (max-width: 560px) {
  .hide-xs { display: none !important; }     /* 主题切换（系统暗色可用） */
}""",
        """@media (max-width: 560px) {
  /* 主题切换不隐藏，改成只留图标（font-size:0 只影响文字，SVG 图标自身尺寸不受影响） */
  .compact-xs { padding: 6px 9px; font-size: 0; }
}""")
    if "compact-xs" not in css:
        # 原块可能已被替换过，直接追加
        css += """
@media (max-width: 560px) {
  .compact-xs { padding: 6px 9px; font-size: 0; }
}
"""
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(css)
    log.append("  ok   ui/assets/app.css :: 主题按钮窄屏只留图标")
else:
    log.append("  skip ui/assets/app.css")

# run_all：并入两个前端体检
patch("tools/run_all.py", [
    ('    "assistant": ("tools/check_assistant.py", "AI 助手：意图/数据/引导校验"),',
     '    "assistant": ("tools/check_assistant.py", "AI 助手：意图/数据/引导校验"),\n'
     '    "front": ("tools/audit_frontend.py", "前端逐页体检（脚本异常/空容器/溢出/重叠）"),\n'
     '    "inter": ("tools/audit_interactive.py", "前端交互态体检（弹窗/面板/引导/工作台）"),'),
])

with io.open(os.path.join(ROOT, "verify", "front_fix2.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("front fix2 done")
