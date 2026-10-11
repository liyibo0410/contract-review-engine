#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前端深检（CDP 真跑真量 · 第二批维度）。

`tools/audit_frontend.py` 查的是「结构性问题」——脚本异常、空容器、塌陷、
重复 id、模板残留、横向溢出、顶栏重叠。那八个维度都「不会崩」，所以它们全绿
不等于「细节没问题」。本工具补的是**只有量计算才能发现的那批细节**：

  1. 文本对比度（WCAG 2.1 AA：正文 ≥ 4.5:1、大字 ≥ 3:1）
  2. 可点击元素的**触控目标尺寸**（WCAG 2.5.8 AA ≥ 24px，最佳实践 44px）
  3. 文本被容器截断但**没有 title/aria-label** 兜底 —— 用户永远看不到全文
  4. 图片缺 `alt`、表单控件无可访问名称、链接无可访问名称
  5. 占位链接（`href="#"` / `javascript:void(0)`）与正数 `tabindex`（反模式）
  6. 元素越出视口（左右任一方向），排除可滚动容器内的正常溢出
  7. 字号 < 11px 的正文（可读性）
  8. 样式卫生：`!important` 计数、内联 style 计数
  9. `<meta name="viewport">` 是否禁用了缩放（`user-scalable=no`）

三档视口（1600 / 1280 / 390）逐页跑，共 6 页。

产物：verify/frontend_deep.txt（明细）+ verify/frontend_deep.json（结构化）
用法：python tools/audit_frontend_deep.py
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
from tools.cdp import Browser, DEFAULT_CHROME                                          # noqa: E402

PORT = 8799
TMP = tempfile.mkdtemp(prefix="cr_deep_")
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
    COOKIE = (r.headers.get("Set-Cookie") or "").split(";")[0]
TOKEN = COOKIE.split("=", 1)[1] if "=" in COOKIE else ""

VIEWPORTS = [(1600, 1000), (1280, 900), (390, 844)]
PAGES = [
    ("审查大盘", "/"),
    ("智能审查工作台", "/workbench?id=%d" % CID),
    ("合规规则与模板", "/rules"),
    ("动态孪生看板", "/twin"),
    ("模拟审批系统", "/mock"),
    ("登录页", "/login"),
]

