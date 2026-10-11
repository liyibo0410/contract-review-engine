# -*- coding: utf-8 -*-
"""v2.0 界面收尾补丁：给动态渲染的按钮加 data-cap、给工作台加状态轮询、
并把各页面顶栏初始化切到 CR.initAuth（带身份与能力）。"""
import io
import os
import re

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs, required=True):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new)
            log.append("  ok   %s :: %s" % (rel, old[:56].replace("\n", " ")))
        else:
            log.append("  MISS %s :: %s" % (rel, old[:56].replace("\n", " ")))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ---------------- 工作台：动态按钮的权限标记
patch("ui/workbench.html", [
    ("'<button class=\"btn sm primary\" onclick=\"event.stopPropagation();adopt(' + r.id + ',true)\">采纳建议</button>' +",
     "'<button class=\"btn sm primary\" data-cap=\"edit_suggestion\" data-cap-label=\"采纳/调整修改建议\" "
     "onclick=\"event.stopPropagation();adopt(' + r.id + ',true)\">采纳建议</button>' +"),
    ("(r.adopted ? '<button class=\"btn sm\" onclick=\"event.stopPropagation();adopt(' + r.id + ',false)\">取消采纳</button>' : '') +",
     "(r.adopted ? '<button class=\"btn sm\" data-cap=\"edit_suggestion\" data-cap-label=\"采纳/调整修改建议\" "
     "onclick=\"event.stopPropagation();adopt(' + r.id + ',false)\">取消采纳</button>' : '') +"),
    ("'<button class=\"btn sm\" style=\"margin-top:5px\" onclick=\"useAlt(' + r.id + ',' + ai + ')\">用此方案替换</button>",
     "'<button class=\"btn sm\" style=\"margin-top:5px\" data-cap=\"edit_suggestion\" "
     "data-cap-label=\"采纳/调整修改建议\" onclick=\"useAlt(' + r.id + ',' + ai + ')\">用此方案替换</button>"),
    ("<button class=\"btn sm danger\" style=\"margin-top:8px\" onclick=\"doWriteback('live')\">重试回写</button>",
     "<button class=\"btn sm danger\" data-cap=\"writeback\" data-cap-label=\"回写审批意见\" "
     "style=\"margin-top:8px\" onclick=\"doWriteback('live')\">重试回写</button>"),
])

# ---------------- 工作台：审查进行中的状态轮询（M9「状态流转可见过程」）
patch("ui/workbench.html", [
    ("""  renderSide();
  renderWriteback();
  loadNotifyStatus();
  CR.initReveal();
}""",
     """  renderSide();
  renderWriteback();
  loadNotifyStatus();
  if (CR.gate) CR.gate();            // 动态渲染出来的按钮也要按能力显隐
  watchStatus();
  CR.initReveal();
}

/* -------------------------------------------------- v2.0 流转过程可见（M9）
   任务处于 pending / parsing / reviewing 时按 1s 轮询，把「正在解析 → 正在审查」
   的过程显示出来，而不是让用户对着一个静态页面猜系统是不是卡住了。 */
let WATCH = null;
const RUNNING = ['pending', 'parsing', 'reviewing'];

function watchStatus() {
  if (WATCH) { clearInterval(WATCH); WATCH = null; }
  if (!D || !D.case || RUNNING.indexOf(D.case.review_status) < 0) return;
  let ticks = 0;
  WATCH = setInterval(async function () {
    ticks++;
    try {
      const c = await CR.api('GET', '/api/cases/' + CASE_ID);
      const st = c.case ? c.case.review_status : '';
      if (RUNNING.indexOf(st) < 0 || ticks > 240) {
        clearInterval(WATCH); WATCH = null;
        if (RUNNING.indexOf(st) < 0) { await load(); CR.toast('审查已完成，页面已刷新', 'ok'); }
        return;
      }
      const lab = { pending: '待处理', parsing: '解析中', reviewing: '审查中' }[st] || st;
      const tag = CR.$('#hTags');
      if (tag) {
        let t = CR.$('#liveTag');
        if (!t) {
          t = document.createElement('span');
          t.id = 'liveTag';
          t.className = 'tag run';
          tag.appendChild(t);
        }
        t.textContent = lab + ' · 第 ' + ticks + ' 秒';
      }
    } catch (e) { /* 忽略单次轮询失败，下一轮继续 */ }
  }, 1000);
}"""),
])

# ---------------- 规 则页 / 孪生页：顶栏初始化 + 权限
for rel in ("ui/rules.html", "ui/twin.html"):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    n = c
    n = re.sub(r"CR\.renderTopbar\(", "CR.initAuth(", n)
    if n != c:
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(n)
        log.append("  ok   %s :: renderTopbar -> initAuth" % rel)
    else:
        log.append("  MISS %s :: renderTopbar -> initAuth" % rel)

with io.open(os.path.join(ROOT, "verify", "ui_patch.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("ui patch done")
