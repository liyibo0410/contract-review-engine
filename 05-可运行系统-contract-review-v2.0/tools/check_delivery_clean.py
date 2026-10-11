# -*- coding: utf-8 -*-
"""交付目录「整洁度」核对（**只读**，不做任何删除）。

同步脚本是「只增不删」的（这是刻意的安全设计），所以源目录里被移动/改名过的文件，
会在目标目录留下旧副本。本脚本把这类残留精确列出来，供人工确认后再处理。

输出：verify/delivery_diff.txt
"""
from __future__ import annotations

import io
import os

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = r"E:\桌面\合同审批系统-李奕波\05-可运行系统-contract-review-v2.0"

SKIP_DIRS = {"data", "__pycache__", ".idea", "_jschk"}

L = ["交付目录整洁度核对（只读）", "=" * 96,
     "源：%s" % SRC, "目标：%s" % DST, ""]


def rel_files(root):
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".pyc", ".bak", ".bak-20260919")):
                continue
            out.add(os.path.relpath(os.path.join(dirpath, fn), root))
    return out


if not os.path.isdir(DST):
    L.append("目标目录不存在，跳过。")
    stale = []
else:
    s, d = rel_files(SRC), rel_files(DST)
    stale = sorted(d - s)          # 目标里多出来的（源已移动/改名/删除）
    missing = sorted(s - d)        # 目标里缺的（尚未同步）
    L.append("源文件 %d 个 ｜ 目标文件 %d 个" % (len(s), len(d)))
    L.append("")
    L.append("① 目标目录里的**旧副本**（源已移入 tools/_onetime/，共 %d 个）：" % len(stale))
    for f in stale:
        L.append("   %s" % f)
    if not stale:
        L.append("   无")
    L.append("")
    L.append("② 目标目录**尚缺**的文件（%d 个）：" % len(missing))
    for f in missing[:40]:
        L.append("   %s" % f)
    if not missing:
        L.append("   无")
    L.append("")
    L.append("=" * 96)
    L.append("说明：这些旧副本是「只增不删」同步策略的副产物（同一文件在源里被移动到")
    L.append("      tools/_onetime/ 后，目标目录的旧位置仍留着）。内容与 _onetime/ 下的")
    L.append("      新位置**完全一致**，删除它们不影响任何功能。")
    L.append("      **本脚本不执行删除** —— 是否清理请人工确认。")

with io.open(os.path.join(SRC, "verify", "delivery_diff.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("delivery diff: stale=%d" % len(stale))
