# -*- coding: utf-8 -*-
"""统计交付物规模（供文档引用，避免手写数字失真）"""
from __future__ import annotations

import io
import json
import os

CR = r"D:\workbuddy work\2026-09-18-20-32-30\contract-review-1.0"
TEXT_EXT = (".py", ".html", ".css", ".js", ".md", ".json")


def acc(path, exts=None, recursive=True, skip_dirs=("reference", "screenshots")):
    f = l = b = 0
    if not os.path.exists(path):
        return 0, 0, 0
    if os.path.isfile(path):
        if exts and not path.endswith(exts):
            return 0, 0, 0
        n = 1
        size = os.path.getsize(path)
        lines = 0
        if path.endswith(TEXT_EXT):
            with io.open(path, encoding="utf-8") as fh:
                lines = len(fh.readlines())
        return n, lines, size
    if recursive:
        walker = []
        for dp, dns, fns in os.walk(path):
            dns[:] = [d for d in dns if d not in skip_dirs]
            for fn in fns:
                walker.append(os.path.join(dp, fn))
    else:
        walker = [os.path.join(path, fn) for fn in os.listdir(path)
                  if os.path.isfile(os.path.join(path, fn))]
    for p in walker:
        if exts and not p.endswith(exts):
            continue
        f += 1
        b += os.path.getsize(p)
        if p.endswith(TEXT_EXT):
            try:
                with io.open(p, encoding="utf-8") as fh:
                    l += len(fh.readlines())
            except Exception:                                    # noqa: BLE001
                pass
    return f, l, b


GROUPS = [
    ("后端审查服务 app/", os.path.join(CR, "app"), (".py",), True),
    ("模拟审批系统 mock/", os.path.join(CR, "mock"), (".py",), True),
    ("前端界面 ui/", os.path.join(CR, "ui"), (".html", ".css", ".js"), True),
    ("规则与配置 config/", os.path.join(CR, "config"), (".json",), True),
    ("示例合同集 samples/", os.path.join(CR, "samples"), None, False),
    ("交付文档（根目录）", CR, (".md",), False),
    ("交付文档 docs/", os.path.join(CR, "docs"), (".md",), True),
    ("构建与验证工具 tools/", os.path.join(CR, "tools"), (".py",), True),
    ("部署脚本 deploy/", os.path.join(CR, "deploy"), (".py",), True),
    ("验收自检 selftest.py", os.path.join(CR, "selftest.py"), (".py",), False),
    ("启动入口 run.py", os.path.join(CR, "run.py"), (".py",), False),
]

rows = []
tf = tl = tb = 0
for label, path, exts, rec in GROUPS:
    f, l, b = acc(path, exts, rec)
    if label.startswith("交付文档（根目录）"):
        root_md = [fn for fn in os.listdir(CR) if fn.endswith(".md")]
        f = len(root_md)
        l = sum(len(io.open(os.path.join(CR, fn), encoding="utf-8").readlines())
                for fn in root_md)
        b = sum(os.path.getsize(os.path.join(CR, fn)) for fn in root_md)
    rows.append((label, f, l, b))
    tf += f
    tl += l
    tb += b

out = ["| 模块 | 文件数 | 行数 | 体积 |", "| --- | --- | --- | --- |"]
for label, f, l, b in rows:
    out.append("| %s | %d | %d | %.1f KB |" % (label, f, l, b / 1024.0))
out.append("| **合计** | **%d** | **%d** | **%.1f KB** |" % (tf, tl, tb / 1024.0))

out.append("")
out.append("| 合同类型 | 文件 | 规则数 | 高风险 | 中风险 | 低风险 |")
out.append("| --- | --- | --- | --- | --- | --- |")
tot = 0
for fn in sorted(os.listdir(os.path.join(CR, "config", "rules"))):
    if not fn.endswith(".json") or fn.endswith(".bak"):
        continue
    d = json.load(io.open(os.path.join(CR, "config", "rules", fn), encoding="utf-8"))
    rs = d["rules"]
    tot += len(rs)
    out.append("| %s | %s | %d | %d | %d | %d |" % (
        d.get("type_label"), fn, len(rs),
        len([r for r in rs if r["risk_level"] == "high"]),
        len([r for r in rs if r["risk_level"] == "medium"]),
        len([r for r in rs if r["risk_level"] == "low"])))
out.append("| **合计** |  | **%d** |  |  |  |" % tot)

samples = [f for f in os.listdir(os.path.join(CR, "samples")) if not f.startswith("_")]
shots = [f for f in os.listdir(os.path.join(CR, "docs", "screenshots"))
         if f.endswith(".png")]
refs = [f for f in os.listdir(os.path.join(CR, "docs", "reference"))]
out.append("")
out.append("示例合同集：%d 个　测试截图：%d 张　参考资产：%d 份" % (
    len(samples), len(shots), len(refs)))

with io.open(os.path.join(CR, "verify", "stats.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("stats done")
