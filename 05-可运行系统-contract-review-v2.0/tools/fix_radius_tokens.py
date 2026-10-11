# -*- coding: utf-8 -*-
"""圆角收敛：把「卡片/面板族」的一次性圆角统一到设计令牌。

判据不是「不同取值的个数」，因为药丸（999px）、圆形（50%）、微元素（2–6px）
本来就该不同 —— 真正的「看着不齐」是同族元素各写一个值：

    .panel .card .stat .toolbar .tk .kpi .dash-head   → var(--radius)   12px
    .risk-card 10px ｜ .model-card 10px ｜ .store-card 10px
    .file-drop 10px ｜ .toast 10px ｜ .req-indicator 10px
    .kv-cell 11px ｜ .mode-col 13px ｜ .modal 14px ｜ .overlay .box 14px
    → 同一族里 5 个不同值（10/11/12/13/14）

本脚本按「选择器 → 目标值」精确改写，并逐条打印改动前后，便于复核。
只改声明值，不动任何其它属性。
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_FILES = ["ui/assets/app.css", "ui/assets/fx.css"]

# 选择器（规范化后）→ 目标值。两个族：
#   族 A 卡片/面板/浮层 → var(--radius)      （12px，移动端 14px）
#   族 B 小底板（图标底/开关/导航项/日志条目）→ var(--radius-s)（ 8px，移动端  9px）
MAP = {
    # ---- 族 A：卡片/面板/浮层
    ".risk-card": "var(--radius)",
    ".model-card": "var(--radius)",
    ".store-card": "var(--radius)",
    ".file-drop": "var(--radius)",
    ".toast": "var(--radius)",
    ".req-indicator": "var(--radius)",
    ".kv-cell": "var(--radius)",
    ".mode-col": "var(--radius)",
    ".modal": "var(--radius)",
    ".overlay .box": "var(--radius)",
    # ---- 族 B：小底板
    ".dash-head .dh-logo": "var(--radius-s)",
    ".kpi .ic": "var(--radius-s)",
    ".panel-head .t .ic": "var(--radius-s)",
    ".sugg": "var(--radius-s)",
    ".notice": "var(--radius-s)",
    ".role-switch": "var(--radius-s)",
    ".log-item": "var(--radius-s)",
    ".sec-item .si-ic": "var(--radius-s)",
    ".store-card .sc-name .si": "var(--radius-s)",
    ".donut-legend .lg": "var(--radius-s)",
    ".brand .mark": "var(--radius-s)",
    ".nav a": "var(--radius-s)",
    # ---- 带强调边的块：只统一圆角部分
    ".quote": "0 var(--radius-s) var(--radius-s) 0",
}


def norm_sel(s):
    return " ".join(s.split())


def blank_comments(css):
    """把注释替换成**等长空白**（保留换行），这样坐标与原文一一对应。

    必须这么做：不剔注释时，紧跟在注释后面的规则，其选择器会带上注释文本
    （如 `/* 提示条 */ .notice`），于是选择器匹配全部失败 —— 第一版就漏了 8 处。
    """
    out = list(css)
    for m in re.finditer(r"/\*[\s\S]*?\*/", css):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def parse(css):
    """返回 [(sel, body, start, end, depth, in_media)]；坐标对 css 原文有效。"""
    out = []
    stack = []
    i = 0
    n = len(css)
    buf = ""
    body_start = None
    while i < n:
        ch = css[i]
        if ch == "{":
            stack.append(buf.strip())
            buf = ""
            body_start = i + 1
        elif ch == "}":
            if stack:
                sel = stack.pop()
                if body_start is not None and body_start <= i:
                    in_media = any(s.startswith("@media") for s in stack)
                    out.append((sel, css[body_start:i], body_start, i, len(stack), in_media))
                body_start = None
            buf = ""
        else:
            buf += ch
        i += 1
    return out


changes = []
for rel in TARGET_FILES:
    fp = os.path.join(ROOT, rel)
    css = io.open(fp, encoding="utf-8").read()
    clean = blank_comments(css)
    assert len(clean) == len(css)
    new_css = css
    # 从后往前改，避免坐标漂移
    for sel, body, bs, be, depth, in_media in sorted(parse(clean), key=lambda r: -r[2]):
        key = norm_sel(sel)
        if key not in MAP:
            continue
        want = MAP[key]
        m = re.search(r"border-radius:\s*([^;]+);", body)
        if not m:
            continue
        cur = " ".join(m.group(1).split())
        if cur == want:
            continue
        newbody = body[:m.start(1)] + want + body[m.end(1):]
        new_css = new_css[:bs] + newbody + new_css[be:]
        line = css[:bs].count("\n") + 1 + body[:m.start()].count("\n")
        changes.append((rel, line, key, cur, want, in_media))
    if changes and "--dry" not in sys.argv:
        with io.open(fp, "w", encoding="utf-8", newline="") as f:
            f.write(new_css)

L = ["圆角收敛改动清单", "=" * 100,
     "文件：%s" % "、".join(TARGET_FILES), ""]
for rel, line, key, cur, want, in_media in sorted(changes, key=lambda c: (c[0], c[1])):
    L.append("%-20s L%-5s %-26s %-28s → %s%s"
             % (os.path.basename(rel), line, key, cur, want,
                "   [媒体查询内]" if in_media else ""))
L.append("")
L.append("共改动 %d 处。" % len(changes))

if changes and "--dry" not in sys.argv:
    L.append("已写入。")
    L.append("")
    L.append("复核（重新解析后的实际取值）：")
    for rel in TARGET_FILES:
        fp = os.path.join(ROOT, rel)
        css2 = io.open(fp, encoding="utf-8").read()
        left = {}
        for sel, body, *_ in parse(blank_comments(css2)):
            k = norm_sel(sel)
            if k in MAP:
                m = re.search(r"border-radius:\s*([^;]+);", body)
                if m:
                    left[k] = " ".join(m.group(1).split())
        bad = [(k, v) for k, v in left.items() if v != MAP[k]]
        L.append("   %-20s 命中 %2d 个选择器 ｜ 与预期不符 %d 个%s"
                 % (os.path.basename(rel), len(left), len(bad),
                    ("：" + "; ".join("%s=%s" % kv for kv in bad)) if bad else ""))
else:
    L.append("（--dry 模式，未写入）")

with io.open(os.path.join(ROOT, "verify", "radius_fix.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("radius fix: %d changes" % len(changes))
