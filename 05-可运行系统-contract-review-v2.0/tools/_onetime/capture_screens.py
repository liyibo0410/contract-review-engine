# -*- coding: utf-8 -*-
"""测试截图采集 + 渲染结果校验

用系统 Edge 无头模式对**真实运行中**的页面截图，并额外用 --dump-dom
把渲染后的 DOM 取回，校验关键内容确实出现在页面上（避免截到空白页）。

服务启动与截图必须在同一进程内完成（宿主环境会回收跨调用的子进程）。

    python tools/capture_screens.py
产物：docs/screenshots/*.png，verify/screenshots.txt
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, output as output_mod, server as server_mod, store as store_mod  # noqa: E402
from mock import feishu                                                       # noqa: E402

PORT = 8792
OUT = os.path.join(ROOT, "docs", "screenshots")
os.makedirs(OUT, exist_ok=True)

cfg = cfg_mod.Config(ROOT)
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT
for k in ("data", "uploads", "parsed", "reports", "samples"):
    cfg.path(k)

st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=False)

httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.8)

exe = output_mod.find_browser()
if not exe:
    print("no browser")
    sys.exit(1)

profile = os.path.join(tempfile.gettempdir(), "cr_shot_profile")


def pick(prefix):
    for c in st.list_cases():
        d = st.case_document(c["id"])
        if d and d["filename"].startswith(prefix):
            return c
    return None

c_purchase = pick("01_") or (st.list_cases() or [None])[0]
c_sales = pick("03_")
c_blocked = next((c for c in st.list_cases() if c["review_status"] == "blocked"), None)

TARGETS = [
    ("01_审查大盘.png", "/", 1680, 1080,
     ["合同审查大盘", "示例合同集", "高风险", "回写状态"]),
    ("02_智能审查工作台_风险用例.png", "/workbench?id=%d" % c_purchase["id"], 1680, 1500,
     ["合同正文", "风险清单", "PU-H-001", "推荐修改条款", "审批协同与回写"]),
    ("03_权责不对等审查_销售合同.png",
     "/workbench?id=%d" % (c_sales["id"] if c_sales else c_purchase["id"]), 1680, 1500,
     ["合同正文", "SA-H-001", "违约责任"]),
    ("04_合规规则与模板.png", "/rules", 1680, 1200,
     ["合规规则与模板", "审查规则清单", "标准示范条款库", "条款类型词典"]),
]
if c_blocked:
    TARGETS.append(("05_解析受阻与重试.png", "/workbench?id=%d" % c_blocked["id"], 1680, 820,
                    ["解析受阻", "重新审查", "处理建议"]))

log = []


def edge(args, timeout=120):
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
           "--hide-scrollbars", "--force-device-scale-factor=1",
           "--user-data-dir=" + profile, "--virtual-time-budget=6000"] + args
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", "ignore")
    except Exception as exc:                                     # noqa: BLE001
        return "__ERR__%s" % exc


for name, path, w, h, needles in TARGETS:
    out_png = os.path.join(OUT, name)
    if os.path.exists(out_png):
        try:
            os.remove(out_png)
        except OSError:
            pass
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    edge(["--window-size=%d,%d" % (w, h), "--screenshot=" + out_png, url])
    size = os.path.getsize(out_png) if os.path.exists(out_png) else 0

    dom = edge(["--window-size=1680,1200", "--dump-dom", url])
    missing = [n for n in needles if n not in dom]
    ok = size > 10000 and not missing and not dom.startswith("__ERR__")
    log.append("%s %-30s %-22s %4d KB  渲染校验：%s" % (
        "OK " if ok else "ERR", name, path, size // 1024,
        "全部命中（" + "、".join(needles) + "）" if not missing
        else ("缺失 " + "、".join(missing))))

httpd.shutdown()
httpd.server_close()
shutil.rmtree(profile, ignore_errors=True)

with io.open(os.path.join(ROOT, "verify", "screenshots.txt"), "w", encoding="utf-8") as f:
    f.write("测试截图采集与渲染校验 → docs/screenshots/\n")
    f.write("=" * 96 + "\n")
    f.write("\n".join(log))
print("screenshots done")
