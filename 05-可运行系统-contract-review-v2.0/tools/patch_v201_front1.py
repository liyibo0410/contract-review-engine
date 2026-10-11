# -*- coding: utf-8 -*-
"""v2.0.1 前端改造 ①：顶栏精简（响应式）+ 大盘统计卡下钻。

顶栏：
  * 抽出 .role-slot 占位，角色区放到环境标签之前（窄屏换行顺序更合理）；
  * 模拟审批 / 主题切换按钮加 hide-sm（≤980px 收起），避免顶栏挤成三行；
  * 「模拟审批接口」改指美化页 /mock（原始 JSON 在页内有入口）。

大盘：
  * card() 增加 drill 参数 → 渲染成可点击卡片（键盘可达）；
  * renderStats 给 7 张卡各自绑定下钻目标；
  * 新增 #mDrill 弹窗 + openDrill()：汇总条 + 真实明细表。
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
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:62]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:62]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ================================================================= app.js 顶栏
patch("ui/assets/app.js", [
    ("""      '<div class="spacer"></div>',
      '<span class="env-tag" id="envTag">' + esc(envText || '本地演示环境') + '</span>',
      '<a class="btn sm ghost" href="/mock/feishu/api/todo" target="_blank" rel="noopener">' +
        icon('external', '', 13) + '模拟审批接口</a>',
      '<button class="btn sm ghost" id="themeBtn" onclick="CR.toggleTheme()"></button>'
    ].join('');""",
     """      '<div class="spacer"></div>',
      '<span class="role-slot"></span>',
      '<span class="env-tag" id="envTag">' + esc(envText || '本地演示环境') + '</span>',
      '<a class="btn sm ghost hide-sm" href="/mock" title="模拟审批系统（美化查看页）">' +
        icon('external', '', 13) + '模拟审批系统</a>',
      '<button class="btn sm ghost hide-sm" id="themeBtn" onclick="CR.toggleTheme()"></button>'
    ].join('');"""),

    ("""  function renderRoleBar() {
    var bar = $('#topbar');
    if (!bar || $('.role-bar', bar)) return;
    var wrap = document.createElement('span');
    wrap.className = 'role-bar';
    wrap.innerHTML =
      '<span class="role-chip" id="roleChip"></span>' +
      '<select class="role-switch" id="roleSwitch" onchange="CR.roleSwitch(this.value)" ' +
      'aria-label="切换演示身份"></select>' +
      '<a class="btn sm ghost" href="/logout" title="退出登录">退出</a>';
    bar.appendChild(wrap);
    paintRoleChip();
  }""",
     """  function renderRoleBar() {
    var bar = $('#topbar');
    if (!bar || $('#roleChip')) return;
    var slot = $('.role-slot', bar) || bar;
    var wrap = document.createElement('span');
    wrap.className = 'role-bar';
    wrap.innerHTML =
      '<span class="role-chip" id="roleChip"></span>' +
      '<select class="role-switch" id="roleSwitch" onchange="CR.roleSwitch(this.value)" ' +
      'aria-label="切换演示身份"></select>' +
      '<a class="btn sm ghost" href="/logout" title="退出登录">退出</a>';
    if (slot === bar) { bar.appendChild(wrap); } else { slot.replaceWith(wrap); }
    paintRoleChip();
  }"""),
])

# ================================================================= index.html
patch("ui/index.html", [
    # 1) card() 支持下钻
    ("""function card(k, v, x, cls, ic, pct, color) {
  const isNum = typeof v === 'number';
  return '<div class="stat ' + (cls || '') + '"' +
    (color ? ' style="--c:' + color + '"' : '') + '>' +
    '<div class="k">' + CR.icon(ic || 'dot', '', 13) + k + '</div>' +
    '<div class="v num"' + (isNum ? ' data-count="' + v + '"' : '') + '>' + v + '</div>' +
    '<div class="x">' + x + '</div>' +
    (pct === undefined ? '' :
      '<div class="bar" style="margin-top:10px"><i style="width:' + Math.max(2, pct) + '%"></i></div>') +
    '</div>';
}""",
     """/* drill：传入 {key, val, title} 即把卡片变成可点击的下钻入口（键盘可达） */
