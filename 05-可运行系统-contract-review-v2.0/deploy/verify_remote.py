#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""线上实例巡检：验证已部署实例的健康状态与前端页面依赖的接口契约

用法：
    python deploy/verify_remote.py                       # 默认检查 39.96.200.45:8160
    python deploy/verify_remote.py http://1.2.3.4:8160   # 指定地址

检查内容（全部只用标准库）：
    * 健康检查与版本、审批对接地址是否指向自身实际端口
    * 3 个页面与 2 个静态资源是否可访问、是否含关键标记
    * 页面数据来源接口（统计 / 列表 / 详情 / 规则库 / 审批待办）是否返回有效数据
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

_BASE_ARG, _LOG_ARG, _i = None, None, 0
_RAW = sys.argv[1:]
while _i < len(_RAW):
    if _RAW[_i] == "--log" and _i + 1 < len(_RAW):
        _LOG_ARG = _RAW[_i + 1]
        _i += 2
        continue
    if not _RAW[_i].startswith("--") and _BASE_ARG is None:
        _BASE_ARG = _RAW[_i]
    _i += 1

BASE = (_BASE_ARG or "http://39.96.200.45:8160").rstrip("/")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

rows = []


def get(path, timeout=30):
    req = urllib.request.Request(BASE + path, headers={"Accept": "*/*"})
    with OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read()


def rec(name, ok, detail):
    rows.append((ok, name, detail))


def main():
    # ---------- 健康检查 ----------
    try:
        status, body = get("/api/health")
        h = json.loads(body)["data"]
        base_ok = str(h.get("approval_base", "")).endswith(":%s/mock/feishu/api" %
                                                          BASE.rsplit(":", 1)[-1])
        rec("/api/health", status == 200 and base_ok,
            "v%s ｜ Python 侧自调用地址 %s ｜ PDF 通道 %s ｜ 规则类型 %s"
            % (h["version"], h["approval_base"],
               "可用" if h["browser_for_pdf"] else "降级（浏览器打印）",
               ",".join(h["rules_types"])))
    except Exception as exc:                                    # noqa: BLE001
        rec("/api/health", False, "无法访问：%r" % exc)
        print_report()
        return 1

    # ---------- 页面与静态资源 ----------
    pages = [
        ("/", "合同审查大盘"),
        ("/workbench?id=1", "workbench"),
        ("/rules", "rules"),
        ("/assets/app.css", "--accent"),
        ("/assets/app.js", "global.CR"),
    ]
    for path, marker in pages:
        try:
            status, body = get(path)
            txt = body.decode("utf-8", "ignore")
            rec("页面 %s" % path, status == 200 and marker in txt,
                "HTTP %s ｜ %d 字节 ｜ 关键标记「%s」%s"
                % (status, len(body), marker, "命中" if marker in txt else "缺失"))
        except Exception as exc:                                # noqa: BLE001
            rec("页面 %s" % path, False, "无法访问：%r" % exc)

    # ---------- 页面数据来源 ----------
    first_id = None
    try:
        stats = json.loads(get("/api/stats")[1])["data"]
        rec("GET /api/stats", stats["total"] > 0,
            "任务 %d 个 ｜ 高风险 %d ｜ 受阻 %d ｜ 高风险金额 %.0f"
            % (stats["total"], stats["by_risk"].get("high", 0),
               stats["blocked"], stats["high_risk_amount"]))
    except Exception as exc:                                    # noqa: BLE001
        rec("GET /api/stats", False, "%r" % exc)

    try:
        items = json.loads(get("/api/cases")[1])["data"]["items"]
        # 详情检查要挑一个「审查完成」的案件：列表按 id 倒序，第一条可能是受阻任务
        done = [c for c in items if c["review_status"] == "completed"]
        first_id = (done or items)[0]["id"] if items else None
        titles = "、".join(c["title"][:14] for c in items[:2])
        rec("GET /api/cases", bool(items),
            "%d 个任务（已完成 %d）｜ 例：%s ｜ 风险分布 %s" % (
                len(items), len(done), titles,
                json.dumps({k: len([c for c in items if c["risk_level"] == k])
                            for k in {c["risk_level"] for c in items}},
                           ensure_ascii=False)))
    except Exception as exc:                                    # noqa: BLE001
        rec("GET /api/cases", False, "%r" % exc)

    if first_id:
        try:
            det = json.loads(get("/api/cases/%d" % first_id)[1])["data"]
            anchored = [r for r in det["results"] if r["page_no"] is not None]
            rec("GET /api/cases/{id}", len(det["paragraphs"]) > 0,
                "#%s《%s》｜ 风险 %d 项（可定位 %d）｜ 段落 %d ｜ 条款 %d ｜ 覆盖度 %.0f%%"
                % (first_id, det["case"]["title"][:16], len(det["results"]),
                   len(anchored), len(det["paragraphs"]), len(det["clauses"]),
                   det["coverage"]["coverage_rate"] * 100))
        except Exception as exc:                                # noqa: BLE001
            rec("GET /api/cases/{id}", False, "%r" % exc)

    try:
        rules = json.loads(get("/api/rules?type=purchase")[1])["data"]
        rec("GET /api/rules", rules["stats"]["count"] > 0,
            "采购规则 %d 条 ｜ 匹配方式 %s"
            % (rules["stats"]["count"], "/".join(rules["stats"]["modes"])))
    except Exception as exc:                                    # noqa: BLE001
        rec("GET /api/rules", False, "%r" % exc)

    try:
        todo = json.loads(get("/mock/feishu/api/todo")[1])["data"]["items"]
        rec("GET /mock/feishu/api/todo", True, "模拟审批待办 %d 个" % len(todo))
    except Exception as exc:                                    # noqa: BLE001
        rec("GET /mock/feishu/api/todo", False, "%r" % exc)

    return print_report()


def print_report():
    ok = [r for r in rows if r[0]]
    out = ["=" * 78,
           "  线上实例巡检：%s" % BASE,
           "=" * 78]
    for good, name, detail in rows:
        out.append("%s %-26s %s" % ("[通过]" if good else "[未通过]", name, detail))
    out.append("-" * 78)
    out.append("  结果：%d / %d 项通过" % (len(ok), len(rows)))
    out.append("=" * 78)
    text = "\n".join(out)
    print(text)
    # 支持 --log <path> 落盘（终端编码环境不一致时便于留证）
    if _LOG_ARG:
        import io
        with io.open(_LOG_ARG, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
