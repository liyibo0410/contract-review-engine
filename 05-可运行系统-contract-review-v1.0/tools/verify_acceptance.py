# -*- coding: utf-8 -*-
"""以文件形式落盘自检结果（便于在无 stdout 回传的环境下查看）。"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import selftest                                                    # noqa: E402

res = selftest.run(ROOT, echo=False)
out = os.path.join(ROOT, "verify")
os.makedirs(out, exist_ok=True)
with io.open(os.path.join(out, "selftest.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)

lines = []
lines.append("结果：%d / %d 项通过，耗时 %d ms" % (
    res["passed_count"], res["total"], res["elapsed_ms"]))
lines.append("")
for i, c in enumerate(res["checks"], 1):
    lines.append("%s %2d. %s" % ("[通过]" if c["passed"] else "[未通过]", i, c["name"]))
    if c["detail"]:
        lines.append("      %s" % str(c["detail"]).replace("\n", "\n      "))
with io.open(os.path.join(out, "selftest.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("selftest done: %s" % res["passed"])
