#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统计卡金额溢出 / 竖屏适配诊断探针（只读，不改业务状态）。

为什么要它：`--dump-dom` 拿到的只是 HTML 结构，量不出「数字被容器裁掉」
「窄屏整页横向滚动」这类只有真正渲染才暴露的问题。这里用无头浏览器在多种
视口下逐元素量 scrollWidth / clientWidth / 边界越界，把问题量化成清单。

用法：
    python tools/probe_overflow.py before
    python tools/probe_overflow.py after

产出：
    verify/overflow_<tag>.json   机器可读
    verify/overflow_<tag>.txt    人读摘要
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
TOOLS = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(ROOT)

import cdp  # noqa: E402

PORT = 8799
BASE = "http://127.0.0.1:%d" % PORT
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SHOTS = "--shots" in sys.argv
SHOT_VIEWPORTS = ("桌面 1600", "手机竖屏 430", "手机竖屏 390")

# 视口：(宽, 高, 是否模拟移动端)。竖屏手机取主流机型逻辑像素。
VIEWPORTS = [
    (1600, 1000, False, "桌面 1600"),
    (1366, 900, False, "笔记本 1366"),
    (1024, 1366, True, "竖屏平板 1024"),
    (820, 1180, True, "竖屏平板 820"),
    (430, 932, True, "手机竖屏 430"),
    (390, 844, True, "手机竖屏 390"),
    (360, 800, True, "手机竖屏 360"),
]

# 页面：(标签, 路径, 注入模式)
PAGES = [
    ("大盘", "/", ""),
    ("大盘·金额最大档", "/", "brief"),
    ("大盘·超长文本兜底", "/", "extreme"),
    ("工作台", "/workbench?id=1", ""),
    ("孪生看板", "/twin", ""),
    ("规则与模板", "/rules", ""),
]

PROBE = r"""
(function () {
  function sel(el) {
    var parts = [], n = el, g = 0;
    while (n && n.nodeType === 1 && g++ < 4) {
      var s = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(s + '#' + n.id); break; }
      var cls = (typeof n.className === 'string' ? n.className : '')
        .trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s);
      n = n.parentElement;
    }
    return parts.join('>');
  }
  function txt(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 46); }

  var de = document.documentElement;
  var res = {
    vw: window.innerWidth, vh: window.innerHeight,
    pageOverflow: Math.max(0, de.scrollWidth - de.clientWidth),
    clipped: [], ellipsis: [], escaped: [], hscroll: [], stats: []
  };
  // 预期内的截断 / 定位，不算缺陷：
  //   .pet-tip  宠物气泡，按设计悬在按钮外侧
  //   .sr-only  无障碍隐藏文本
  var WHITE = ['pet-tip', 'sr-only'];

  var els = document.body.querySelectorAll('*');
  for (var i = 0; i < els.length; i++) {
    var el = els[i], cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    var wl = false;
    for (var w = 0; w < WHITE.length; w++) {
      if (el.className && String(el.className).indexOf(WHITE[w]) >= 0) wl = true;
    }
    if (wl) continue;
    var r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) continue;
    var ox = cs.overflowX;
    if ((ox === 'hidden' || ox === 'clip') && el.clientWidth > 0) {
      var d = el.scrollWidth - el.clientWidth;
      if (d > 2) {
        // 带省略号的截断是设计内行为（有 title/ellipsis 兜底），与被硬切掉的
        // 内容（用户彻底看不到）性质不同，分开归类。
        if (cs.textOverflow === 'ellipsis') {
          res.ellipsis.push({ sel: sel(el), cw: el.clientWidth, d: d, t: txt(el) });
        } else {
          res.clipped.push({ sel: sel(el), cw: el.clientWidth,
            sw: el.scrollWidth, d: d, t: txt(el) });
        }
      }
    }
    var p = el.parentElement;
    if (p && cs.position !== 'fixed') {
      var pr = p.getBoundingClientRect();
      if (pr.width > 1) {
        var over = Math.max(r.right - pr.right, pr.left - r.left);
        if (over > 3 && (el.children.length === 0)) {
          // 祖先里若有 overflow-x:auto/scroll（如手机端 .nav），越界只是「需要横滑」，
          // 不是布局事故 —— 分开归类，否则真问题会被这类噪音淹掉。
          var n = p, g = 0, scrollable = false;
          while (n && g++ < 6) {
            var s = getComputedStyle(n);
            if (s.overflowX === 'auto' || s.overflowX === 'scroll') { scrollable = true; break; }
            n = n.parentElement;
          }
          var rec = { sel: sel(el), d: Math.round(over), t: txt(el) };
          if (scrollable) res.hscroll.push(rec); else res.escaped.push(rec);
        }
      }
    }
  }

  var stats = document.querySelectorAll('.stat');
  for (var j = 0; j < stats.length; j++) {
    var s = stats[j];
    var v = s.querySelector('.v'), k = s.querySelector('.k');
    res.stats.push({
      k: k ? txt(k) : '',
      v: v ? txt(v) : '',
      cardW: Math.round(s.getBoundingClientRect().width),
      vClient: v ? v.clientWidth : 0,
      vScroll: v ? v.scrollWidth : 0,
      vH: v ? Math.round(v.getBoundingClientRect().height) : 0,
      clipped: v ? Math.max(0, v.scrollWidth - v.clientWidth) : 0
    });
  }
  return JSON.stringify(res);
})()
"""

