# -*- coding: utf-8 -*-
"""自检执行器：跑 selftest 并把结果写成 UTF-8 文本 + JSON（结果落文件，不依赖 stdout）。"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import selftest  # noqa: E402

res = selftest.run(ROOT, echo=False)

import app as _app                                                       # noqa: E402
lines = ["=" * 92,
         "  衡鉴 V2.1.7 · 验收自检（selftest）｜运行版本 %s" % _app.__version__,
         "=" * 92]
for i, c in enumerate(res["checks"], 1):
    lines.append("  %s %2d. %s" % ("[通过]" if c["passed"] else "[未通过]", i, c["name"]))
    if c["detail"]:
        lines.append("        %s" % c["detail"])
lines.append("-" * 92)
lines.append("  结果：%d / %d 项通过，耗时 %d ms" % (
    res["passed_count"], res["total"], res["elapsed_ms"]))
lines.append("=" * 92)

out_dir = os.path.join(ROOT, "verify")
os.makedirs(out_dir, exist_ok=True)
with io.open(os.path.join(out_dir, "selftest.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
with io.open(os.path.join(out_dir, "selftest.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)

print("selftest %d/%d" % (res["passed_count"], res["total"]))
sys.exit(0 if res.get("passed") else 1)
