# -*- coding: utf-8 -*-
"""卡片动效 · JS 层

关键设计（都是为了不和定时刷新打架）：
  * 入场动画由 JS 按**容器**打一次性标记，重渲染不再播 —— 否则孪生看板 4 秒一刷
    会一直重播入场，画面持续抖动；
  * 悬停/光泽等常驻效果写在 CSS 的 :is(...) 选择器上（不依赖 JS 打过标记），
    所以刷新后新建的 DOM 节点自动继承效果；
  * 数据变化时才给数值加 .cfx-tick 跳一下 —— 这才是有信息量的"动态"；
    比对表存在 JS 变量里（跨渲染保留），不放在 DOM 上（会被 innerHTML 冲掉）。
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


# ================================================================ app.js 动效模块
patch("ui/assets/app.js", [
    ("""  /* ------------------------------------------------------------ v2.0 角色与权限""",
     """  /* ------------------------------------------------------------ v2.1.3 卡片动效
     入场错峰只播一次（按容器记忆），常驻效果交给 CSS 的 :is(...) 选择器 ——
     这样定时刷新重建的节点仍带悬停效果，但不会重播入场动画。 */
  const CFX_ENTER_SEL = [
    '.panel', '.card', '.stat', '.kpi', '.tk', '.kv-cell', '.risk-card',
    '.mode-col', '.mock-item', '.mock-kv .cell', '.mock-comment',
    '.login-card', '.role-item', '.deny-card', '.deny-cell',
    '.meter', '.pipe-node', '.sec-item', '.model-card', '.store-card'
  ].join(',');
  const CFX_PREV = {};

  function cfxEnter(scope) {
    const s = scope || document;
    if (s.__cfx) { return 0; }          // 同一容器只播一次
    s.__cfx = 1;
    const cards = $$(CFX_ENTER_SEL, s).filter(el => !el.classList.contains('cfx-enter'));
    cards.forEach(function (el, i) {
      el.style.setProperty('--cfx-i', String(Math.min(i, 14)));
      el.classList.add('cfx-enter');
      el.addEventListener('animationend', function once() {
        el.classList.remove('cfx-enter');
        el.removeEventListener('animationend', once);
      });
    });
    return cards.length;
  }

  /* 数值变化检测：返回 true 表示"这次和上次不一样"。
     比对表放在 JS 里而不是 DOM 上 —— innerHTML 重写会把 DOM 上的记忆冲掉。 */
  function cfxChanged(key, val) {
    const had = Object.prototype.hasOwnProperty.call(CFX_PREV, key);
    const changed = had && CFX_PREV[key] !== val;
    CFX_PREV[key] = val;
    return changed;
  }

  /* ------------------------------------------------------------ v2.0 角色与权限"""),

    ("""    save, load, icon, countUp, spark, initReveal,
    authMe, can, gate, gateNav, roleSwitch, logout, initAuth, renderRoleBar,""",
     """    save, load, icon, countUp, spark, initReveal,
    motion: { enter: cfxEnter, changed: cfxChanged },
    authMe, can, gate, gateNav, roleSwitch, logout, initAuth, renderRoleBar,"""),

    # 静态页面（登录页 / 权限页）自动播一次
    ("""  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initReveal());
  } else {
    initReveal();
  }""",
     """  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { initReveal(); cfxEnter(); });
  } else {
    initReveal();
    cfxEnter();
  }"""),
])

# ================================================================ 各页面挂载
# index：首屏 + 下钻弹窗（弹窗内容晚于首屏出现，单独作为容器播一次）
patch("ui/index.html", [
    ("""  renderTable();
  await renderStats();
  CR.initReveal();
}""",
     """  renderTable();
  await renderStats();
  CR.motion.enter();            // 首屏卡片错峰入场（同一容器只播一次）
  CR.initReveal();
}"""),
    ("""  // 行点击跳转用事件委托，避免把路径拼进 onclick 字符串（引号易出错）
  CR.$$('#drillBody tr[data-go]').forEach(function (tr) {""",
     """  CR.motion.enter(CR.$('#drillBody'));   // 弹窗内容作为独立容器播一次入场
  // 行点击跳转用事件委托，避免把路径拼进 onclick 字符串（引号易出错）
  CR.$$('#drillBody tr[data-go]').forEach(function (tr) {"""),
])

# workbench：主渲染 + 结果页签（风险卡片）
patch("ui/workbench.html", [
    ("""  renderSide();
  renderWriteback();
  loadNotifyStatus();""",
     """  renderSide();
  renderWriteback();
  CR.motion.enter();            // 双栏面板与风险卡片错峰入场
  CR.motion.enter(CR.$('#tabBody'));
  loadNotifyStatus();"""),
])

# rules / twin / mock
patch("ui/rules.html", [
    ("CR.initAuth(", "CR.initAuth("),
])
patch("ui/twin.html", [
    ("""  FIRST = false;
  CR.initReveal();
}""",
     """  FIRST = false;
  CR.motion.enter();            // 首屏播一次；后续 4 秒刷新不再重播入场
  CR.initReveal();
}"""),
    # KPI 数值变化时跳一下
    ("""    '<div class="v">' + x[1] + '<small>' + x[2] + '</small></div>' +""",
     """    '<div class="v' + (CR.motion.changed('kpi|' + x[0], x[1]) ? ' cfx-tick' : '') +
    '">' + x[1] + '<small>' + x[2] + '</small></div>' +"""),
    # 流水线节点数值变化时跳一下
    ("""    '<div class="vl">' + n.value + '<small>' + CR.esc(n.unit) + '</small></div>' +""",
     """    '<div class="vl">' + n.value + '<small>' + CR.esc(n.unit) + '</small></div>' +"""),
])

# 普通页面（无显式渲染钩子的）：在 initAuth 之后补一次
for rel in ("ui/rules.html",):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    if "CR.motion.enter()" not in c:
        c = c.replace("CR.initAuth(", "CR.motion.enter();\nCR.initAuth(", 1)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(c)
        log.append("  ok   %s :: 补一次入场" % rel)
    else:
        log.append("  skip %s" % rel)

patch("ui/mock.html", [
    ("""  renderList();
  renderEps();
  if (CUR) pick(CUR);""",
     """  renderList();
  renderEps();
  CR.motion.enter();
  if (CUR) pick(CUR);"""),
    ("""  CR.$('#detail').innerHTML = html;
}""",
     """  CR.$('#detail').innerHTML = html;
  CR.motion.enter(CR.$('#detail'));
}"""),
])

with io.open(os.path.join(ROOT, "verify", "cfx_js.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("cfx js done")