INJECT_BRIEF = r"""
(function () {
  var vs = document.querySelectorAll('.stat.amt .v'), n = 0;
  for (var i = 0; i < vs.length; i++) {
    // 走到 CR.moneyBrief 的最长档：9999.99 亿
    vs[i].innerHTML = CR.moneyBrief(999999999999);
    n++;
  }
  if (CR.fitStats) CR.fitStats();
  return n;
})()
"""

INJECT_EXTREME = r"""
(function () {
  var vs = document.querySelectorAll('.stat.amt .v'), n = 0;
  for (var i = 0; i < vs.length; i++) {
    // 绕过格式化，直接塞 27 字符的原始金额，验证 fitStats() 兜底是否真的生效
    vs[i].innerHTML = '\u00a5 1,234,567,890.00 <span class="faint">\uff08123456.8 \u4e07\uff09</span>';
    n++;
  }
  if (CR.fitStats) CR.fitStats();
  return n;
})()
"""

INJECTORS = {"brief": INJECT_BRIEF, "extreme": INJECT_EXTREME}


def boot(port=PORT):
    from app import config as cfg_mod
    from app import server as server_mod
    from app import store as store_mod
    from mock import feishu

    cfg = cfg_mod.Config(ROOT)
    cfg.data.setdefault("server", {})
    cfg.data["server"]["host"] = "127.0.0.1"
    cfg.data["server"]["port"] = port
    for key in ("data", "uploads", "parsed", "reports", "samples"):
        cfg.path(key)
    st = store_mod.Store(cfg.db_path())
    feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
                cfg.path("samples"))
    httpd = server_mod.serve(cfg, st)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.8)
    return httpd


