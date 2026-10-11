#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶栏体检：量出每个子元素的宽度与所在行，定位「桌面宽度下顶栏换行」的元凶。

顺带量统计卡内部结构（下钻提示 ::after 与副标题的关系）、以及各页面一屏内的
纵向占用，帮助判断「页面显得挤/空」。
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

PORT = 8799
TMP = tempfile.mkdtemp(prefix="cr_tb_")
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

TB = r"""
(function(){
  var bar=document.querySelector('#topbar')||document.querySelector('.topbar');
  if(!bar) return JSON.stringify({error:'no topbar'});
  var br=bar.getBoundingClientRect();
  var out={barH:Math.round(br.height), barW:Math.round(br.width), rows:[], kids:[],
           navLinks:[], wrap:getComputedStyle(bar).flexWrap,
           gap:getComputedStyle(bar).columnGap, pad:getComputedStyle(bar).padding};
  var rowsMap={};
  Array.prototype.forEach.call(bar.children,function(e){
    var r=e.getBoundingClientRect();
    var tag=e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+(e.className&&typeof e.className==='string'?'.'+e.className.trim().split(/\s+/).join('.'):'');
    var y=Math.round(r.top);
    rowsMap[y]=(rowsMap[y]||0)+1;
    out.kids.push({tag:tag.slice(0,58),w:Math.round(r.width),h:Math.round(r.height),
                   x:Math.round(r.left),y:y,right:Math.round(r.right),
                   txt:(e.textContent||'').replace(/\s+/g,' ').trim().slice(0,26)});
  });
  Object.keys(rowsMap).forEach(function(k){out.rows.push({y:+k,n:rowsMap[k]});});
  out.rows.sort(function(a,b){return a.y-b.y;});
  var nav=bar.querySelector('.nav');
  if(nav){ out.navW=Math.round(nav.getBoundingClientRect().width);
    out.navScrollW=nav.scrollWidth; out.navClientW=nav.clientWidth;
    Array.prototype.forEach.call(nav.querySelectorAll('a'),function(a){
      var r=a.getBoundingClientRect();
      out.navLinks.push({t:a.textContent.trim(),w:Math.round(r.width),x:Math.round(r.left)});
    });
  }
  // 统计卡内部几何
  var card=document.querySelector('#stats .stat.clickable');
  if(card){
    var cr=card.getBoundingClientRect(), cs=getComputedStyle(card,'::after');
    var val=card.querySelector('.v'), x=card.querySelector('.x'), k=card.querySelector('.k');
    out.card={h:Math.round(cr.height),w:Math.round(cr.width),pad:getComputedStyle(card).padding,
      afterContent:cs.content, afterRight:cs.right, afterBottom:cs.bottom,
      k:{h:Math.round(k.getBoundingClientRect().height),bottom:Math.round(k.getBoundingClientRect().bottom)},
      v:{h:Math.round(val.getBoundingClientRect().height),bottom:Math.round(val.getBoundingClientRect().bottom)},
      x:{h:Math.round(x.getBoundingClientRect().height),bottom:Math.round(x.getBoundingClientRect().bottom)}};
  }
  // 首屏纵向节奏
  var ph=document.querySelector('.page-head');
  out.page={docH:document.documentElement.scrollHeight,
            headBottom:ph?Math.round(ph.getBoundingClientRect().bottom):null,
            statsBottom:card?Math.round(card.closest('.stats').getBoundingClientRect().bottom):null};
  return JSON.stringify(out);
})()
"""

L = ["顶栏与统计卡几何体检（CDP）", "=" * 100, "目标 %s ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
ud = tempfile.mkdtemp(prefix="cdp_")
try:
    with Browser(ud=ud) as br:
        for width in (1600, 1440, 1280, 1100, 980, 760, 390):
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=width, height=900,
                     deviceScaleFactor=1, mobile=width < 700)
            pg.navigate(BASE + "/", cookie=("cr_session", TOKEN))
            pg.wait_load()
            raw = pg.eval(TB)
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            rows = d.get("rows") or []
            L.append("视口 %4dpx ｜ 顶栏高 %spx ｜ 行数 %d ｜ flex-wrap=%s ｜ gap=%s"
                     % (width, d.get("barH"), len(rows), d.get("wrap"), d.get("gap")))
            for k in (d.get("kids") or []):
                L.append("      %-56s w=%-5s h=%-4s x=%-5s y=%-4s  %s"
                         % (k["tag"], k["w"], k["h"], k["x"], k["y"], k["txt"]))
            if d.get("navLinks"):
                L.append("      nav 宽 %s（内容 %s）｜ 链接：%s"
                         % (d.get("navW"), d.get("navScrollW"),
                            "、".join("%s(%s)" % (x["t"], x["w"]) for x in d["navLinks"])))
            c = d.get("card")
            if c:
                L.append("      统计卡 h=%s w=%s padding=%s ｜ ::after content=%s right=%s bottom=%s"
                         % (c["h"], c["w"], c["pad"], c["afterContent"], c["afterRight"],
                            c["afterBottom"]))
                L.append("        k 底 %s ｜ v 底 %s ｜ x 底 %s（卡片底需留出 ::after 空间）"
                         % (c["k"]["bottom"], c["v"]["bottom"], c["x"]["bottom"]))
            if d.get("page"):
                L.append("      文档总高 %s ｜ 头部底 %s ｜ 统计卡底 %s"
                         % (d["page"]["docH"], d["page"]["headBottom"], d["page"]["statsBottom"]))
            L.append("")
finally:
    shutil.rmtree(ud, ignore_errors=True)
httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

with io.open(os.path.join(ROOT, "verify", "topbar_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("topbar probe done")