# ------------------------------------------------------------------ 探针 JS
PROBE = r"""
(function(){
  function vis(e){var cs=getComputedStyle(e);if(cs.display==='none'||cs.visibility==='hidden'||
      parseFloat(cs.opacity)===0)return false;var r=e.getBoundingClientRect();
      return r.width>0&&r.height>0;}
  function rgbs(s){var m=/rgba?\(([^)]+)\)/.exec(s||'');if(!m)return null;
      var p=m[1].split(',').map(function(x){return parseFloat(x)});
      return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1};}
  function lum(c){function f(v){v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);}
      return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b);}
  function ratio(a,b){var l1=lum(a),l2=lum(b);if(l1<l2){var t=l1;l1=l2;l2=t;}
      return (l1+0.05)/(l2+0.05);}
  // 有效背景：向上找第一个不透明底色；若中途遇到渐变/图片背景则判为「不可判定」
  function bgOf(el){
    var e=el;
    while(e&&e!==document.documentElement){
      var cs=getComputedStyle(e);
      if(cs.backgroundImage&&cs.backgroundImage!=='none') return null;
      var c=rgbs(cs.backgroundColor);
      if(c&&c.a>0.9) return c;
      e=e.parentElement;
    }
    var b=rgbs(getComputedStyle(document.body).backgroundColor);
    return b&&b.a>0.9? b : null;
  }
  function path(e){
    var s=e.tagName.toLowerCase();
    if(e.id) s+='#'+e.id;
    else if(e.classList&&e.classList.length) s+='.'+e.classList[0];
    return s;
  }
  function directText(e){
    var t='';
    for(var i=0;i<e.childNodes.length;i++){
      var n=e.childNodes[i]; if(n.nodeType===3) t+=n.nodeValue;
    }
    return t.replace(/\s+/g,' ').trim();
  }
  var out={vw:innerWidth,vh:innerHeight,path:location.pathname+location.search,
           title:document.title,contrast:[],small:[],targets:[],trunc:[],
           noalt:[],nolabel:[],noname:[],placeholder:[],tabindex:[],
           overflow:[],important:0,inline:0,viewportMeta:''};

  var vm=document.querySelector('meta[name=viewport]');
  out.viewportMeta=vm?vm.getAttribute('content')||'':'';

  var all=document.querySelectorAll('*');
  for(var i=0;i<all.length;i++){
    var e=all[i]; if(!vis(e)) continue;
    var cs=getComputedStyle(e);

    // ---- 对比度（只测承载直接文本的元素）
    var txt=directText(e);
    if(txt){
      var size=parseFloat(cs.fontSize)||0;
      var bold=(parseInt(cs.fontWeight,10)||400)>=700;
      var large=(size>=24)||(bold&&size>=18.66);
      var fg=rgbs(cs.color), bg=bgOf(e);
      if(fg&&bg){
        var cr=ratio(fg,bg);
        var need=large?3.0:4.5;
        if(cr<need) out.contrast.push({sel:path(e),ratio:Math.round(cr*100)/100,
            need:need,size:Math.round(size*10)/10,text:txt.slice(0,26)});
      }
      if(size>0&&size<11){out.small.push({sel:path(e),size:Math.round(size*10)/10,
          text:txt.slice(0,26)});}
    }

    // ---- 文本截断但无兜底
    if(txt&&e.children.length===0){
      var cut=e.scrollWidth>e.clientWidth+1;
      var clipped=(cs.overflowX==='hidden'||cs.overflow==='hidden'||
                   cs.textOverflow==='ellipsis');
      if(cut&&clipped&&!e.getAttribute('title')&&!e.getAttribute('aria-label')){
        out.trunc.push({sel:path(e),sw:e.scrollWidth,cw:e.clientWidth,
            text:txt.slice(0,26)});
      }
    }

    // ---- 样式卫生
    if(e.getAttribute&&e.getAttribute('style')) out.inline++;

    // ---- 正数 tabindex
    var ti=e.getAttribute&&e.getAttribute('tabindex');
    if(ti!==null&&parseInt(ti,10)>0) out.tabindex.push({sel:path(e),ti:ti});

    // ---- 越出视口左右边界
    var r=e.getBoundingClientRect();
    if(r.width>0&&(r.left<-2||r.right>innerWidth+2)){
      // 装饰层（光斑 / 扫描线）故意溢出视口，不算缺陷
      var deco=e.closest('.glow,.scanline,[aria-hidden="true"]');
      var p=e.parentElement,scrollable=false;
      while(p&&p!==document.body){
        var ps=getComputedStyle(p);
        if(ps.overflowX==='auto'||ps.overflowX==='scroll'){scrollable=true;break;}
        p=p.parentElement;
      }
      if(!scrollable&&!deco) out.overflow.push({sel:path(e),left:Math.round(r.left),
          right:Math.round(r.right),vw:innerWidth});
    }
  }

  // ---- 触控目标（WCAG 2.5.8 AA：24×24，或「间距例外」——四周 24×24 内无其他目标）
  //      另按规范豁免「行内目标」：位于句子中、尺寸受行高约束的链接/按钮不算违规。
  var clickable=document.querySelectorAll('a,button,[role=button],[onclick],'+
      'input[type=checkbox],input[type=radio],.stat.clickable');
  var boxes=[];
  for(var j=0;j<clickable.length;j++){
    var c=clickable[j]; if(!vis(c)) continue;
    var csr=getComputedStyle(c);
    if(csr.display==='inline') continue;                 // 行内目标：规范豁免
    var rr=c.getBoundingClientRect();
    boxes.push({el:c,r:rr,disp:csr.display});
  }
  function hit(a,b){return !(a.right<b.left||a.left>b.right||a.bottom<b.top||a.top>b.bottom);}
  for(var q=0;q<boxes.length;q++){
    var bx=boxes[q];
    var w=Math.round(bx.r.width),h=Math.round(bx.r.height);
    if(w>=24&&h>=24) continue;
    // 间距例外：以中心为原点扩成 24×24，若与任何其他目标相交，则不算合格
    var cx=bx.r.left+bx.r.width/2, cy=bx.r.top+bx.r.height/2;
    var pad={left:cx-12,top:cy-12,right:cx+12,bottom:cy+12};
    var clash=false;
    for(var z=0;z<boxes.length;z++){
      if(boxes[z]===bx) continue;
      // 祖先/后代不算冲突：可点击的行包住其中的复选框时，真正的目标是整行
      if(boxes[z].el.contains(bx.el)||bx.el.contains(boxes[z].el)) continue;
      if(hit(boxes[z].r,pad)){clash=true;break;}
    }
    if(clash){out.targets.push({sel:path(bx.el),w:w,h:h,
        text:(bx.el.textContent||'').replace(/\s+/g,' ').trim().slice(0,22)});}
  }

  // ---- 图片 alt
  var imgs=document.querySelectorAll('img');
  for(var k=0;k<imgs.length;k++){
    var im=imgs[k]; if(!vis(im)) continue;
    var a=im.getAttribute('alt');
    if(a===null||a.trim()===''){out.noalt.push({sel:path(im),
        src:(im.getAttribute('src')||'').slice(0,40)});}
  }

  // ---- 表单控件可访问名称
  var flds=document.querySelectorAll('input,select,textarea');
  for(var m=0;m<flds.length;m++){
    var f=flds[m]; if(!vis(f)) continue;
    if(f.type==='hidden') continue;
    var lab='';
    if(f.id){var l=document.querySelector('label[for="'+f.id+'"]');if(l)lab=l.textContent;}
    if(!lab&&f.closest('label')) lab=f.closest('label').textContent;
    if(!lab) lab=f.getAttribute('aria-label')||f.getAttribute('placeholder')||
        f.getAttribute('title')||'';
    if(!String(lab).trim()){out.nolabel.push({sel:path(f),type:f.type||f.tagName});}
  }

  // ---- 链接可访问名称 / 占位链接
  var as=document.querySelectorAll('a');
  for(var n=0;n<as.length;n++){
    var an=as[n]; if(!vis(an)) continue;
    var nm=(an.textContent||'').trim()||an.getAttribute('aria-label')||
        an.getAttribute('title')||'';
    if(!nm) out.noname.push({sel:path(an),href:(an.getAttribute('href')||'').slice(0,32)});
    var hr=an.getAttribute('href');
    if(hr===null||hr===''||hr==='#'||/^javascript:/i.test(hr)){
      out.placeholder.push({sel:path(an),href:String(hr).slice(0,32),
          text:(an.textContent||'').trim().slice(0,22)});
    }
  }

  // ---- !important 计数（走样式表）
  try{
    for(var s=0;s<document.styleSheets.length;s++){
      var rules=null;
      try{rules=document.styleSheets[s].cssRules;}catch(err){continue;}
      if(!rules) continue;
      for(var t=0;t<rules.length;t++){
        var cs2=rules[t].cssText||'';
        if(cs2.indexOf('!important')>=0){
          out.important+=(cs2.match(/!important/g)||[]).length;
        }
      }
    }
  }catch(e2){}

  return JSON.stringify(out);
})()
"""