def login_token(role="admin"):
    req = urllib.request.Request(
        BASE + "/api/auth/switch", method="POST",
        data=json.dumps({"role": role}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=20) as r:
        raw = r.headers.get("Set-Cookie") or ""
        body = json.loads(r.read().decode("utf-8"))
    m = [c for c in raw.split(";") if c.strip().startswith("cr_session=")]
    if m:
        return m[0].split("=", 1)[1].strip()
    return body.get("token") or ""


def main():
    tag = (sys.argv[1] if len(sys.argv) > 1 else "run").strip() or "run"
    report = {"tag": tag, "port": PORT, "viewports": [], "pages": {}}
    notes = []
    httpd = boot()
    token = login_token("admin")
    notes.append("登录令牌获取：%s" % ("成功" if token else "失败"))

    ud = tempfile.mkdtemp(prefix="hj_probe_")
    br = cdp.Browser(chrome=cdp.DEFAULT_CHROME, ud=ud, headless=True).start()
    try:
        for plabel, path, inject in PAGES:
            pg = br.new_page()
            url = BASE + path
            pg.navigate(url, cookie=("cr_session", token) if token else None)
            pg.wait_load()
            report["pages"].setdefault(plabel, {})
            if inject:
                n = pg.eval(INJECTORS[inject])
                notes.append("注入 %s（%s）：替换 %s 张金额卡" % (inject, plabel, n))
                pg.br.pump(0.5)
            for w, h, mob, vlabel in VIEWPORTS:
                try:
                    pg._send("Emulation.setDeviceMetricsOverride",
                             width=w, height=h, deviceScaleFactor=1, mobile=mob)
                except IOError as e:
                    notes.append("视口设置失败 %s %s：%s" % (plabel, vlabel, e))
                    continue
                pg.br.pump(0.45)
                raw = pg.eval(PROBE)
                if isinstance(raw, str):
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        data = {"_parse_error": raw[:200]}
                else:
                    data = {"_eval_error": str(raw)[:200]}
                data["viewport"] = vlabel
                report["pages"][plabel][vlabel] = data
                if SHOTS and plabel == "大盘·金额最大档" and vlabel in SHOT_VIEWPORTS:
                    shot_dir = os.path.join(ROOT, "verify", "shots")
                    os.makedirs(shot_dir, exist_ok=True)
                    fn = os.path.join(shot_dir, "%s_%s.png" % (tag, vlabel.replace(" ", "")))
                    try:
                        pg.screenshot(fn, width=w, height=h)
                        notes.append("截图 %s" % os.path.relpath(fn, ROOT))
                    except Exception as e:  # noqa: BLE001
                        notes.append("截图失败 %s：%s" % (vlabel, e))
            errs = [e for e in br.events if e[1] == pg.sid and e[0] == "error"]
            if errs:
                notes.append("%s 页面脚本报错 %d 条：%s" % (plabel, len(errs), errs[0][2][:120]))
            pg._send("Page.close") if False else None
    finally:
        br.close()
        try:
            httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(ud, ignore_errors=True)

    report["notes"] = notes

    os.makedirs(os.path.join(ROOT, "verify"), exist_ok=True)
    jp = os.path.join(ROOT, "verify", "overflow_%s.json" % tag)
    with io.open(jp, "w", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=1))

    # -------- 人读摘要
    L = []
    L.append("=" * 78)
    L.append("统计卡金额溢出 / 竖屏适配诊断 —— tag=%s" % tag)
    L.append("=" * 78)
    for n in notes:
        L.append("· " + n)
    L.append("")
    bad_total = 0
    for plabel, _, _ in PAGES:
        vs = report["pages"].get(plabel) or {}
        L.append("【%s】" % plabel)
        for w, h, mob, vlabel in VIEWPORTS:
            d = vs.get(vlabel)
            if not d:
                L.append("  %-14s  (无数据)" % vlabel)
                continue
            page_of = d.get("pageOverflow", -1)
            clipped = d.get("clipped") or []
            ell = d.get("ellipsis") or []
            esc = d.get("escaped") or []
            hs = d.get("hscroll") or []
            stat_clip = [s for s in (d.get("stats") or []) if s.get("clipped", 0) > 2]
            flag = "OK " if (page_of <= 1 and not clipped and not stat_clip) else "!! "
            if flag == "!! ":
                bad_total += 1
            L.append("  %s%-14s 页面横向溢出=%dpx  硬裁=%d  越界=%d  金额卡被裁=%d  （省略号截断=%d 需横滑=%d）"
                     % (flag, vlabel, page_of, len(clipped), len(esc), len(stat_clip),
                        len(ell), len(hs)))
            for s in stat_clip:
                L.append("        · 卡片[%s] 文本=%r 卡宽=%d 内容宽=%d 溢出=%dpx"
                         % (s["k"], s["v"], s["cardW"], s["vScroll"], s["clipped"]))
            for c in clipped[:6]:
                L.append("        · 被裁 %s  %s(+%dpx)  %r"
                         % (c["sel"], c["cw"], c["d"], c["t"]))
            for e in esc[:4]:
                L.append("        · 越界 %s  +%dpx  %r" % (e["sel"], e["d"], e["t"]))
        L.append("")

    # 桌面视口下的 7 张卡明细（看金额卡到底多宽）
    desk = (report["pages"].get("大盘·金额最大档") or {}).get("桌面 1600") or {}
    if desk.get("stats"):
        L.append("【金额最大档 · 桌面 1600 统计卡实测】")
        for s in desk["stats"]:
            L.append("  %-8s 卡宽=%4d 内容宽=%5d 溢出=%3d  %s"
                     % (s["k"], s["cardW"], s["vScroll"], s["clipped"], s["v"]))
        L.append("")

    L.append("异常视口组合数：%d" % bad_total)
    tp = os.path.join(ROOT, "verify", "overflow_%s.txt" % tag)
    with io.open(tp, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("JSON: %s" % jp)
    print("TXT : %s" % tp)
    print("异常视口组合数：%d" % bad_total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
