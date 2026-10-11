#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""针对「引擎负载与资源占用」面板的专项探针：
把面板渲染出来的**文字与几何**原样打印出来，用事实判断哪里不对。

同时把服务端 /api/twin 里这个面板用到的字段单独打印，便于比对
「面板显示的」与「接口给的」是否一致。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import config as cfg_mod, pipeline, server as server_mod, store as store_mod  # noqa: E402
from mock import feishu                                                               # noqa: E402
from tools.cdp import Browser, DEFAULT_CHROME                                         # noqa: E402

PORT = 8802
TMP = tempfile.mkdtemp(prefix="cr_eng_")
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(TMP, "data"), "uploads": os.path.join(TMP, "uploads"),
    "parsed": os.path.join(TMP, "parsed"), "reports": os.path.join(TMP, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT
for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)
st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)
httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.7)
pipeline.pull_from_approval(cfg, st, auto_review=True)
BASE = "http://127.0.0.1:%d" % PORT

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_req = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with _op.open(_req, timeout=30) as r:
    TOKEN = (r.headers.get("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]

L = ["「引擎负载与资源占用」面板专项探针", "=" * 100, ""]

# ---------------- 接口侧
_tw = json.loads(_op.open(BASE + "/api/twin").read().decode()) if False else None
rq = urllib.request.Request(BASE + "/api/twin", headers={"Cookie": "cr_session=" + TOKEN})
tw = json.loads(_op.open(rq, timeout=30).read().decode())["data"]
L.append("① 接口侧字段（/api/twin）")
L.append("   queue     = %s" % json.dumps(tw.get("queue"), ensure_ascii=False))
L.append("   storage   = %s" % json.dumps(tw.get("storage"), ensure_ascii=False))
L.append("   engines   = %s" % json.dumps(tw.get("engines"), ensure_ascii=False)[:420])
L.append("   rollup    = %s" % json.dumps(tw.get("rollup"), ensure_ascii=False))
L.append("   http      = %s" % json.dumps(
    {k: v for k, v in (tw.get("http") or {}).items() if k != "top_paths"}, ensure_ascii=False))
L.append("   notify    = %s" % json.dumps(
    {k: v for k, v in (tw.get("notify") or {}).items() if k != "recent"}, ensure_ascii=False))
L.append("   counts    = %s" % json.dumps(tw.get("counts"), ensure_ascii=False))
L.append("   scheduler = %s" % json.dumps(tw.get("scheduler"), ensure_ascii=False)[:200])
L.append("")

# 面板用到的 4 个仪表的「值 / 上限 / 占比」
q, s, e = tw["queue"], tw["storage"], tw["engines"]
rows = [
    ("队列水位", q.get("queued"), q.get("maxsize"), "个"),
    ("待处理堆积", q.get("pending_cases"), q.get("max_pending"), "个"),
    ("附件存储", s.get("uploads_mb"), q.get("max_total_mb"), "MB"),
    ("OCR 平均置信度", round((e["ocr"].get("avg_confidence") or 0) * 100), 100, "%"),
]
L.append("② 四个仪表的实际数值与占比")
for name, v, m, u in rows:
    pct = (v / m * 100) if m else 0
    bar = int(min(100, pct) / 4)
    L.append("   %-14s %8s %-3s / %-8s %-3s  → 占比 %6.2f%%  进度条 %s"
             % (name, v, u, m, u, pct, "█" * bar + "·" * (25 - bar)))
L.append("")

ud = tempfile.mkdtemp(prefix="cdp_")
try:
    with Browser(ud=ud) as br:
        pg = br.new_page()
        pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                 deviceScaleFactor=1, mobile=False)
        pg.navigate(BASE + "/twin", cookie=("cr_session", TOKEN))
        pg.wait_load()
        time.sleep(1.2)
        br.pump(0.4)
        panel = pg.eval(
            "(function(){"
            "var out={rows:[],panels:[]};"
            "function one(p,tag){"
            " var g=p.querySelectorAll('.meter'), kv=p.querySelectorAll('.kv-cell'),"
            "     ml=p.querySelectorAll('.metric-list .ml'), rb=p.querySelectorAll('.rb'),"
            "     bi=p.querySelectorAll('.blocked-item'), mp=p.querySelectorAll('.metric');"
            " var r=p.getBoundingClientRect();"
            " out.panels.push({pos:tag,title:(p.querySelector('.ttl')||{}).textContent||'',"
            "  head:(p.querySelector('.panel-head')||{}).innerText||'',"
            "  w:Math.round(r.width),h:Math.round(r.height),"
            "  blocks:{仪表:g.length,'kv卡片':kv.length,'指标行':ml.length,"
            "          规则条:rb.length,受阻项:bi.length,'接口条':mp.length},"
            "  hdrs:p.querySelectorAll('.panel-body > div > div[style*=font-size]').length,"
            "  seps:p.querySelectorAll('.sep').length,"
            "  gauges:Array.prototype.map.call(g,function(x){"
            "    var bar=x.querySelector('.bar i'),bb=x.getBoundingClientRect();"
            "    var cs=getComputedStyle(x);"
            "    return (x.querySelector('.k span')||{}).textContent+'='+"
            "           (x.querySelector('.k b')||{}).textContent+'['+x.className+']'+"
            "           ' box='+Math.round(bb.width)+'x'+Math.round(bb.height)+"
            "           ' w='+cs.width+' h='+cs.height+' flex='+cs.flex+"
            "           ' ovf='+cs.overflow"
            "           +(bar?(' 条宽'+Math.round(bar.getBoundingClientRect().width)+"
            "             '/'+Math.round(x.querySelector('.bar').getBoundingClientRect().width)):'');}),"
            "  lines:Array.prototype.map.call(ml,function(x){"
            "    return (x.querySelector('.mll')||{}).textContent+' | '+"
            "           (x.querySelector('.mlv')||{}).textContent+' | '+"
            "           (x.querySelector('.mln')||{}).textContent;})});}"
            "Array.prototype.forEach.call(document.querySelectorAll('.twin-mid > .panel'),"
            " function(p,i){one(p,'中排'+(i+1));});"
            "Array.prototype.forEach.call(document.querySelectorAll('.twin-bottom > .panel'),"
            " function(p,i){one(p,'底排'+(i+1));});"
            "var kpi=document.querySelector('.twin-kpi');"
            "out.kpiH=kpi?Math.round(kpi.getBoundingClientRect().height):-1;"
            "return JSON.stringify(out);})()")
        d = json.loads(panel) if isinstance(panel, str) else (panel or {})
        L.append("③ 各面板渲染实况（KPI 行高 %s）" % d.get("kpiH"))
        for p in d.get("panels") or []:
            bl = {k: v for k, v in (p.get("blocks") or {}).items() if v}
            L.append("")
            L.append("   [%s] %s  （%sx%s）"
                     % (p["pos"], p["title"], p["w"], p["h"]))
            L.append("        表头：%s" % (p["head"] or "").replace("\n", " ｜ "))
            L.append("        内容块：%s ｜ 小标题 %d 个 ｜ 分隔线 %d 条 ｜ 视觉形态 %d 种"
                     % (bl or "无", p["hdrs"], p["seps"], len(bl)))
            for g in p.get("gauges") or []:
                L.append("          仪表 %s" % g)
            for ln in p.get("lines") or []:
                L.append("          指标行 %s" % ln)
finally:
    shutil.rmtree(ud, ignore_errors=True)
httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

with io.open(os.path.join(ROOT, "verify", "engine_panel.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("engine panel probe done")
