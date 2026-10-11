# -*- coding: utf-8 -*-
"""图片资产体检：实际像素尺寸 vs 显示尺寸、体积、可省空间。

判据：位图若按「显示尺寸 ×2（Retina）」都还绰绰有余，说明存在无谓的体积。
"""
from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from PIL import Image
except Exception as exc:                                          # noqa: BLE001
    print("需要 pillow：%s" % exc)
    sys.exit(2)

# 显示尺寸来自 CSS / 页面实测（见 css 里的 .brand img / .login-card img）
USAGE = {
    "assets/logo-sm.png": ("顶栏品牌标识（深色主题）", 26, 26),
    "assets/logo-sm-light.png": ("顶栏品牌标识（浅色主题）", 26, 26),
    "assets/favicon.png": ("标签页图标", 32, 32),
}

L = ["图片资产体检（像素尺寸 / 显示尺寸 / 体积）", "=" * 96, ""]
TOT = [0, 0]
for rel, (desc, dw, dh) in USAGE.items():
    p = os.path.join(ROOT, "ui", rel)
    if not os.path.exists(p):
        L.append("%-26s 不存在" % rel)
        continue
    raw = open(p, "rb").read()
    im = Image.open(p)
    w, h = im.size
    TOT[0] += len(raw)
    # 显示尺寸 ×2 的像素预算（Retina 足够）；超出即浪费
    budget = (dw * 2) * (dh * 2)
    ratio = (w * h) / float(budget)
    L.append("%-26s %s" % (rel, desc))
    L.append("   像素 %sx%s ｜ 显示 %sx%s（Retina 需要 %sx%s）｜ 体积 %s ｜ 模式 %s"
             % (w, h, dw, dh, dw * 2, dh * 2, ("%.1f KB" % (len(raw) / 1024.0)),
                im.mode))
    L.append("   像素冗余：%.1f 倍于 Retina 需求%s"
             % (ratio, "  ← 明显偏大" if ratio > 2.5 else ""))
    # 估算优化后体积：缩到 Retina 尺寸再按最优 PNG 存一遍
    try:
        tgt = im.convert("RGBA") if im.mode != "RGBA" else im.copy()
        tgt = tgt.resize((dw * 2, dh * 2), Image.LANCZOS)
        buf = io.BytesIO()
        tgt.save(buf, "PNG", optimize=True)
        best = len(buf.getvalue())
        L.append("   缩到 %sx%s + optimize 后约 %s（可省 %.1f KB / %.0f%%）"
                 % (dw * 2, dh * 2, ("%.1f KB" % (best / 1024.0)),
                    (len(raw) - best) / 1024.0, (1 - best / float(len(raw))) * 100))
        TOT[1] += best
    except Exception as exc:                                      # noqa: BLE001
        L.append("   优化估算失败：%s" % exc)

L.append("")
L.append("合计 %s ｜ 优化后约 %s"
         % ("%.1f KB" % (TOT[0] / 1024.0), "%.1f KB" % (TOT[1] / 1024.0)))
L.append("")
L.append("说明：静态资源已可缓存（带 ETag、走 304 重验证），因此图片体积只影响")
L.append("      「首次访问」的传输量，不再乘每次翻页的次数。")
with io.open(os.path.join(ROOT, "verify", "image_audit.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("image audit done")