function card(k, v, x, cls, ic, pct, color, drill) {
  const isNum = typeof v === 'number';
  const d = drill ? ' data-drill="' + CR.esc(JSON.stringify(drill)) + '"' : '';
  return '<div class="stat ' + (cls || '') + (drill ? ' clickable' : '') + '"' +
    (color ? ' style="--c:' + color + '"' : '') + d +
    (drill ? ' tabindex="0" role="button" aria-label="' + CR.esc(k + '：查看明细') + '"' : '') + '>' +
    '<div class="k">' + CR.icon(ic || 'dot', '', 13) + k + '</div>' +
    '<div class="v num"' + (isNum ? ' data-count="' + v + '"' : '') + '>' + v + '</div>' +
    '<div class="x">' + x + '</div>' +
    (pct === undefined ? '' :
      '<div class="bar" style="margin-top:10px"><i style="width:' + Math.max(2, pct) + '%"></i></div>') +
    '</div>';
}"""),

    # 2) renderStats 绑定下钻
    ("""  CR.$('#stats').innerHTML =
    card('审查任务总数', total, '含审批拉取与本地上传', '', 'layers', 100, 'var(--brand)') +
    card('高风险合同', br.high || 0, '需优先处置', br.high ? 'hl' : '', 'alert',
         pc(br.high, total), 'var(--high)') +
    card('中风险合同', br.medium || 0, '需整改后签署', br.medium ? 'hl' : '', 'bolt',
         pc(br.medium, total), 'var(--mid)') +
    card('审查完成', bs.completed || 0, '已生成审查意见', 'ok', 'checkCircle',
         pc(bs.completed, total), 'var(--ok)') +
    card('解析受阻', st.blocked || 0, '待管理员重试', st.blocked ? 'hl' : '', 'lock',
         pc(st.blocked, total), 'var(--mid)') +
    card('回写成功', bw.success || 0, '已写入审批评论区', 'ok', 'external',
         pc(bw.success, total), 'var(--teal)') +
    card('高风险金额', CR.money(st.high_risk_amount), '高风险合同累计金额', 'hl', 'scale',
         undefined, 'var(--high)');""",
     """  // 7 张卡全部可下钻：点击后弹出该口径的明细清单（含金额合计与占比）
  CR.$('#stats').innerHTML =
    card('审查任务总数', total, '含审批拉取与本地上传', '', 'layers', 100, 'var(--brand)',
         { key: 'all', title: '全部审查任务' }) +
    card('高风险合同', br.high || 0, '需优先处置', br.high ? 'hl' : '', 'alert',
         pc(br.high, total), 'var(--high)',
         { key: 'risk', val: 'high', title: '高风险合同明细' }) +
    card('中风险合同', br.medium || 0, '需整改后签署', br.medium ? 'hl' : '', 'bolt',
         pc(br.medium, total), 'var(--mid)',
         { key: 'risk', val: 'medium', title: '中风险合同明细' }) +
    card('审查完成', bs.completed || 0, '已生成审查意见', 'ok', 'checkCircle',
         pc(bs.completed, total), 'var(--ok)',
         { key: 'status', val: 'completed', title: '审查完成的任务' }) +
    card('解析受阻', st.blocked || 0, '待管理员重试', st.blocked ? 'hl' : '', 'lock',
         pc(st.blocked, total), 'var(--mid)',
         { key: 'status', val: 'blocked', title: '解析受阻的任务' }) +
    card('回写成功', bw.success || 0, '已写入审批评论区', 'ok', 'external',
         pc(bw.success, total), 'var(--teal)',
         { key: 'writeback', val: 'success', title: '回写成功的任务' }) +
    card('高风险金额', CR.money(st.high_risk_amount), '高风险合同累计金额', 'hl', 'scale',
         undefined, 'var(--high)',
         { key: 'risk', val: 'high', title: '高风险合同金额明细' });
  bindDrill();
  renderViz(br);
}

