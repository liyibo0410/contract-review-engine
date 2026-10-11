# -*- coding: utf-8 -*-
"""验证 v2.0.1 新增的 /api/twin 与 /api/mock/overview 返回真实数据（不起 HTTP，直接调函数）。

关键断言：孪生看板的每一个数字都必须能在库里/文件里对上，
不允许出现「接口返回了值但底层没有对应数据」的造假情况。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, pipeline, store as store_mod   # noqa: E402
from mock import feishu                                           # noqa: E402

tmp = tempfile.mkdtemp(prefix="cr_twin_")
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(tmp, "data"), "uploads": os.path.join(tmp, "uploads"),
    "parsed": os.path.join(tmp, "parsed"), "reports": os.path.join(tmp, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
cfg.data["approval"]["base_url"] = ""
for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)
st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)

pipeline.pull_from_approval(cfg, st, auto_review=True)
cid = [c["id"] for c in st.list_cases() if c["review_status"] == "completed"][0]
st.update_case(cid, writeback_status="success")
# 签名：add_writeback(case_id, mode, channel, status, payload, comment_id="", error="")
st.add_writeback(cid, "live", "mock_feishu", "success",
                 "审计留痕正文（自检写入）", "CMT-TWIN-001")

import time
t0 = time.perf_counter()
ov = pipeline.twin_overview(cfg, st, metrics={"requests": 120, "errors": 1,
                                              "error_rate": 0.0083, "avg_ms": 12.5,
                                              "bytes_out": 20480, "uptime_sec": 3661,
                                              "started_at": "2026-09-19 09:00:00",
                                              "top_paths": [], "slow_requests": []},
                            denied={"unauthorized": 2, "forbidden": 3, "recent": []})
ms = (time.perf_counter() - t0) * 1000

L = []
L.append("孪生看板数据真实性校验（直接调聚合函数，不起 HTTP）")
L.append("=" * 96)
L.append("聚合耗时：%.1f ms" % ms)
L.append("")
L.append("1) 流水线七段（每一段的数字与来源）")
for n in ov["pipeline"]:
    L.append("   %-8s %-10s %6s %-8s  来源 %-28s  %s"
             % (n["key"], n["name"], n["value"], n["unit"], n["src"], n["note"]))
L.append("")
L.append("2) 真实计数（与库内直接统计比对）")
L.append("   聚合值：%s" % json.dumps(ov["counts"], ensure_ascii=False))
cases = st.list_cases()
manual = {
    "total": len(cases),
    "completed": len([c for c in cases if c["review_status"] == "completed"]),
    "blocked": len([c for c in cases if c["review_status"] == "blocked"]),
    "pending": len([c for c in cases if c["review_status"] in ("pending", "parsing", "reviewing")]),
    "writeback_success": len([c for c in cases if c["writeback_status"] == "success"]),
}
L.append("   手工统计：%s" % json.dumps(manual, ensure_ascii=False))
ok_counts = (ov["counts"] == manual)

bar = st.rollup()
L.append("")
L.append("3) rollup 明细：%s" % json.dumps(bar, ensure_ascii=False))
ok_rollup = (
    bar["cases"] == manual["total"]
    and bar["hits"] == sum(1 for c in cases for _ in st.results(c["id"]))
    and bar["adopted"] == sum(1 for c in cases for r in st.results(c["id"]) if r["adopted"])
    and bar["writeback_success"] == manual["writeback_success"]
)
L.append("   与逐行统计一致：%s" % ok_rollup)
L.append("   命中数 %s / 采纳数 %s / 回写成功 %s" % (bar["hits"], bar["adopted"], bar["writeback_success"]))

L.append("")
L.append("4) 引擎与运行态")
L.append("   engines = %s" % json.dumps(ov["engines"], ensure_ascii=False)[:400])
L.append("   queue   = %s" % json.dumps(ov["queue"], ensure_ascii=False)[:260])
L.append("   storage = %s" % json.dumps(ov["storage"], ensure_ascii=False))
L.append("   auth    = %s" % json.dumps(ov["auth"], ensure_ascii=False))
ok_engines = (ov["engines"]["rules"]["total"] >= 30
              and ov["engines"]["clause_types"] >= 10
              and ov["storage"]["reports"] >= 0)

L.append("")
L.append("5) 实时日志来源 = 真实审计表")
act = ov["activity"]
L.append("   返回 %d 条；库里共 %d 条审计" % (len(act), bar["audit"]))
L.append("   最近 3 条：")
for a in act[:3]:
    L.append("     [%s] %s/%s %s" % (a["created_at"], a["step"], a["tool"], (a["detail"] or "")[:60]))
ok_activity = len(act) > 0 and len(act) <= 60

L.append("")
L.append("6) 金额分档（与 cases 逐行求和比对）")
amt = {lv: sum((c["amount"] or 0) for c in cases if c["risk_level"] == lv)
       for lv in ("high", "medium", "low", "none")}
ok_amt = amt == ov["amount_by_risk"]
L.append("   聚合 = %s" % json.dumps(ov["amount_by_risk"], ensure_ascii=False))
L.append("   手工 = %s" % json.dumps(amt, ensure_ascii=False))
L.append("   一致：%s" % ok_amt)

L.append("")
L.append("7) 硬性检查")
NEED_KEYS = ("generated_at", "version", "pipeline", "stats", "rollup", "amount_by_risk",
             "counts", "blocked_detail", "engines", "queue", "scheduler", "notify",
             "storage", "http", "auth", "activity", "errors")
missing = [k for k in NEED_KEYS if k not in ov]
L.append("   载荷必需键齐全：%s%s" % (not missing, "" if not missing else "（缺 %s）" % missing))
L.append("   载荷内不含任何随机/模拟字段：%s"
         % (not any(k in json.dumps(ov, ensure_ascii=False)
                    for k in ("random", "simulated", "演示态模拟值", "fake"))))
L.append("   每个流水线节点都标注了数据来源：%s" % all(n.get("src") for n in ov["pipeline"]))

# 聚合过程零异常 —— 这条是踩坑补上的：
# 曾经 notify 取状态时写错函数名，异常被静默吞掉，
# 结果面板把「脱敏」显示成"关闭"（其实是开启）。现在异常会进 errors 数组。
ok_errors = ov.get("errors") == []
L.append("   聚合过程无异常（errors 为空）：%s%s"
         % (ok_errors, "" if ok_errors else " → %s" % ov.get("errors")))
ok_mask = (ov.get("notify") or {}).get("mask", {}).get("party") is True
L.append("   通知脱敏开关被正确读取为开启：%s（notify.mask=%s）"
         % (ok_mask, json.dumps((ov.get("notify") or {}).get("mask"), ensure_ascii=False)))
ok_notify = (ov.get("notify") or {}).get("enabled") is not None
L.append("   通知状态可读（enabled 不为 null）：%s" % ok_notify)

passed = (ok_counts and ok_rollup and ok_engines and ok_activity and ok_amt
          and not missing and ok_errors and ok_mask and ok_notify
          and all(n.get("src") for n in ov["pipeline"]))
L.append("")
L.append("=" * 96)
L.append("结论：%s" % ("通过 —— 孪生看板数据全部可在库/文件中对上" if passed else "失败"))
L.append("=" * 96)

out = os.path.join(ROOT, "verify", "twin_data.txt")
with io.open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("twin data check: %s" % ("PASS" if passed else "FAIL"))
sys.exit(0 if passed else 1)
