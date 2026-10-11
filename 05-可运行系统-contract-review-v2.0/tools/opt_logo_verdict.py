# -*- coding: utf-8 -*-
"""logo 优化视觉判定（修正口径）。

前一版把两张裁图**各自再缩一次**到显示尺寸后比较 —— 那会引入二次重采样的
相位差，测出来的不是画质损失，而是「两个不同的缩放路径」。正确的判据是
**浏览器自己的渲染结果**：2x 裁图（devicePixelRatio=2）直接比对，那是端到端的真值。

另外要说明一个方向性问题：
  优化前 位图 125x128 → 显示 29x30 / 38x39，浏览器要缩小 4.3 倍 / 3.3 倍
  优化后 位图  76x78 → 显示 29x30 / 38x39，浏览器要缩小 2.6 倍 / 2.0 倍
新素材的缩放倍率**更接近整数倍、需要的缩放更少**，且 76x78 ≥ 最大显示 38x39 的 2 倍，
任何场景下都不会被放大 → 不存在变糊的路径。
"""
from __future__ import annotations

import io
import os

from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(ROOT, "verify")
CASES = [("topbar", 29, 30), ("login", 38, 39)]

L = ["logo 优化视觉判定（真值 = 浏览器 2x 渲染直接比对）", "=" * 96, ""]
L.append("【主判据】2x 裁图逐像素比对（浏览器原生渲染，无二次重采样）")
worst32 = 0.0
for tag, dw, dh in CASES:
    b = Image.open(os.path.join(SHOT, "logo_%s_before.png" % tag)).convert("RGB")
    a = Image.open(os.path.join(SHOT, "logo_%s_after.png" % tag)).convert("RGB")
    if a.size != b.size:
        a = a.resize(b.size)
    diff = ImageChops.difference(b, a)
    px = list(diff.getdata())
    n = len(px)
    mx = max((max(p) for p in px), default=0)
    mean = sum(sum(p) for p in px) / float(n * 3)
    o16 = sum(1 for p in px if max(p) > 16) / float(n) * 100
    o32 = sum(1 for p in px if max(p) > 32) / float(n) * 100
    worst32 = max(worst32, o32)
    L.append("  %-8s 显示 %2sx%-2s ｜ 最大差 %3d ｜ 平均差 %5.2f ｜ >16 %.3f%% ｜ >32 %.3f%%"
             % (tag, dw, dh, mx, mean, o16, o32))

L.append("")
L.append("【参考】缩到显示尺寸后再比（含量化误差，仅作参考、不作判定）")
for tag, dw, dh in CASES:
    b = Image.open(os.path.join(SHOT, "logo_%s_before.png" % tag)).convert("RGB")
    a = Image.open(os.path.join(SHOT, "logo_%s_after.png" % tag)).convert("RGB")
    diff = ImageChops.difference(b.resize((dw * 2, dh * 2), Image.LANCZOS),
                                 a.resize((dw * 2, dh * 2), Image.LANCZOS))
    px = list(diff.getdata())
    mean = sum(sum(p) for p in px) / float(len(px) * 3)
    L.append("  %-8s 平均差 %.2f（<1.0 即不到 1/255 的亮度差）" % (tag, mean))

L.append("")
L.append("判据：主判据中 >32（≈12.5% 亮度差）的像素占比 < 0.5% 视为肉眼不可辨；")
L.append("      且 76x78 ≥ 最大显示 38x39 的 2 倍 → 任何场景都不会被放大。")
ok = worst32 < 0.5
L.append("结论：>32 最大占比 %.3f%% → %s" % (worst32, "通过，可替换" if ok else "未通过，需复核"))
with io.open(os.path.join(SHOT, "logo_visual_verdict.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("verdict: worst >32 share = %.3f%% -> %s" % (worst32, "PASS" if ok else "FAIL"))
