# -*- coding: utf-8 -*-
"""列出所有 border-radius 取值的归属（规则选择器），为「圆角收敛」提供依据。"""
from __future__ import annotations

import io
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["ui/assets/app.css", "ui/assets/fx.css"]

L = ["border-radius 取值归属清单", "=" * 100,
     "目的：把「同类元素用了不同圆角」的地方找出来，收敛到设计令牌。", ""]

by_val = defaultdict(list)
for rel in FILES:
    css = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
    # 去掉注释，避免注释里的示例被算进来
    css = re.sub(r"/\*[\s\S]*?\*/", "", css)
    # 逐条规则（选择器 { 声明 }）——含 @media 内的（简单括号配对）
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = m.group(1).strip().replace("\n", " ")
        body = m.group(2)
        for rm in re.finditer(r"border-radius:\s*([^;]+);", body):
            v = " ".join(rm.group(1).split())
            by_val[v].append(sel)

order = sorted(by_val.items(), key=lambda kv: (-len(kv[1]), kv[0]))
for v, sels in order:
    L.append("%-34s %2d 处" % (v, len(sels)))
    for s in sels[:8]:
        L.append("      %s" % (s[:96] if len(s) <= 96 else s[:93] + "..."))
    if len(sels) > 8:
        L.append("      …（另 %d 处）" % (len(sels) - 8))

L.append("")
L.append("令牌现状（app.css :root）：")
css = io.open(os.path.join(ROOT, "ui/assets/app.css"), encoding="utf-8").read()
for mm in re.finditer(r"(--radius[a-z\-]*):\s*([^;]+);", css):
    L.append("   %-14s %s" % (mm.group(1), mm.group(2)))
L.append("   （另有一处在移动端媒体查询里覆写，见 app.css 内 '--radius: 14px'）")

with io.open(os.path.join(ROOT, "verify", "radius_audit.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("radius audit done: %d 种取值" % len(by_val))