lines = ["前端深检（CDP 真跑真量 · 对比度/触控/截断/可访问性/越界/样式卫生）", "=" * 104,
         "目标 %s ｜ 用户 admin ｜ 浏览器 %s ｜ %s"
         % (BASE, os.path.basename(DEFAULT_CHROME or "?"), time.strftime("%Y-%m-%d %H:%M:%S")),
         "视口 %s" % "、".join("%d×%d" % v for v in VIEWPORTS), ""]
issues = []
detail = {}
CATS = [
    ("contrast", "对比度不足", "文本对比度低于 WCAG AA 阈值"),
    ("targets", "触控目标过小", "可点击区域小于 24×24"),
    ("trunc", "文本被截断且无兜底", "看不到全文，也没有 title/aria-label"),
    ("noalt", "图片缺 alt", "读屏软件无法描述"),
    ("nolabel", "表单控件无可访问名称", "读屏软件只念「编辑框」"),
    ("noname", "链接无可访问名称", "读屏软件只念「链接」"),
    ("placeholder", "占位链接", "href 为空或 # / javascript:"),
    ("tabindex", "正数 tabindex", "打乱自然 Tab 顺序"),
    ("overflow", "元素越出视口", "左右任一方向超出且不在可滚动容器内"),
    ("small", "字号过小", "正文小于 11px"),
]

if not DEFAULT_CHROME:
    lines.append("未找到 Chrome / Edge，无法执行深检。")
