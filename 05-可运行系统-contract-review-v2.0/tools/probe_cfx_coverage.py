# -*- coding: utf-8 -*-
"""卡片动效覆盖率探针：逐页统计被装饰的卡片数量与种类，证明「所有页面的全部卡片」都覆盖到。"""
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

PORT = 8806
TMP = tempfile.mkdtemp(prefix="cr_cov_")
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
_done = [c for c in st.list_cases() if c["review_status"] == "completed"]
CID = _done[0]["id"] if _done else 1
BASE = "http://127.0.0.1:%d" % PORT

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _login(username, password):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with _op.open(req, timeout=30) as r:
        return (r.headers.get("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]


TOKEN = _login("admin", "Admin@2026")
# 越权拦截页要用一个**确实没有该能力**的角色去访问受限页面才会出现，
# 所以单独取一个业务经办人的会话（/rules 需要 edit_rules，applicant 没有）。
TOKEN_APP = _login("applicant", "Apply@2026")

COV = ("(function(){"
       "var cfx=document.querySelectorAll('.cfx');"
       "var kinds={};"
       "Array.prototype.forEach.call(cfx,function(e){"
       "  var ks=Array.prototype.filter.call(e.classList,function(c){"
       "    return ['panel','card','stat','kpi','tk','kv-cell','risk-card','mode-col',"
       "            'mock-item','cell','mock-comment','login-card','role-item','deny-card',"
       "      'deny-cell','meter','pipe-node','sec-item','model-card','store-card']"
       "      .indexOf(c)>-1;});"
       "  (ks.length?ks:['其他']).forEach(function(k){kinds[k]=(kinds[k]||0)+1;});});"
       "var all=document.querySelectorAll('.panel,.card,.stat,.kpi,.tk,.kv-cell,.risk-card,"
       "  .mode-col,.mock-item,.mock-kv .cell,.mock-comment,.login-card,.role-item,"
       "  .deny-card,.deny-cell,.meter,.pipe-node,.sec-item,.model-card,.store-card');"
       "var miss=[];"
       "Array.prototype.forEach.call(all,function(e){"
       "  if(!e.classList.contains('cfx')){"
       "    miss.push(e.className.slice(0,30));}});"
       "return JSON.stringify({cfx:cfx.length,total:all.length,kinds:kinds,"
       "lift:document.querySelectorAll('.cfx-lift').length,"
       "beam:document.querySelectorAll('.cfx-beam').length,"
       "title:document.title,path:location.pathname,"
       "miss:miss.slice(0,6)});})()")

PAGES = [("审查大盘", "/", TOKEN, ""), ("智能审查工作台", "/workbench?id=%d" % CID, TOKEN, ""),
         ("合规规则与模板", "/rules", TOKEN, ""), ("动态孪生看板", "/twin", TOKEN, ""),
         ("模拟审批系统", "/mock", TOKEN, ""), ("登录页", "/login", TOKEN, ""),
         # 越权拦截页：必须真的落在拦截页上（否则被重定向后「有卡片」会假通过）
         ("越权拦截页", "/rules", TOKEN_APP, ".deny-card")]

L = ["卡片动效覆盖率（逐页）", "=" * 100,
     "目标 %s ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
OK = [0]
N = [0]

ud = tempfile.mkdtemp(prefix="cdp_")
try:
    with Browser(ud=ud) as br:
        for name, path, tok, need_sel in PAGES:
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                     deviceScaleFactor=1, mobile=False)
            pg._send("Emulation.setEmulatedMedia",
                     features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
            pg.navigate(BASE + path, cookie=("cr_session", tok))
            pg.wait_load()
            time.sleep(1.4)
            br.pump(0.3)
            d = json.loads(pg.eval(COV) or "{}")
            kinds = "、".join("%s×%s" % (k, v) for k, v in (d.get("kinds") or {}).items())
            landed = True
            if need_sel:
                landed = bool(pg.eval("!!document.querySelector(%s)" % json.dumps(need_sel)))
            N[0] += 1
            ok = (d.get("total", 0) > 0 and d.get("cfx", 0) >= d.get("total", 0) and landed)
            if ok:
                OK[0] += 1
            L.append("%s %-14s 卡片 %2s 个 → 已装饰 %2s 个 ｜ 上浮 %s ｜ 流光 %s"
                     % ("[OK ]" if ok else "[ERR]", name, d.get("total"), d.get("cfx"),
                        d.get("lift"), d.get("beam")))
            L.append("        实际落点：%s（%s）%s"
                     % (d.get("path"), (d.get("title") or "")[:34],
                        "" if landed else "  ← **未落到目标页**"))
            L.append("        种类：%s" % kinds)
            if d.get("miss"):
                L.append("        **未装饰**：%s" % "、".join(d["miss"]))
finally:
    shutil.rmtree(ud, ignore_errors=True)
httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

L.append("")
L.append("=" * 100)
L.append("结果：%d / %d 个页面的卡片全部覆盖" % (OK[0], N[0]))
L.append("=" * 100)
with io.open(os.path.join(ROOT, "verify", "cfx_coverage.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("cfx coverage: %d / %d" % (OK[0], N[0]))
sys.exit(0 if OK[0] == N[0] else 1)
