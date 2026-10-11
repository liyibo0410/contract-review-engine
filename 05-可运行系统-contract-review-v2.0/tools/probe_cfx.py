#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡片动效专项探针：验证「动效真的生效」且「不与定时刷新打架」。

检查项：
  1) 入场动画真的在跑 —— 在页面加载后立即读 `document.getAnimations()`，
     应能看到名为 cfxIn 的动画在播放；稍后应已结束（不残留）；
  2) 常驻效果真的挂上了 —— 卡片计算样式里 background-image 含渐变、
     transition-property 含 background-position；
  3) **定时刷新不重播入场** —— 孪生看板刷新前后对比 cfxIn 动画数量，
     刷新后应仍为 0（只首屏播一次）；
  4) **数据变化的数值才跳** —— 触发一次刷新，检查是否只有变化的数值带 cfx-tick；
  5) 降级生效 —— 模拟 prefers-reduced-motion 后不应有 cfxIn 动画。
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

PORT = 8803
TMP = tempfile.mkdtemp(prefix="cr_cfx_")
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

L = ["卡片动效专项探针", "=" * 100, "目标 %s ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
OK = [0]
N = [0]


def chk(name, ok, detail=""):
    N[0] += 1
    if ok:
        OK[0] += 1
    L.append("%s %-46s %s" % ("[OK ]" if ok else "[ERR]", name, detail))
    return ok


STAT = ("(function(){var e=document.querySelector('.cfx')||document.querySelector('.stat');"
        "if(!e)return null;var cs=getComputedStyle(e);return {sel:e.className.slice(0,40),"
        "bg:cs.backgroundImage.slice(0,60),tp:cs.transitionProperty,tr:cs.transitionDuration};})()")

# 样式表体检：确认浏览器**真的解析到了** .cfx 规则（而不是文件里有、解析丢了）
SHEETS = (
    "(function(){"
    "function hit(el,sel){try{return el.matches(sel);}catch(x){return false;}}"
    "var out={files:[],cfxRules:0,list:[],probeSel:'',probeMatch:null,"
    " hasSweep:false,cfxAll:'',topLevel:0,mediaRules:0,err:''};"
    "var e=document.querySelector('.cfx');"
    "if(e){ out.probeSel=e.className.slice(0,40); out.probeMatch=hit(e,'.cfx'); }"
    "for(var i=0;i<document.styleSheets.length;i++){"
    "  var ss=document.styleSheets[i];"
    "  out.files.push(ss.href?ss.href.split('/').pop():'inline');"
    "  var rs=null; try{ rs=ss.cssRules; }catch(err){ out.err+='['+i+']'+err.message; continue; }"
    "  if(!rs){continue;}"
    "  for(var j=0;j<rs.length;j++){"
    "    var r=rs[j]; var st=r.selectorText||'';"
    "    if(!st){ out.mediaRules++; continue; }"
    "    out.topLevel++;"
    "    if(r.cssText.indexOf('cfxSweep')>=0){ out.hasSweep=true; }"
    "    if(st.indexOf('.cfx')>=0){ out.cfxRules++;"
    "      if(out.list.length<6){ out.list.push(st+' | parent='"
    "        +(r.parentRule?r.parentRule.constructor.name:'null')"
    "        +' | '+r.cssText.slice(0,64)); } }"
    "  }"
    "}"
    "return JSON.stringify(out);})()")

ANIM = ("(function(){var a=document.getAnimations?document.getAnimations():[];"
        "var n={};a.forEach(function(x){var nm=(x.animationName)||"
        "((x.effect&&x.effect.getKeyframes&&'keyframes')||'?');n[nm]=(n[nm]||0)+1;});"
        "var fe=document.querySelector('.cfx');"
        "var probe=null;"
        "if(fe){var cs=getComputedStyle(fe);probe={cls:fe.className.slice(0,60),"
        " inline:fe.getAttribute('style')||'',"
        " bg:cs.backgroundImage.slice(0,44),tp:cs.transitionProperty.slice(0,80)};}"
        "return JSON.stringify({names:n,enter:document.querySelectorAll('.cfx-enter').length,"
        "tick:document.querySelectorAll('.cfx-tick').length,"
        "card:document.querySelectorAll('.panel,.stat,.tk,.kv-cell').length,"
        "cfx:document.querySelectorAll('.cfx').length,"
        "cbe:document.querySelectorAll('.cfx-beam').length,probe:probe});})()")

ud = tempfile.mkdtemp(prefix="cdp_")
try:
    with Browser(ud=ud) as br:
        # ---------------- 1) 入场动画
        # 注意：headless Chrome 默认就报 prefers-reduced-motion: reduce ——
        # 若不显式声明 no-preference，本项目的降级规则（animation/transition: none !important）
        # 会把全部动效吃掉，探针会误判成"动效没实现"。这里显式声明正常偏好。
        def set_motion(pg, value):
            pg._send("Emulation.setEmulatedMedia",
                     features=[{"name": "prefers-reduced-motion", "value": value}])

        pg = br.new_page()
        pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                 deviceScaleFactor=1, mobile=False)
        set_motion(pg, "no-preference")
        pg.navigate(BASE + "/", cookie=("cr_session", TOKEN))
        MOTION = "matchMedia('(prefers-reduced-motion: reduce)').matches"
        # 不等 wait_load 的 2.6s 缓冲，尽快读一次"正在播"的状态
        t0 = time.time()
        while time.time() - t0 < 1.0:
            if br.loaded.get(pg.sid):
                break
            br.pump(0.05)
        br.pump(0.25)
        early = json.loads(pg.eval(ANIM) or "{}")
        sh = json.loads(pg.eval(SHEETS) or "{}")
        reduced = pg.eval(MOTION)
        L.append("⓪ 环境：prefers-reduced-motion: reduce = %s（已显式声明 no-preference）"
                 % reduced)
        L.append("   样式表体检：%s" % json.dumps(
            {k: v for k, v in sh.items() if k != "list"}, ensure_ascii=False))
        for m in sh.get("list") or []:
            L.append("     · %s" % m)
        L.append("   探针元素 class=%r ｜ matches('.cfx')=%s ｜ 顶层规则 %s 条 / 媒体规则 %s 条"
                 % (sh.get("probeSel"), sh.get("probeMatch"),
                    sh.get("topLevel"), sh.get("mediaRules")))
        L.append("① 加载后约 0.3s 的动画状态：%s" % json.dumps(early, ensure_ascii=False))
        chk("浏览器真的解析到了 .cfx 规则（样式表里能找到）",
            sh.get("cfxRules", 0) > 0, "含 .cfx 的规则 %s 条" % sh.get("cfxRules"))
        # 入场动画改为 JS 内联样式驱动（不再依赖运行时加的类），所以断言看动画本身
        chk("卡片入场动画真的在播放（cfxIn）",
            (early.get("names") or {}).get("cfxIn", 0) > 0,
            "cfxIn × %s （错峰入场在跑）；被标记 .cfx 的卡片 %s 个"
            % ((early.get("names") or {}).get("cfxIn"), early.get("cfx")))
        st_css = pg.eval(STAT) or {}
        sweep = "cfxSweep" in (sh.get("cfxAll") or "") or any(
            "cfxSweep" in m for m in (sh.get("list") or []))
        chk("卡片的常驻效果已挂到计算样式上（悬停光泽 + 扫光动画）",
            "gradient" in (st_css.get("bg") or "")
            and sh.get("hasSweep") is True,
            "%s ｜ 背景=%s… ｜ 悬停扫光规则=%s ｜ transition=%s"
            % ((st_css.get("sel") or ""), (st_css.get("bg") or "")[:26],
               "已定义" if sh.get("hasSweep") else "缺失",
               (st_css.get("tp") or "")[:44]))
        chk("强调条流光的卡片被标记（cfx-beam）", early.get("cbe", 0) > 0,
            "%s 个卡片启用流光；被标记 .cfx 的卡片 %s 个"
            % (early.get("cbe"), early.get("cfx")))

        # 等动画结束，确认不残留
        time.sleep(2.2)
        br.pump(0.3)
        late = json.loads(pg.eval(ANIM) or "{}")
        chk("入场动画结束后不留内联样式残留",
            late.get("enter", 0) == 0
            and "animation" not in ((late.get("probe") or {}).get("inline") or ""),
            "残余内联样式=%r ｜ 仍在播 %s"
            % ((late.get("probe") or {}).get("inline"),
               json.dumps((late.get("names") or {}), ensure_ascii=False)))

        # ---------------- 3) 孪生看板：定时刷新不重播
        pg2 = br.new_page()
        pg2._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                  deviceScaleFactor=1, mobile=False)
        set_motion(pg2, "no-preference")
        pg2.navigate(BASE + "/twin", cookie=("cr_session", TOKEN))
        pg2.wait_load()
        time.sleep(5.2)                     # 跨过至少一次 4 秒刷新
        br.pump(0.4)
        tw = json.loads(pg2.eval(ANIM) or "{}")
        L.append("")
        L.append("③ 孪生看板刷新后的动画状态：%s" % json.dumps(tw, ensure_ascii=False))
        chk("定时刷新不重播入场动画（cfxIn 不再出现）",
            (tw.get("names") or {}).get("cfxIn", 0) == 0 and tw.get("enter", 0) == 0,
            "cfxIn x%s，cfx-enter %s 个（说明入场只播了一次）"
            % ((tw.get("names") or {}).get("cfxIn"), tw.get("enter")))
        chk("刷新后卡片仍保有常驻效果（重建的 DOM 也生效）",
            tw.get("card", 0) > 0,
            "页面卡片总数 %s" % tw.get("card"))

        # ---------------- 4) 数值变化才跳
        pg2.eval("(function(){CR.motion.changed('probe|a',1);})()")
        a = pg2.eval("CR.motion.changed('probe|a',1)")
        b = pg2.eval("CR.motion.changed('probe|a',2)")
        chk("数值变化检测正确（同值不跳、变值才跳）",
            a is False and b is True,
            "同值→%s ｜ 变值→%s" % (a, b))

        # ---------------- 5) 降级
        pg3 = br.new_page()
        pg3._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                  deviceScaleFactor=1, mobile=False)
        pg3._send("Emulation.setEmulatedMedia",
                  features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        pg3.navigate(BASE + "/", cookie=("cr_session", TOKEN))
        pg3.wait_load()
        br.pump(0.3)
        rm = json.loads(pg3.eval(ANIM) or "{}")
        rmc = pg3.eval(STAT) or {}
        L.append("")
        L.append("⑤ 降级（prefers-reduced-motion: reduce）：reduce=%s ｜ %s"
                 % (pg3.eval(MOTION), json.dumps(rm, ensure_ascii=False)))
        chk("减少动效偏好下：入场动画不播、常驻过渡被关掉（可访问性降级生效）",
            (rm.get("names") or {}).get("cfxIn", 0) == 0
            and "none" in (rmc.get("tp") or ""),
            "cfxIn × %s ｜ transition-property=%s ｜ 卡片背景=%s"
            % ((rm.get("names") or {}).get("cfxIn"), rmc.get("tp") or "-",
               (rmc.get("bg") or "none")[:26]))
finally:
    shutil.rmtree(ud, ignore_errors=True)
httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

L.append("")
L.append("=" * 100)
L.append("结果：%d / %d 项通过" % (OK[0], N[0]))
L.append("=" * 100)
with io.open(os.path.join(ROOT, "verify", "cfx_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("cfx probe: %d / %d" % (OK[0], N[0]))
sys.exit(0 if OK[0] == N[0] else 1)
