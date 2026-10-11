# -*- coding: utf-8 -*-
"""生成 logo 优化候选并量化视觉差异（不直接覆盖原文件）。

原图 125x128 / 23.3 KB，实际显示最大只有 38x39（登录页），顶栏只有 26x27
—— 即 5.9 倍于 Retina 需求的像素冗余。这里生成几个候选，按
「体积」+「与原图的像素差异」两个维度给出取舍依据。
"""
from __future__ import annotations

import io
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "verify", "logo_candidates")

try:
    from PIL import Image, ImageChops
except Exception as exc:                                          # noqa: BLE001
    print("需要 pillow：%s" % exc)
    sys.exit(2)

# 实际最大显示尺寸（登录页 38x39）→ Retina 需要 76x78
TARGETS = [(76, 78), (78, 80), (52, 52)]

if os.path.isdir(OUT):
    shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

L = ["logo 优化候选（体积 vs 视觉差异）", "=" * 96,
     "实际最大显示：登录页 38x39（Retina 需 76x78）｜ 顶栏 26x27（Retina 需 52x54）", ""]

for name in ("logo-sm.png", "logo-sm-light.png"):
    src = os.path.join(ROOT, "ui", "assets", name)
    raw = open(src, "rb").read()
    im = Image.open(src).convert("RGBA")
    L.append("%s  原图 %sx%s / %.1f KB" % (name, im.width, im.height, len(raw) / 1024.0))
    L.append("   %-14s %-10s %-12s %s" % ("候选", "体积", "省", "与「原图缩到同尺寸」的最大像素差"))
    # 基准：把原图缩到 26x26 显示 —— 用于判断缩小后是否有可见损失
    for tw, th in TARGETS:
        cand = im.resize((tw, th), Image.LANCZOS)
        buf = io.BytesIO()
        cand.save(buf, "PNG", optimize=True)
        best = len(buf.getvalue())
        p = os.path.join(OUT, "%s.%dx%d.png" % (name[:-4], tw, th))
        with open(p, "wb") as f:
            f.write(buf.getvalue())
        # 视觉差异：两个版本都缩到 26x26（顶栏实际显示尺寸）比对
        a = im.resize((26, 26), Image.LANCZOS)
        b = cand.resize((26, 26), Image.LANCZOS)
        diff = ImageChops.difference(a, b)
        mx = max((ch for px in diff.getdata() for ch in px), default=0)
        L.append("   %-14s %-10s %-12s %d / 255%s"
                 % ("%dx%d" % (tw, th), "%.1f KB" % (best / 1024.0),
                    "%.0f%%" % ((1 - best / float(len(raw))) * 100),
                    mx, "  ← 肉眼无差" if mx <= 6 else ""))
    L.append("   候选文件已存 verify/logo_candidates/")

L.append("")
L.append("结论：76x78 已覆盖最大显示场景（38x39 的 2 倍），且与 78x80 体积接近。")
with io.open(os.path.join(ROOT, "verify", "logo_opt.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("logo candidates done")