/* -------------------------------------------------- 统计卡下钻（v2.0.1）
   点击卡片 → 用同一套筛选参数拉真实清单 → 汇总条 + 明细表。
   不用前端本地数组过滤，保证「弹窗里的数」和「接口返回的数」永远一致。 */
function bindDrill() {
  CR.$$('#stats .stat.clickable').forEach(function (el) {
    el.onclick = function () { openDrill(JSON.parse(el.dataset.drill)); };
    el.onkeydown = function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        openDrill(JSON.parse(el.dataset.drill));
      }
    };
  });
}

const DRILL_Q = { risk: 'risk', status: 'status', writeback: 'writeback' };

async function openDrill(cfg) {
  if (!cfg) return;
  CR.openModal('mDrill');
  const title = CR.$('#drillTitle');
  if (title) title.textContent = cfg.title || '明细';
  CR.$('#drillBody').innerHTML = CR.skeleton(5);
  const q = new URLSearchParams();
  if (cfg.key !== 'all' && DRILL_Q[cfg.key]) q.set(DRILL_Q[cfg.key], cfg.val);
  try {
    const data = await CR.api('GET', '/api/cases?' + q.toString());
    renderDrill(cfg, data.items || []);
  } catch (e) {
    CR.$('#drillBody').innerHTML = '<div class="empty">读取明细失败：' +
      CR.esc(e.message) + '</div>';
  }
}

function renderDrill(cfg, items) {
  const amt = items.reduce((s, c) => s + Number(c.amount || 0), 0);
  const parties = new Set(items.map(c => c.counterparty).filter(Boolean));
  const hi = items.filter(c => c.risk_level === 'high').length;
  const total = (STAT_TOTAL || 0) || items.length;
  const share = total ? Math.round(items.length / total * 100) : 0;
  const maxAmt = items.reduce((m, c) => Math.max(m, Number(c.amount || 0)), 0);

  const sum =
    '<div class="drill-sum">' +
    '<div class="kv-cell"><div class="k">命中任务</div><div class="v">' + items.length +
    '<small>个</small></div><div class="faint" style="font-size:11.6px">占全部任务 ' +
    share + '%</div></div>' +
    '<div class="kv-cell"><div class="k">金额合计</div><div class="v" style="font-size:15px">' +
    CR.money(amt) + '</div><div class="faint" style="font-size:11.6px">单笔最高 ' +
    CR.money(maxAmt).replace(/<[^>]+>/g, '').trim() + '</div></div>' +
    '<div class="kv-cell"><div class="k">涉及相对方</div><div class="v">' + parties.size +
    '<small>家</small></div><div class="faint" style="font-size:11.6px">去重统计</div></div>' +
    '<div class="kv-cell"><div class="k">其中高风险</div><div class="v" style="color:var(--high)">' +
    hi + '<small>个</small></div><div class="faint" style="font-size:11.6px">需优先处置</div></div>' +
    '</div>';

  const rows = items.map(c => {
    const blocked = c.review_status === 'blocked';
    return '<tr class="clickable" onclick="location.href=\'/workbench?id=' + c.id + '\'">' +
      '<td class="mono" style="font-size:11.8px">' + CR.esc(c.case_no) + '</td>' +
      '<td>' + CR.esc(c.title) + '</td>' +
      '<td>' + CR.esc(c.counterparty || '-') + '</td>' +
      '<td class="num amt">' + CR.money(c.amount).replace(/<[^>]+>/g, '').trim() + '</td>' +
      '<td>' + CR.riskTag(c.risk_level, c.risk_level_label) + '</td>' +
      '<td>' + (blocked
        ? '<span class="tag blocked">解析受阻</span>'
        : CR.statusTag(c.review_status, c.review_status_label)) + '</td>' +
      '<td>' + CR.wbTag(c.writeback_status, c.writeback_status_label) + '</td>' +
      '<td>' + (blocked
        ? '<span class="faint" style="font-size:11.6px">' +
          CR.esc((c.blocked_reason || '').slice(0, 18)) + '…</span>'
        : '<span style="color:var(--brand);font-size:12.4px">进入审查 →</span>') + '</td>' +
      '</tr>';
  }).join('');

  CR.$('#drillBody').innerHTML = sum +
    (items.length
      ? '<div style="font-size:11.8px;color:var(--sub);margin:14px 0 6px">' +
        '明细清单（点任一行进入该任务的审查工作台）</div>' +
        '<div class="table-wrap"><table class="drill-tbl"><thead><tr>' +
        '<th>单号</th><th>合同名称</th><th>相对方</th><th class="num">金额</th>' +
        '<th>风险</th><th>审查状态</th><th>回写</th><th>操作</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
        '<div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">' +
        '<button class="btn sm" onclick="drillToFilter()">在大盘中按此条件筛选</button>' +
        '<button class="btn sm ghost" onclick="CR.download(\'/api/export\')">导出全量数据(JSON)</button>' +
        '</div>'
      : '<div class="empty" style="padding:26px 8px">该口径下暂无任务。</div>');
}