else:
    ud = tempfile.mkdtemp(prefix="cdp_")
    try:
        with Browser(ud=ud) as br:
            for vw, vh in VIEWPORTS:
                lines.append("-" * 104)
                lines.append("视口 %d×%d" % (vw, vh))
                for name, path in PAGES:
                    pg = br.new_page()
                    pg._send("Emulation.setDeviceMetricsOverride", width=vw, height=vh,
                             deviceScaleFactor=1, mobile=vw < 700)
                    pg.navigate(BASE + path, cookie=("cr_session", TOKEN))
                    pg.wait_load()
                    time.sleep(0.5)
                    raw = pg.eval(PROBE)
                    d = json.loads(raw) if isinstance(raw, str) else (raw or {})

                    key = "%s@%d" % (name, vw)
                    detail[key] = d
                    counts = {k: len(d.get(k) or []) for k, _, _ in CATS}
                    bad = sum(counts.values())
                    lines.append("  [%s] %-18s 问题 %-3d ｜ 对比度 %d ｜ 触控 %d ｜ 截断 %d ｜ "
                                 "alt %d ｜ 标签 %d ｜ 链接名 %d ｜ 占位链 %d ｜ "
                                 "tabindex %d ｜ 越界 %d ｜ 小字 %d"
                                 % ("OK " if bad == 0 else "NG ",
                                    name, bad, counts["contrast"], counts["targets"],
                                    counts["trunc"], counts["noalt"], counts["nolabel"],
                                    counts["noname"], counts["placeholder"],
                                    counts["tabindex"], counts["overflow"], counts["small"]))
                    if vw == 390:
                        lines.append("        样式卫生：!important %d 处 ｜ 内联 style %d 处 ｜ "
                                     "viewport meta = %s"
                                     % (d.get("important", 0), d.get("inline", 0),
                                        d.get("viewportMeta") or "(无)"))

                    for k, label, why in CATS:
                        items = d.get(k) or []
                        if not items:
                            continue
                        lines.append("        · %s ×%d（%s）" % (label, len(items), why))
                        for it in items[:6]:
                            lines.append("            - %s" % json.dumps(it, ensure_ascii=False))
                        if len(items) > 6:
                            lines.append("            … 另有 %d 项，见 JSON" % (len(items) - 6))
                        if k == "targets" and vw >= 1000:
                            issues.append("%s@%d：触控目标过小 ×%d（桌面端非阻断，窄屏需复核）"
                                          % (name, vw, len(items)))
                        else:
                            issues.append("%s@%d：%s ×%d" % (name, vw, label, len(items)))

                    # 缩放的禁用是硬问题，单独提
                    meta = (d.get("viewportMeta") or "").lower()
                    if "user-scalable=no" in meta or "maximum-scale=1" in meta.replace(" ", ""):
                        issues.append("%s@%d：viewport 禁用了缩放（可访问性问题）" % (name, vw))
    except Exception as exc:                                                # noqa: BLE001
        import traceback
        lines.append("")
        lines.append("深检执行异常：%s" % exc)
        lines.append(traceback.format_exc()[-1200:])
        issues.append("深检执行异常：%s" % exc)
    finally:
        shutil.rmtree(ud, ignore_errors=True)

lines.append("")
lines.append("=" * 104)
lines.append("问题清单：%d 项" % len(issues))
for i, s in enumerate(issues, 1):
    lines.append("  %d. %s" % (i, s))
if not issues:
    lines.append("  无 —— 六页 × 三视口在「对比度 / 触控目标 / 截断 / alt / 表单标签 /")
    lines.append("        链接名称 / 占位链接 / tabindex / 越界 / 小字」十个维度上均正常。")
lines.append("=" * 104)

os.makedirs(os.path.join(ROOT, "verify"), exist_ok=True)
with io.open(os.path.join(ROOT, "verify", "frontend_deep.json"), "w", encoding="utf-8") as f:
    json.dump(detail, f, ensure_ascii=False, indent=2)
with io.open(os.path.join(ROOT, "verify", "frontend_deep.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

httpd.shutdown()
httpd.server_close()
shutil.rmtree(TMP, ignore_errors=True)

print("frontend deep audit: %d issues" % len(issues))
sys.exit(0 if not issues else 1)
