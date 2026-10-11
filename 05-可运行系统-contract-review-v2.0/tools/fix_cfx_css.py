# -*- coding: utf-8 -*-
"""全站卡片动效 + 修 .gauge 类名冲突

一、类名冲突（「还是乱的」的真正根因）
   app.css 里新加的水位框用了 `.gauge`，而 fx.css 里**旧的环形仪表**也叫 `.gauge`
   （`width:68px; height:68px; flex:none`）。fx.css 后加载 → 压过 app.css，
   于是水位框被压成 68×68 的小方块，`overflow: visible` 让里面的标签/数值/进度条/说明
   全部溢出，四个方块挤在两列里、右侧空一大片。
   → 新类改名 `.meter`（fx.css 的 `.gauge` 保持不动，它仍服务于环形仪表场景）。

二、卡片动效系统（克制、有节制、且不与定时刷新打架）
   三条容易做错的地方，这次都处理了：
   * **入场动画只播一次**：定时刷新的页面（孪生看板 4 秒一刷）若每次重播入场，
     画面会一直抖。所以按「容器」记一次性标记，重渲染不再播入场；
   * **"活"的感觉交给数据脉冲**：刷新时只让**真正变化的数值**跳一下，
     而不是整块重播动画 —— 这才是有信息量的动态；
   * **动画只负责运动**：终态一律由 CSS 静态规则决定，动画被禁用/不执行也不会不可见。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:52]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:52]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ================================================================ ① 类名冲突：.gauge → .meter
patch("ui/assets/app.css", [
    ("""/* ==========================================================================
   动态孪生看板（v2.0.1 · 全真实数据版）
   ========================================================================== */""",
     """/* ==========================================================================
   动态孪生看板（v2.0.1 · 全真实数据版）
   注意：这里的水位框类名是 **.meter** 而不是 .gauge —— fx.css 里的 `.gauge`
   是「环形仪表」（width:68px; height:68px），两者同名会被后者压成小方块导致内容溢出。
   ========================================================================== */"""),
    (""".gauge-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 640px) { .gauge-row { grid-template-columns: 1fr; } }
.gauge {
  background: var(--panel-3); border: 1px solid var(--line-2);
  border-radius: var(--radius-s); padding: 11px 13px;
}
.gauge .k { font-size: 12px; color: var(--sub); display: flex; justify-content: space-between; }
.gauge .k b { font-variant-numeric: tabular-nums; color: var(--ink); font-size: 13px; }
.gauge .bar { height: 6px; border-radius: 999px; background: var(--panel-3);
  overflow: hidden; margin-top: 8px; }
.gauge .bar i { display: block; height: 100%; background: var(--brand); transition: width .5s; }
.gauge.ok .bar i { background: var(--ok); }
.gauge.warn .bar i { background: var(--mid); }
.gauge.hl .bar i { background: var(--high); }
.gauge .sub { font-size: 11.5px; color: var(--faint); margin-top: 5px; }""",
     """.meter-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 640px) { .meter-row { grid-template-columns: 1fr; } }
.meter {
  background: var(--panel-3); border: 1px solid var(--line-2);
  border-radius: var(--radius-s); padding: 11px 13px;
}
.meter .k { font-size: 12px; color: var(--sub); display: flex; justify-content: space-between; gap: 8px; }
.meter .k b { font-variant-numeric: tabular-nums; color: var(--ink); font-size: 13px; }
.meter .bar { height: 6px; border-radius: 999px; background: var(--panel-3);
  overflow: hidden; margin-top: 8px; }
.meter .bar i { display: block; height: 100%; background: var(--brand); transition: width .5s; }
.meter.ok .bar i { background: var(--ok); }
.meter.warn .bar i { background: var(--mid); }
.meter.hl .bar i { background: var(--high); }
.meter .sub { font-size: 11.5px; color: var(--faint); margin-top: 5px; }"""),
])

patch("ui/assets/app.css", [
    (""".gauge .bar i { min-width: 2px; }
.gauge.idle .bar i { background: var(--faint); }
.gauge.idle .bar { background: var(--panel-3); }
.gauge.idle .k b { color: var(--sub); }""",
     """.meter .bar i { min-width: 2px; }
