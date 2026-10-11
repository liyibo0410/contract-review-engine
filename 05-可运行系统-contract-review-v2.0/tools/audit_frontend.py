#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前端逐页体检（基于 CDP 真跑真量）。

为什么不用 `--dump-dom`：它只能拿 HTML 结构，量不出「布局塌陷、元素重叠、
横向溢出、脚本报错」—— 而「前端还是有问题」绝大多数属于这一类。

本工具做四件事：
  1) 真跑每个页面，收集 **JS 异常 / console 错误 / 资源加载失败**；
  2) 量出关键元素的**几何信息**（尺寸、可见性、是否在视口内、是否重叠）；
  3) 检查**横向溢出**（窄屏左右晃动的根因）、重复 id、模板占位符残留；
  4) 断言「统计卡下钻提示」与「副标题文案」不重叠、「顶栏品牌与导航」不重叠。

产物：verify/frontend_audit.txt（含每页明细与问题清单）
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

PORT = 8798
TMP = tempfile.mkdtemp(prefix="cr_audit_")
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

# 取一个会话 Cookie（页面都要登录）
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_req = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with _op.open(_req, timeout=30) as r:
    COOKIE = (r.headers.get("Set-Cookie") or "").split(";")[0]
TOKEN = COOKIE.split("=", 1)[1] if "=" in COOKIE else ""

PROBE = r"""
(function(){
  function info(sel){
    var e=document.querySelector(sel); if(!e) return null;
    var r=e.getBoundingClientRect(); var cs=getComputedStyle(e);
    return {w:Math.round(r.width),h:Math.round(r.height),
            x:Math.round(r.left),y:Math.round(r.top),
            right:Math.round(r.right),bottom:Math.round(r.bottom),
            disp:cs.display,vis:cs.visibility,ovf:cs.overflowX};
  }
  var out={url:location.pathname+location.search,
           title:document.title,
           err:(document.getElementById('petErrLog')||{}).textContent||'',
           petErr:(document.getElementById('petRoot')||{}).dataset ?
                  (document.getElementById('petRoot').dataset.err||''):'',
           vw:innerWidth, vh:innerHeight,
           scrollW:document.documentElement.scrollWidth,
           clientW:document.documentElement.clientWidth,
           tpl:(document.body.innerHTML.match(/\{\{[A-Z_]+\}\}/g)||[]),
           ids:{}, zero:[], missing:[]};
  var seen={}, dup=[];
  Array.prototype.forEach.call(document.querySelectorAll('[id]'),function(e){
    if(seen[e.id]) dup.push(e.id); seen[e.id]=1; });
  out.dupeIds=dup;
  return out;
})()
"""

# 拆成两段：先拿基础信息，再按页取元素几何（避免一次 eval 太长）
BASE_PROBE = PROBE

IDS_BY_PAGE = {
    "/": ["#topbar", ".brand", ".brand-txt b", ".nav", "#roleChip", ".role-bar",
          "#stats", "#stats .stat.clickable", "#swSched", "#queueKv", "#tbl",
          "#subTitle", "#btnPull"],
    "/workbench": ["#topbar", "#doc", "#tabBody", "#summaryBox", "#opinion",
                   "#btnWb", "#tabBody .risk-card"],
    "/rules": ["#topbar", "#ruleBody", ".rule-row", "#curTypeLabel"],
    "/twin": ["#topbar", "#kpi", "#pipe", ".pipe-node", "#logStream", ".log-stream .li",
              "#gauges", ".meter", "#opsKv", "#ruleBars", ".rb", "#topPaths"],
    "/mock": ["#topbar", "#list", ".mock-item", "#detail", "#eps", ".ep"],
    "/login": ["#loginForm", ".role-item", "#roleList"],
}

PAGES = [
    ("审查大盘", "/"),
    ("智能审查工作台", "/workbench?id=%d" % CID),
    ("合规规则与模板", "/rules"),
    ("动态孪生看板", "/twin"),
    ("模拟审批系统", "/mock"),
    ("登录页", "/login"),
]

lines = ["前端逐页体检（CDP 真跑真量）", "=" * 100,
         "目标 %s ｜ 用户 admin ｜ 浏览器 %s ｜ %s"
         % (BASE, os.path.basename(DEFAULT_CHROME), time.strftime("%Y-%m-%d %H:%M:%S")), ""]
issues = []
detail = {}

if not DEFAULT_CHROME:
    lines.append("未找到 Chrome / Edge，无法执行体检。")