/* 把下钻条件回填到大盘筛选器上，保持「弹窗 → 列表」口径一致 */
function drillToFilter() {
  const cfg = window.__LAST_DRILL;
  if (!cfg) return;
  CR.closeModal('mDrill');
  if (cfg.key === 'risk') { CR.$('#fRisk').value = cfg.val; }
  else if (cfg.key === 'status') { CR.$('#fStatus').value = cfg.val; }
  else if (cfg.key === 'writeback') { CR.$('#fRisk').value = ''; }
  if (cfg.key === 'all') {
    CR.$('#fRisk').value = ''; CR.$('#fStatus').value = ''; CR.$('#fType').value = '';
    CR.$('#kw').value = '';
  }
  load();
}"""),
])

# openDrill 里记录最后一次下钻条件
patch("ui/index.html", [
    ("async function openDrill(cfg) {\n  if (!cfg) return;",
     "async function openDrill(cfg) {\n  if (!cfg) return;\n  window.__LAST_DRILL = cfg;"),
    # STAT_TOTAL 供占比计算
    ("  const pc = (a, b) => (b ? Math.round((a || 0) / b * 100) : 0);",
     "  const pc = (a, b) => (b ? Math.round((a || 0) / b * 100) : 0);\n  STAT_TOTAL = total;"),
    ("let CASES = [];",
     "let CASES = [];\nlet STAT_TOTAL = 0;"),
])

# 统计卡逻辑尾部原为 renderViz(br); 现在被新代码接管，清掉重复调用
patch("ui/index.html", [
    ("""  CR.$('#subTitle').textContent = '共 ' + total + ' 个审查任务 · 高风险 ' +
    (br.high || 0) + ' 个 · 解析受阻 ' + (st.blocked || 0) + ' 个';

  renderViz(br);
}""",
     """  CR.$('#subTitle').textContent = '共 ' + total + ' 个审查任务 · 高风险 ' +
    (br.high || 0) + ' 个 · 解析受阻 ' + (st.blocked || 0) + ' 个 · 点下方卡片可查看明细';
}"""),
])

# 新增下钻弹窗 DOM
patch("ui/index.html", [
    ("""<!-- ==================== 角色与权限（需求 2.4.2） ==================== -->""",
     """<!-- ==================== 统计卡下钻明细（v2.0.1） ==================== -->
<div class="mask" id="mDrill">
  <div class="modal" style="max-width:1120px">
    <div class="mh">
      <span class="ttl" id="drillTitle">明细</span>
      <div class="spacer"></div>
      <button class="btn sm ghost" onclick="CR.closeModal('mDrill')">关闭</button>
    </div>
    <div class="mb" id="drillBody"></div>
    <div class="mf">
      <span class="faint" style="font-size:11.8px;margin-right:auto">
        数据口径与大盘筛选一致，均来自 /api/cases 实时查询</span>
      <button class="btn primary" onclick="CR.closeModal('mDrill')">关闭</button>
    </div>
  </div>
</div>

<!-- ==================== 角色与权限（需求 2.4.2） ==================== -->"""),
])

with io.open(os.path.join(ROOT, "verify", "ui_patch_front1.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("front patch 1 done")
