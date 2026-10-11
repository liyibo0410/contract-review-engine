# -*- coding: utf-8 -*-
"""工作台滚动几何探针：为什么「往下滑时合同正文卡片看不见」。

只量不猜。输出：
  ① 静态几何（顶栏 / 页头 / 双栏 / 正文卡 / 正文内层滚动区 / 风险清单）
  ② 滚动扫描（0 / 25% / 50% / 75% / 100%）下正文卡与正文区的**可见高度**
  ③ 结论：可见高度为 0 的滚动位置就是用户说的「看不见」；并给出「可用高度 vs
     代码里写死的 max-height」的差额，说明这个写死值差在哪。
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
from tools.cdp import Browser                                                         # noqa: E402

PORT = 8805
TMP = tempfile.mkdtemp(prefix="cr_wb_")
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
_rq = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with _op.open(_rq, timeout=30) as r:
    TOKEN = (r.headers.get("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]

# 自动挑一个「正文最长 + 有风险命中」的案件：空数据会让几何结论完全失真
# （实测：随手取第一个案件时 #doc 只有 154px、0 张风险卡，量出来的结论毫无意义）
CASES = st.list_cases()
CAND = [c["id"] for c in CASES][:12] or [1]

GEO = ("(function(){"
       "function R(sel){var e=document.querySelector(sel);if(!e)return null;"
       "var r=e.getBoundingClientRect(),cs=getComputedStyle(e);"
       "return {sel:sel,x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),"
       "h:Math.round(r.height),top:Math.round(r.top),bottom:Math.round(r.bottom),"
       "pos:cs.position,top_css:cs.top,z:cs.zIndex,ovf:cs.overflowY,"
       "mh:cs.maxHeight,ai:cs.alignItems,gtc:cs.gridTemplateColumns,"
       "ch:e.clientHeight,sh:e.scrollHeight};}"
       "var out={vw:innerWidth,vh:innerHeight,nPara:document.querySelectorAll('.doc-para').length,"
       "nRisk:document.querySelectorAll('.risk-card').length,"
       "htmlSH:document.documentElement.scrollHeight,"
       "bodyOv:getComputedStyle(document.body).overflowX+'/'+getComputedStyle(document.body).overflowY,"
       "htmlOv:getComputedStyle(document.documentElement).overflowX+'/'+getComputedStyle(document.documentElement).overflowY,"
       "topbar:R('.topbar'),page:R('.page'),head:R('.page-head'),wb:R('.wb'),"
       "side:R('.wb-side'),panel:R('.wb-side > .panel'),right:R('.wb > div:nth-child(2)'),"
       "doc:R('#doc'),risk:R('#tabBody')};"
       "var rc=document.querySelectorAll('.risk-card');"
       "out.riskCount=rc.length;"
       "if(rc.length){var f=rc[0].getBoundingClientRect(),l=rc[rc.length-1].getBoundingClientRect();"
       "out.riskFirst={top:Math.round(f.top),h:Math.round(f.height)};"
       "out.riskLast={top:Math.round(l.top),bottom:Math.round(l.bottom)};}"
       "var st=[].filter.call(document.querySelectorAll('*'),function(e){"
       "return getComputedStyle(e).position==='sticky';});"
       "out.sticky=st.slice(0,8).map(function(e){return (e.className||e.tagName)"
       "+' top='+getComputedStyle(e).top;});"
       "return JSON.stringify(out);})()")

SWEEP = ("(function(f){var d=document.documentElement;"
         "var max=Math.max(0,d.scrollHeight-innerHeight);"
         "window.scrollTo(0,Math.round(max*f));"
         "function vis(sel){var e=document.querySelector(sel);if(!e)return null;"
         "var r=e.getBoundingClientRect();"
         "var v=Math.max(0,Math.min(r.bottom,innerHeight)-Math.max(r.top,0));"
         "return {top:Math.round(r.top),bottom:Math.round(r.bottom),"
         "h:Math.round(r.height),vis:Math.round(v)};}"
         "var doc=document.querySelector('#doc');"
         "var r2=null;if(doc){var rr=doc.getBoundingClientRect();"
         "var v2=Math.max(0,Math.min(rr.bottom,innerHeight)-Math.max(rr.top,0));"
         "r2={top:Math.round(rr.top),h:Math.round(rr.height),vis:Math.round(v2),"
         "ch:doc.clientHeight,sh:doc.scrollHeight};}"
         "return JSON.stringify({f:f,scrolled:Math.round(window.scrollY),max:max,"
         "panel:vis('.wb > div:nth-child(1) .panel'),doc:r2,"
         "riskPanel:vis('.wb > div:nth-child(2) .panel')});})(%s)")

L = ["工作台滚动几何探针（合同正文卡片为什么「往下滑看不见」）", "=" * 100,
     "目标 %s ｜ 视口 1600x1000 ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]

ud = tempfile.mkdtemp(prefix="cdp_wb_")
CID = CAND[0]
try:
    with Browser(ud=ud) as br:
        def open_case(cid):
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                     deviceScaleFactor=1, mobile=False)
            pg._send("Emulation.setEmulatedMedia",
                     features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
            pg.navigate(BASE + "/workbench?id=%d" % cid, cookie=("cr_session", TOKEN))
            pg.wait_load()
            br.pump(0.35)
            n = json.loads(pg.eval(
                "JSON.stringify({p:document.querySelectorAll('.doc-para').length,"
                "r:document.querySelectorAll('.risk-card').length})") or "{}")
            return pg, int(n.get("p") or 0), int(n.get("r") or 0)

        L.append("⓪ 选案件（正文段落数 + 风险卡片数，取综合最大的那个）")
        best, pg = None, None
        for cid in CAND:
            p, r = open_case(cid)[1:]
            L.append("   #%-3s 正文段落 %3d ｜ 风险卡片 %2d" % (cid, p, r))
            score = p + r * 3
            if best is None or score > best[0]:
                best = (score, cid, p, r)
        CID = best[1]
        L.append("   → 采用案件 #%d（段落 %d，风险卡 %d）" % (CID, best[2], best[3]))
        pg = open_case(CID)[0]

        d = json.loads(pg.eval(GEO) or "{}")
        L.append("")
        L.append("① 静态几何（scrollY=0）")
        L.append("   视口 %sx%s ｜ 文档 scrollHeight=%s ｜ 正文段落 %s ｜ 风险卡片 %s"
                 % (d.get("vw"), d.get("vh"), d.get("htmlSH"),
                    d.get("nPara"), d.get("nRisk")))
        for key, label in (("topbar", "顶栏"), ("head", ".page-head"), ("wb", ".wb 双栏"),
                           ("side", ".wb-side 左列"), ("panel", "左栏=正文卡"),
                           ("right", "右栏"),
                           ("doc", "#doc 正文滚动区"), ("risk", "#tabBody 风险清单")):
            r = d.get(key)
            if not r:
                L.append("   %-16s （不存在）" % label)
                continue
            L.append("   %-16s y=%4s h=%4s bottom=%4s ｜ position=%s top=%s "
                     "overflowY=%s max-height=%s ｜ client=%s scroll=%s"
                     % (label, r["y"], r["h"], r["bottom"], r["pos"], r["top_css"],
                        r["ovf"], r["mh"], r["ch"], r["sh"]))
        wb = d.get("wb") or {}
        L.append("   .wb grid-template-columns=%s ｜ align-items=%s"
                 % (wb.get("gtc"), wb.get("ai")))
        L.append("   html overflow=%s ｜ body overflow=%s%s"
                 % (d.get("htmlOv"), d.get("bodyOv"),
                    "  ← body 成了滚动容器，sticky 会失效（须为 visible 或 clip）"
                    if "hidden" in str(d.get("bodyOv")) else ""))
        side = d.get("side") or {}
        L.append("   .wb-side h=%s（正文卡 h=%s）→ sticky 可移动空间 %s px"
                 % (side.get("h"), (d.get("panel") or {}).get("h"),
                    (side.get("h") or 0) - ((d.get("panel") or {}).get("h") or 0)))
        L.append("   页面上的 sticky 元素：%s" % ("；".join(d.get("sticky") or []) or "（无）"))

        panel = d.get("panel") or {}
        doc = d.get("doc") or {}
        vh = d.get("vh") or 0
        headroom = vh - (panel.get("top") or 0)
        L.append("")
        L.append("   ▶ 正文卡：top=%s h=%s bottom=%s（视口 %s）"
                 % (panel.get("top"), panel.get("h"), panel.get("bottom"), vh))
        L.append("   ▶ 若卡片贴住顶栏下方（top≈76），它可用的最大高度 = %s px" % (vh - 76))
        L.append("   ▶ 而 #doc 的 max-height 写死为 %s px（= 100vh − 258）"
                 % (doc.get("mh")))
        L.append("   ▶ 卡片内除正文外还占 %s px（页头/内边距等），"
                 "故正文区理论高度 = %s − 该值"
                 % ((panel.get("h") or 0) - (doc.get("h") or 0),
                    vh - 76))

        L.append("")
        L.append("② 滚动扫描（把页面滚到 0/25%/50%/75%/100%）")
        L.append("   %-6s %-8s %-30s %-38s" % ("进度", "scrollY", "正文卡（左栏）", "正文区 #doc"))
        bad = []
        for f in (0.0, 0.25, 0.5, 0.75, 1.0):
            r = json.loads(pg.eval(SWEEP % f) or "{}")
            br.pump(0.15)
            p = r.get("panel") or {}
            dd = r.get("doc") or {}
            L.append("   %-6s %-8s top=%5s h=%4s 可见=%4s %-9s top=%5s h=%4s 可见=%4s ch=%s sh=%s"
                     % ("%d%%" % (f * 100), r.get("scrolled"),
                        p.get("top"), p.get("h"), p.get("vis"),
                        "(不可见)" if (p.get("vis") or 0) <= 0 else "",
                        dd.get("top"), dd.get("h"), dd.get("vis"), dd.get("ch"), dd.get("sh")))
            # 修好之后的要求不是「可见 > 0」，而是**整块可见**
            if (p.get("vis") or 0) < (p.get("h") or 1) - 2:
                bad.append("%d%%（可见 %s / %s）" % (f * 100, p.get("vis"), p.get("h")))

        L.append("")
        L.append("=" * 100)
        if bad:
            L.append("结论：以下滚动位置**合同正文卡片没有整块可见**：%s" % "、".join(bad))
            L.append("      （`align-items=%s`；正文区 max-height=%s）"
                     % (wb.get("ai"), doc.get("mh")))
        else:
            L.append("结论：各滚动位置下**合同正文卡片均整块可见**。")
        L.append("=" * 100)

        # ---------------------------------------------------------- ③ 点风险 → 风险清单
        L.append("")
        L.append("③ 点正文里的风险高亮 → 是否跳到右侧对应风险卡片")
        before = json.loads(pg.eval(
            "(function(){window.scrollTo(0,0);"
            "var m=document.querySelector('#doc mark.hl[data-rid]');"
            "if(!m)return JSON.stringify({none:1});"
            "var rid=m.dataset.rid;"
            "var c=document.querySelector('#card-'+rid);"
            "var cr=c?c.getBoundingClientRect():null;"
            "return JSON.stringify({rid:rid,txt:m.textContent.slice(0,18),"
            "hasOnclick:!!m.getAttribute('onclick'),"
            "cardBefore:cr?{top:Math.round(cr.top),bottom:Math.round(cr.bottom)}:null});})()")
            or "{}")
        if before.get("none"):
            L.append("   [ERR] 正文里没有可点的风险高亮，跳过")
            bad.append("无风险高亮")
        else:
            rid = before["rid"]
            L.append("   点第 1 个高亮：data-rid=%s ｜ 文本「%s」｜ 有 onclick=%s"
                     % (rid, before.get("txt"), before.get("hasOnclick")))
            L.append("   点击前目标卡片位置：%s" % json.dumps(before.get("cardBefore")))
            pg.eval("document.querySelector('#doc mark.hl[data-rid]').click()")
            br.pump(0.5)
            time.sleep(0.8)               # 等 smooth 滚动结束
            br.pump(0.4)
            after = json.loads(pg.eval(
                "(function(){var c=document.querySelector('#card-%s');"
                "var r=c?c.getBoundingClientRect():null;"
                "var dv=document.querySelector('.wb-side > .panel').getBoundingClientRect();"
                "var dvis=Math.max(0,Math.min(dv.bottom,innerHeight)-Math.max(dv.top,0));"
                "return JSON.stringify({on:!!(c&&c.classList.contains('on')),"
                "top:r?Math.round(r.top):null,bottom:r?Math.round(r.bottom):null,"
                "inView:!!(r&&r.bottom>76&&r.top<innerHeight-4),"
                "docPanelVis:Math.round(dvis),docPanelH:Math.round(dv.height),"
                "scrollY:Math.round(window.scrollY)});})()" % rid) or "{}")
            L.append("   点击后：卡片 .on=%s ｜ top=%s bottom=%s ｜ 进入视口=%s ｜ 页面 scrollY=%s"
                     % (after.get("on"), after.get("top"), after.get("bottom"),
                        after.get("inView"), after.get("scrollY")))
            L.append("   此时左侧正文卡可见 %s / %s px"
                     % (after.get("docPanelVis"), after.get("docPanelH")))
            if not before.get("hasOnclick"):
                bad.append("正文高亮未绑定 onclick")
            if not after.get("on"):
                bad.append("点击后目标风险卡片未进入选中态")
            if not after.get("inView"):
                bad.append("点击后目标风险卡片不在视口内")
            if (after.get("docPanelVis") or 0) < (after.get("docPanelH") or 1) - 2:
                bad.append("点风险后左侧正文卡被裁掉")

        # ---------------------------------------------------------- ④ 顶栏：导航与 logo
        L.append("")
        L.append("④ 顶栏尺寸（导航偏小 / logo 放大）")
        tb = json.loads(pg.eval(
            "(function(){window.scrollTo(0,0);"
            "var bar=document.querySelector('.topbar');"
            "var links=[].map.call(document.querySelectorAll('.nav a'),function(a){"
            "var r=a.getBoundingClientRect(),cs=getComputedStyle(a);"
            "return {t:a.textContent.trim(),w:Math.round(r.width),h:Math.round(r.height),"
            "fs:cs.fontSize,pt:cs.paddingTop,lh:cs.lineHeight};});"
            "var lg=document.querySelector('.brand-logo');var lr=lg?lg.getBoundingClientRect():null;"
            "var b=document.querySelector('.brand-txt b');var bi=document.querySelector('.brand-txt i');"
            "return JSON.stringify({topbarH:Math.round(bar.getBoundingClientRect().height),"
            "topbarScrollW:bar.scrollWidth,topbarClientW:bar.clientWidth,"
            "links:links,logo:lg?{w:Math.round(lr.width),h:Math.round(lr.height),"
            "natural:lg.naturalWidth+'x'+lg.naturalHeight,file:(lg.getAttribute('src')||'').split('/').pop()}:null,"
            "brandB:b?{fs:getComputedStyle(b).fontSize,txt:b.textContent.trim()}:null,"
            "brandI:bi?bi.textContent.trim():null});})()") or "{}")
        L.append("   顶栏高度 %s px ｜ scrollWidth=%s clientWidth=%s%s"
                 % (tb.get("topbarH"), tb.get("topbarScrollW"), tb.get("topbarClientW"),
                    "  ← 横向溢出！" if (tb.get("topbarScrollW") or 0) > (tb.get("topbarClientW") or 0) + 1 else ""))
        for lk in tb.get("links") or []:
            L.append("     导航「%s」 %sx%s ｜ font-size=%s ｜ padding-top=%s ｜ line-height=%s"
                     % (lk["t"], lk["w"], lk["h"], lk["fs"], lk["pt"], lk["lh"]))
        lg = tb.get("logo") or {}
        L.append("   logo %sx%s ｜ 位图 %s ｜ 文件 %s"
                 % (lg.get("w"), lg.get("h"), lg.get("natural"), lg.get("file")))
        L.append("   品牌字号：%s（%s）｜ 副标题 %s"
                 % ((tb.get("brandB") or {}).get("fs"), (tb.get("brandB") or {}).get("txt"),
                    tb.get("brandI")))
        nav = (tb.get("links") or [{}])[0]
        if (tb.get("topbarH") or 0) > 74:
            bad.append("顶栏高度 %s px（>74 说明换行成了两行）" % tb.get("topbarH"))
        if (tb.get("topbarScrollW") or 0) > (tb.get("topbarClientW") or 0) + 1:
            bad.append("顶栏横向溢出")
        try:
            if float(str(nav.get("fs", "0")).replace("px", "")) < 14:
                bad.append("导航字号仍偏小（%s）" % nav.get("fs"))
        except ValueError:
            pass
        if (lg.get("h") or 0) < 34:
            bad.append("logo 高度 %s px（未放大）" % lg.get("h"))

        L.append("")
        L.append("=" * 100)
        L.append("最终判定：%s" % ("全部通过" if not bad else "仍有问题 —— " + "；".join(bad)))
        L.append("=" * 100)
finally:
    shutil.rmtree(ud, ignore_errors=True)
httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

with io.open(os.path.join(ROOT, "verify", "workbench_geom.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("workbench geom done: case=%s, 问题 %d 项" % (CID, len(bad)))
sys.exit(0 if not bad else 1)