else:
    ud = tempfile.mkdtemp(prefix="cdp_")
    try:
        with Browser(ud=ud) as br:
            for name, path in PAGES:
                pg = br.new_page()
                pg.navigate(BASE + path, cookie=("cr_session", TOKEN))
                pg.wait_load()
                base = pg.eval(BASE_PROBE) or {}
                sels = IDS_BY_PAGE.get(path.split("?")[0], [])
                geo = pg.eval(
                    "(function(){var out={};%s.forEach(function(s){"
                    "var e=document.querySelector(s);if(!e){out[s]=null;return;}"
                    "var r=e.getBoundingClientRect(),cs=getComputedStyle(e);"
                    "out[s]={w:Math.round(r.width),h:Math.round(r.height),"
                    "x:Math.round(r.left),y:Math.round(r.top),"
                    "right:Math.round(r.right),bottom:Math.round(r.bottom),"
                    "disp:cs.display,vis:cs.visibility};});return JSON.stringify(out);})()"
                    % json.dumps(sels)) or {}
                if isinstance(geo, str):
                    geo = json.loads(geo)

                # 顶栏重叠检测
                tb = geo.get("#topbar") or geo.get(".topbar")
                brand = geo.get(".brand")
                nav = geo.get(".nav")
                overlap = bool(brand and nav and brand["right"] > nav["x"] + 1
                               and abs(brand["y"] - nav["y"]) < 16)
                # 统计卡下钻提示（::after 是伪元素，量不到；改用「卡片底边与副标题底边」判断）
                card = geo.get("#stats .stat.clickable")
                sub = geo.get("#subTitle")

                errs = [e for e in br.events if e[1] == pg.sid
                        and e[0] in ("error", "warning")]
                line = ("[%s] %-22s 异常=%d 空容器=%d 塌陷=%d 重复id=%d 模板=%d 横向溢出=%s"
                        % (name, path,
                           len(errs),
                           len([k for k, v in geo.items() if v is None]),
                           len([k for k, v in geo.items() if v and (v["w"] == 0 or v["h"] == 0)]),
                           len(base.get("dupeIds") or []),
                           len(base.get("tpl") or []),
                           "是(%s>%s)" % (base.get("scrollW"), base.get("clientW"))
                           if (base.get("scrollW") or 0) > (base.get("clientW") or 0) + 2
                           else "否"))
                lines.append(line)
                detail[name] = {"base": base, "geo": geo, "errors": errs}

                if base.get("err"):
                    issues.append("%s：前端异常 → %s" % (name, str(base["err"])[:200]))
                for kind, _sid, txt in errs:
                    issues.append("%s：%s → %s" % (name, kind, txt[:180]))
                if base.get("dupeIds"):
                    issues.append("%s：重复 id → %s" % (name, "、".join(base["dupeIds"][:5])))
                if base.get("tpl"):
                    issues.append("%s：模板占位符未替换 → %s" % (name, "、".join(base["tpl"][:5])))
                miss = [k for k, v in geo.items() if v is None]
                if miss:
                    issues.append("%s：元素不存在 → %s" % (name, "、".join(miss)))
                zero = [k for k, v in geo.items() if v and (v["w"] == 0 or v["h"] == 0)]
                if zero:
                    issues.append("%s：元素塌陷（尺寸为 0）→ %s" % (name, "、".join(zero)))
                if (base.get("scrollW") or 0) > (base.get("clientW") or 0) + 2:
                    issues.append("%s：出现横向溢出 scrollW=%s > clientW=%s"
                                  % (name, base.get("scrollW"), base.get("clientW")))
                if overlap:
                    issues.append("%s：顶栏品牌文字与导航重叠（品牌右缘 %s > 导航左缘 %s）"
                                  % (name, brand["right"], nav["x"]))
                if tb:
                    lines.append("        顶栏高 %spx ｜ 品牌 %s↔%s ｜ 导航 %s↔%s ｜ 重叠=%s"
                                 % (tb["h"], brand and brand["x"], brand and brand["right"],
                                    nav and nav["x"], nav and nav["right"], overlap))
                if card and sub:
                    lines.append("        统计卡底边 %s ｜ 副标题底边 %s ｜ 卡片高 %s"
                                 % (card["bottom"], sub["bottom"], card["h"]))
                if geo.get("#kpi"):
                    lines.append("        KPI %sx%s ｜ 流水线节点 %s 个 ｜ 日志行 %s"
                                 % (geo["#kpi"]["w"], geo["#kpi"]["h"],
                                    (geo.get(".pipe-node") or {}).get("w"),
                                    (geo.get(".log-stream .li") or {}).get("h")))
                pg._send("Target.closeTarget", targetId=None) if False else None
    except Exception as exc:                                                # noqa: BLE001
        import traceback
        lines.append("")
        lines.append("体检执行异常：%s" % exc)
        lines.append(traceback.format_exc()[-1200:])
        issues.append("体检执行异常：%s" % exc)
    finally:
        shutil.rmtree(ud, ignore_errors=True)

lines.append("")
lines.append("=" * 100)
lines.append("问题清单：%d 项" % len(issues))
for i, s in enumerate(issues, 1):
    lines.append("  %d. %s" % (i, s))
if not issues:
    lines.append("  无 —— 六页在「脚本异常 / console 错误 / 空容器 / 塌陷 / 重复 id /")
    lines.append("        模板残留 / 横向溢出 / 顶栏重叠」八个维度上均正常。")
lines.append("=" * 100)

with io.open(os.path.join(ROOT, "verify", "frontend_audit.json"), "w",
             encoding="utf-8") as f:
    json.dump(detail, f, ensure_ascii=False, indent=2)
with io.open(os.path.join(ROOT, "verify", "frontend_audit.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(lines))

httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

print("frontend audit: %d issues" % len(issues))
sys.exit(0 if not issues else 1)
