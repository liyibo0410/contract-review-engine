#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""衡鉴 V2.1.7 —— 启动入口

用法：
    python run.py                     # 以 config/app.json 中的地址启动
    python run.py --port 8780         # 覆盖端口
    python run.py --reset             # 清空数据库与模拟审批数据后启动
    python run.py --selftest          # 只跑验收自检，不启动服务

零第三方依赖：仅使用 Python 标准库；PDF 报告走系统浏览器无头打印通道，
扫描件 OCR 走 Windows 内置 OCR 引擎。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def banner(cfg, st, mock_db):
    from app import output
    line = "─" * 62
    print(line)
    print("  衡鉴 V2.1.7   Contract Approval Review System")
    print(line)
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = cfg.get("server", {}).get("port", 8770)
    print("  审查大盘      http://%s:%d/" % (host, port))
    print("  智能审查工作台 http://%s:%d/workbench?id=1" % (host, port))
    print("  动态孪生看板   http://%s:%d/twin" % (host, port))
    print("  合规规则与模板 http://%s:%d/rules" % (host, port))
    print("  模拟审批系统   http://%s:%d/mock/feishu/api/todo" % (host, port))
    print(line)
    print("  数据库        %s" % cfg.db_path())
    print("  审批对接      mode=%s  base=%s" % (
        cfg.get("approval", {}).get("mode"), cfg.get("approval", {}).get("base_url")))
    print("  PDF 报告通道  %s" % (output.find_browser() or "未找到浏览器，将降级为浏览器打印导出"))
    print("  大模型润色    %s" % ("已启用" if (cfg.get("llm") or {}).get("enabled")
                              else "未启用（使用确定性规则模板生成摘要）"))
    print("  已建审查任务  %d 个" % len(st.list_cases()))
    print(line)
    print("  按 Ctrl+C 停止服务")
    print(line)


def main():
    ap = argparse.ArgumentParser(description="衡鉴 V2.1.7")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--reset", action="store_true", help="清空审查数据库与模拟审批数据")
    ap.add_argument("--selftest", action="store_true", help="执行验收自检后退出")
    args = ap.parse_args()

    os.chdir(ROOT)

    from app import config as cfg_mod
    from app import store as store_mod
    from mock import feishu

    cfg = cfg_mod.Config(ROOT)
    if args.port:
        cfg.data["server"]["port"] = args.port
    if args.host:
        cfg.data["server"]["host"] = args.host

    for key in ("data", "uploads", "parsed", "reports", "samples"):
        cfg.path(key)

    st = store_mod.Store(cfg.db_path())
    mock_db = os.path.join(cfg.path("data"), "mock_approval.json")
    feishu.init(ROOT, mock_db, cfg.path("samples"), force_reseed=args.reset)
    if args.reset:
        for t in ("contract_cases", "contract_documents", "review_results",
                  "audit_logs", "writeback_logs"):
            st._x("DELETE FROM %s" % t)
        print("[reset] 已清空数据库与模拟审批数据")

    if args.selftest:
        import selftest
        res = selftest.run(ROOT, echo=True)
        return 0 if res.get("passed") else 1

    banner(cfg, st, mock_db)

    from app import server as server_mod
    httpd = server_mod.serve(cfg, st)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop] 服务已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
