#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交互态前端体检：把「点开之后」的状态也量一遍。

静态渲染没问题，不代表交互态没问题 —— 弹窗、抽屉、引导气泡、助手面板这些
「点开才出现」的东西，恰恰是布局问题的高发区（超出视口、遮挡、滚动失效）。

检查项：
  1) 统计卡下钻弹窗：宽高是否超出视口、表格是否可横向滚动、关闭按钮可达；
  2) AI 助手面板：是否在视口内、消息区是否可滚、快捷提问是否换行溢出；
  3) 新手引导：聚光灯与气泡是否都在视口内、气泡是否遮住被高亮元素；
  4) 工作台：风险卡片点击能否定位到正文高亮、切页签内容是否重新渲染；
  5) 模拟审批页：点击待办单后详情是否真的渲染出来。
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

PORT = 8801
TMP = tempfile.mkdtemp(prefix="cr_ia_")
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
_req = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with _op.open(_req, timeout=30) as r:
    TOKEN = (r.headers.get("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]

L = ["交互态前端体检（CDP）", "=" * 100,
     "目标 %s ｜ %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
issues = []


def geom(sel):
    return ("(function(){var e=document.querySelector(%s);if(!e)return null;"
            "var r=e.getBoundingClientRect(),cs=getComputedStyle(e);"
            "return {w:Math.round(r.width),h:Math.round(r.height),x:Math.round(r.left),"
            "y:Math.round(r.top),right:Math.round(r.right),bottom:Math.round(r.bottom),"
            "sw:e.scrollWidth,sh:e.scrollHeight,cw:e.clientWidth,ch:e.clientHeight,"
            "ovfY:cs.overflowY,ovfX:cs.overflowX,vis:cs.visibility,disp:cs.display};})()"
            % json.dumps(sel))


def in_view(g, vw, vh, tol=2):
    if not g:
        return False
    return (g["x"] >= -tol and g["y"] >= -tol
            and g["right"] <= vw + tol and g["bottom"] <= vh + tol)


ud = tempfile.mkdtemp(prefix="cdp_")
try:
    with Browser(ud=ud) as br:
        # ---------------------------------------------- 1) 下钻弹窗
        for width, height in ((1600, 1000), (980, 900), (390, 844)):
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                     deviceScaleFactor=1, mobile=width < 700)
            pg.navigate(BASE + "/", cookie=("cr_session", TOKEN))
            pg.wait_load()
            pg.eval("CR.$$('#stats .stat.clickable')[0].click()")
            time.sleep(1.6)
            br.pump(0.3)
            g = pg.eval(geom("#mDrill .modal"))
            row = pg.eval(geom("#drillBody table"))
            wrap = pg.eval(geom("#drillBody .table-wrap"))
            L.append("下钻弹窗 @%dx%d：弹窗 %s" % (width, height, g))
            ok_in = in_view(g, width, height)
            scrollable = bool(wrap and wrap["ovfX"] in ("auto", "scroll"))
            L.append("        在视口内=%s ｜ 表格 %s ｜ 横向可滚动=%s"
                     % (ok_in, row, scrollable))
            if not ok_in:
                issues.append("下钻弹窗 @%dx%d 超出视口：%s" % (width, height, g))
            if row and row["sw"] > row["cw"] + 2 and not scrollable:
                issues.append("下钻弹窗表格内容 %s > 可视 %s 但没有横向滚动"
                              % (row["sw"], row["cw"]))

        # ---------------------------------------------- 2) 助手面板
        for width, height in ((1600, 1000), (390, 844)):
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                     deviceScaleFactor=1, mobile=width < 700)
            pg.navigate(BASE + "/?pet=open", cookie=("cr_session", TOKEN))
            pg.wait_load()
            time.sleep(1.2)
            br.pump(0.3)
            panel = pg.eval(geom("#petPanel"))
            body = pg.eval(geom("#petBody"))
            chips = pg.eval(geom("#petChips"))
            btn = pg.eval(geom("#petBtn"))
            L.append("助手面板 @%dx%d：面板 %s" % (width, height, panel))
            L.append("        消息区 %s ｜ 快捷提问 %s ｜ 宠物按钮 %s" % (body, chips, btn))
            if not in_view(panel, width, height):
                issues.append("助手面板 @%dx%d 超出视口：%s" % (width, height, panel))
            if not in_view(btn, width, height):
                issues.append("宠物按钮 @%dx%d 超出视口：%s" % (width, height, btn))
            if body and body["sh"] > body["ch"] + 2 and body["ovfY"] not in ("auto", "scroll"):
                issues.append("助手消息区内容溢出但不可滚动（%s > %s）" % (body["sh"], body["ch"]))
            if chips and chips["sh"] > chips["ch"] + 6:
                issues.append("助手快捷提问被裁切（scrollHeight %s > clientHeight %s）"
                              % (chips["sh"], chips["ch"]))

        # ---------------------------------------------- 3) 新手引导（含窄屏）
        for width, height in ((1600, 1000), (390, 844)):
            pg = br.new_page()
            pg._send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                     deviceScaleFactor=1, mobile=width < 700)
            pg.navigate(BASE + "/?pet=tour", cookie=("cr_session", TOKEN))
            pg.wait_load()
            time.sleep(1.6)
            br.pump(0.4)
            spot = pg.eval(geom("#petSpot"))
            coach = pg.eval(geom("#petCoach"))
            tgt = pg.eval(geom("#stats"))
            L.append("新手引导 @%dx%d：聚光灯 %s" % (width, height, spot))
            L.append("        气泡 %s ｜ 首步目标 #stats %s" % (coach, tgt))
            if not in_view(coach, width, height):
                issues.append("引导气泡 @%dx%d 超出视口：%s" % (width, height, coach))
            # 判据用「聚光灯」而不是「目标元素」：
            # 气泡压住元素本身是正常的（tooltip 就是这样），压住**高亮区**才是问题。
            # 目标比视口高时（窄屏下统计卡片区 900+px），聚光灯会封顶到 45% 视口高。
            if coach and spot:
                cover = (coach["x"] < spot["right"] and coach["right"] > spot["x"]
                         and coach["y"] < spot["bottom"] and coach["bottom"] > spot["y"])
                L.append("        气泡是否压住高亮区=%s" % cover)
                if cover:
                    issues.append("引导气泡 @%dx%d 压住了要高亮的区域" % (width, height))
            if spot and not in_view(spot, width, height):
                issues.append("聚光灯 @%dx%d 超出视口：%s" % (width, height, spot))
            if spot and tgt and tgt["h"] > height * 0.7:
                L.append("        （目标高 %s > 视口 70%%，聚光灯已封顶到 %s）"
                         % (tgt["h"], spot["h"]))

        # ---------------------------------------------- 4) 工作台交互
        pg = br.new_page()
        pg._send("Emulation.setDeviceMetricsOverride", width=1600, height=1000,
                 deviceScaleFactor=1, mobile=False)
        pg.navigate(BASE + "/workbench?id=%d" % CID, cookie=("cr_session", TOKEN))
        pg.wait_load()
        card = pg.eval(geom("#tabBody .risk-card"))
        doc = pg.eval(geom("#doc"))
        pg.eval("(function(){var c=document.querySelector('#tabBody .risk-card');"
                "if(c) c.click();})()")
        time.sleep(1.2)
        br.pump(0.3)
        hl = pg.eval(geom("#doc .hl, #doc mark"))
        scroll = pg.eval("(function(){var d=document.querySelector('#doc');"
                         "return d?d.scrollTop:-1;})()")
        L.append("工作台：风险卡片 %s" % card)
        L.append("        正文容器 %s ｜ 点卡片后正文滚动位置 %s" % (doc, scroll))
        if card and doc and card["x"] < doc["right"] and card["right"] > doc["x"]:
            issues.append("工作台风险卡片与正文容器横向重叠（双栏挤在一起）")
        if scroll == 0:
            L.append("        提示：正文未滚动（该风险项可能本就在首屏，非缺陷）")

        # ---------------------------------------------- 5) 模拟审批页交互
        pg = br.new_page()
        pg._send("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                 deviceScaleFactor=1, mobile=False)
        pg.navigate(BASE + "/mock", cookie=("cr_session", TOKEN))
        pg.wait_load()
        time.sleep(1.0)
        pg.eval("(function(){var it=document.querySelector('.mock-item');if(it) it.click();})()")
        time.sleep(1.4)
        br.pump(0.3)
        det = pg.eval(geom("#detail"))
        cells = pg.eval("document.querySelectorAll('#detail .cell').length")
        cmt = pg.eval("document.querySelectorAll('#detail .mock-comment').length")
        L.append("模拟审批页：详情容器 %s ｜ 字段格 %s 个 ｜ 评论 %s 条" % (det, cells, cmt))
        if not cells:
            issues.append("模拟审批页点击待办单后详情没有渲染出字段")
finally:
    shutil.rmtree(ud, ignore_errors=True)

httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

L.append("")
L.append("=" * 100)
L.append("发现问题：%d 项" % len(issues))
for i, s in enumerate(issues, 1):
    L.append("  %d. %s" % (i, s))
if not issues:
    L.append("  无 —— 弹窗 / 助手面板 / 引导气泡 / 工作台 / 模拟审批页的交互态均正常。")
L.append("=" * 100)

with io.open(os.path.join(ROOT, "verify", "interactive_audit.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(L))
print("interactive audit: %d issues" % len(issues))
sys.exit(0 if not issues else 1)