.meter.idle .bar i { background: var(--faint); }
.meter.idle .bar { background: var(--panel-3); }
.meter.idle .k b { color: var(--sub); }"""),
    (""".gauge-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 640px) { .gauge-row { grid-template-columns: 1fr; } }""",
     """.meter-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 640px) { .meter-row { grid-template-columns: 1fr; } }"""),
])

patch("ui/twin.html", [
    ('<div class="gauge-row" id="gauges"></div>', '<div class="meter-row" id="gauges"></div>'),
    ("""function gauge(k, v, max, unit, sub, cls) {
  const pct = max > 0 ? Math.min(100, v / max * 100) : 0;
  return '<div class="gauge ' + (cls || '') + '">' +
    '<div class="k"><span>' + CR.esc(k) + '</span><b>' + v + (unit || '') + '</b></div>' +
    '<div class="bar"><i style="width:' + Math.max(1.5, pct) + '%"></i></div>' +
    '<div class="sub">' + CR.esc(sub || '') + '</div></div>';
}""",
     """function gauge(k, v, max, unit, sub, cls) {
  const pct = max > 0 ? Math.min(100, v / max * 100) : 0;
  return '<div class="meter ' + (cls || '') + '">' +
    '<div class="k"><span>' + CR.esc(k) + '</span><b>' + v + (unit || '') + '</b></div>' +
    '<div class="bar"><i style="width:' + Math.max(1.5, pct) + '%"></i></div>' +
    '<div class="sub">' + CR.esc(sub || '') + '</div></div>';
}"""),
])

# ================================================================ ② 卡片动效：CSS
p = os.path.join(ROOT, "ui/assets/fx.css")
with io.open(p, "r", encoding="utf-8") as f:
    fx = f.read()

CFX_CSS = """

/* ==========================================================================
   卡片动效系统（v2.1.3）
   --------------------------------------------------------------------------
   设计口径（三条，都是踩过坑才定下来的）：
     1) **入场只播一次**：定时刷新的页面若每次重渲染都重播入场，画面会一直抖。
        所以入场动画由 JS 按「容器」打一次性标记，重渲染不再播；
     2) **"活"的感觉交给数据脉冲**：刷新时只让真正变化的数值跳一下
        （.cfx-tick），而不是整块重播动画 —— 这才是有信息量的动态；
     3) **动画只负责运动，不负责可见性**：所有终态都由静态 CSS 决定，
        动画被禁用或未执行时元素依然完整可见，不会出现"永久隐形"。
   强度刻意压低：位移 ≤3px、时长 0.3~0.9s、光泽透明度 .055，
   符合控制台观感（克制的高级感），不做弹跳与颜色闪烁。
   ========================================================================== */

/* ---------- 基础：悬停光泽扫过 ---------- */
.cfx {
  position: relative;
  background-image: linear-gradient(105deg,
    transparent 42%, rgba(255, 255, 255, .055) 50%, transparent 58%);
  background-size: 240% 100%;
  background-position: 135% 0;
  background-repeat: no-repeat;
  transition: transform .34s cubic-bezier(.22, .68, .36, 1),
              border-color .34s,
              box-shadow .34s,
              background-position .95s cubic-bezier(.22, .4, .2, 1);
}
html[data-theme="light"] .cfx {
  background-image: linear-gradient(105deg,
    transparent 42%, rgba(20, 40, 90, .05) 50%, transparent 58%);
}
.cfx:hover {
  border-color: rgba(61, 139, 253, .42);
  box-shadow: var(--glow);
  background-position: -35% 0;
}
/* 小卡片才做上浮；大面板只提亮边框（大块位移在栅格里很晃眼） */
.cfx-lift:hover { transform: translateY(-3px); }
.cfx-lift:active { transform: translateY(-1px) scale(.998); }

/* ---------- 入场（错峰） ---------- */
@keyframes cfxIn {
  from { opacity: 0; transform: translateY(13px) scale(.988); }
  to { opacity: 1; transform: none; }
}
.cfx-enter {
  animation: cfxIn .52s cubic-bezier(.22, .68, .36, 1) both;
  animation-delay: calc(var(--cfx-i, 0) * 32ms);
}

/* ---------- 强调条流光（沿用各类卡片已有的 ::before） ---------- */
@keyframes cfxBeam { 0% { background-position: -70% 0; } 100% { background-position: 170% 0; } }
.cfx-beam::before {
  background-size: 220% 100% !important;
  animation: cfxBeam 5.4s linear infinite;
}

/* ---------- 数据变化脉冲 ---------- */
@keyframes cfxTick {
  0% { transform: none; }
  30% { transform: translateY(-2px); color: var(--cyan); }
  100% { transform: none; }
}
.cfx-tick { animation: cfxTick .52s ease-out; display: inline-block; }

/* ---------- 表格行悬停 ---------- */
.grid tbody tr { transition: background .2s; }
.grid tbody tr:hover { background: var(--panel-3); }

/* ---------- 降级：必须写在本文件最后 ---------- */
@media (prefers-reduced-motion: reduce) {
  .cfx, .cfx:hover { transition: none !important; background-image: none !important; }
  .cfx-enter, .cfx-tick, .cfx-beam::before { animation: none !important; }
  .cfx-lift:hover, .cfx-lift:active { transform: none !important; }
}
"""
if "卡片动效系统（v2.1.3）" not in fx:
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(fx + CFX_CSS)
    log.append("  ok   ui/assets/fx.css :: 追加卡片动效系统")
else:
    log.append("  skip ui/assets/fx.css")

with io.open(os.path.join(ROOT, "verify", "cfx_css.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("cfx css done")
