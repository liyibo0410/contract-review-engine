# -*- coding: utf-8 -*-
"""数据重置工具（运维 / 演示前清理）

清空审查数据库、已解析缓存、报告与模拟审批数据，让系统回到可重复的初始状态。
    python tools/reset_data.py
"""
from __future__ import annotations

import glob
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, store as store_mod          # noqa: E402
from mock import feishu                                        # noqa: E402

cfg = cfg_mod.Config(ROOT)
for k in ("data", "uploads", "parsed", "reports", "samples"):
    cfg.path(k)

st = store_mod.Store(cfg.db_path())
lines = []
for t in ("contract_cases", "contract_documents", "review_results",
          "audit_logs", "writeback_logs"):
    st._x("DELETE FROM %s" % t)
    lines.append("清空表 %s" % t)
st._x("DELETE FROM sqlite_sequence WHERE name IN "
      "('contract_cases','contract_documents','review_results','audit_logs','writeback_logs')")
lines.append("重置自增序列")

n = 0
for pattern in ("*.json",):
    for p in glob.glob(os.path.join(cfg.path("parsed"), pattern)):
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
lines.append("清理解析缓存 %d 个文件" % n)

feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)
lines.append("重置模拟审批系统（%d 个待办）" % len(feishu.todo()))

with io.open(os.path.join(ROOT, "verify", "reset.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("reset done")
