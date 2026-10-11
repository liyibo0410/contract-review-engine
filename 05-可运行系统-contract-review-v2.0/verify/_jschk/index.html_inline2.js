
let CASES = [];
let STAT_TOTAL = 0;
let FILE = null;
let CFG = { max_upload_mb: 30 };

const BTYPE = { purchase: '采购合同', sales: '销售合同', service: '服务合同', labor: '劳动合同' };

CR.initAuth('/', '演示环境 · 模拟审批系统');

function debounce(fn, ms) {
  let t;
  return function () { clearTimeout(t); t = setTimeout(fn, ms || 320); };
}
const debouncedLoad = debounce(load);

/* ------------------------------------------------------------------ 数据 */
async function load() {
  const q = new URLSearchParams();
  const kw = CR.$('#kw').value.trim();
  if (kw) q.set('kw', kw);
  const r = CR.$('#fRisk').value; if (r) q.set('risk', r);
  const s = CR.$('#fStatus').value; if (s) q.set('status', s);
  const t = CR.$('#fType').value; if (t) q.set('type', t);
  if (!CASES.length) {
    CR.$('#tbody').innerHTML = '<tr><td colspan="10">' + CR.skeleton(6) + '</td></tr>';
    CR.$('#vizRisk').innerHTML = CR.skeleton(3);
    CR.$('#vizType').innerHTML = CR.skeleton(3);
    CR.$('#vizAmount').innerHTML = CR.skeleton(3);
  }
  try {
    const data = await CR.api('GET', '/api/cases?' + q.toString());
    CASES = data.items || [];
  } catch (e) {
    CR.$('#tbody').innerHTML = '<tr><td colspan="10"><div class="empty">加载失败：' +
      CR.esc(e.message) +
      '<br><button class="btn sm" style="margin-top:10px" onclick="load()">重试</button>' +
      '</div></td></tr>';
    return;
  }
  renderTable();
  await renderStats();
  CR.motion.enter();            // 首屏卡片错峰入场（同一容器只播一次）
  CR.initReveal();
}

/* ------------------------------------------------------------------ KPI + 可视化 */
async function renderStats() {
  let st;
  try { st = await CR.api('GET', '/api/stats'); } catch (e) { return; }
  const bs = st.by_status || {}, br = st.by_risk || {}, bw = st.by_writeback || {};
  const total = st.total || 0;
  const pc = (a, b) => (b ? Math.round((a || 0) / b * 100) : 0);
  STAT_TOTAL = total;

  // 7 张卡全部可下钻：点击后弹出该口径的明细清单（含金额合计与占比）。
  // 「高风险金额」是唯一一张数值长度不确定的卡：合同金额从几千到十几亿都可能，
  // 所以主显用 CR.moneyBrief() 的亿/万降档格式（长度有上界），精确值退到副标题，
  // 渲染完再交给 CR.fitStats() 做一次自适应字号 —— 保证任何数值都不会被卡片裁掉。
  const hra = Number(st.high_risk_amount || 0);
  const hraExact = hra > 0
    ? '¥' + hra.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '';
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
    card('高风险金额', hra > 0 ? CR.moneyBrief(hra) : '-',
         hraExact ? '累计 · ' + hraExact : '高风险合同累计金额',
         'hl amt', 'scale', undefined, 'var(--high)',
         { key: 'risk', val: 'high', title: '高风险合同金额明细' });

  // 数字滚动（金额卡片是格式化文本，不参与）
  CR.$$('#stats .v[data-count]').forEach(el => {
    const target = parseFloat(el.dataset.count) || 0;
    CR.countUp(el, target, { duration: 1500, thousands: true });
  });
  // 金额卡字号自适应（现在算 + 窗口变化时重算，见 app.js fitStats）
  if (CR.fitStats) CR.fitStats(CR.$('#stats'));
  CR.$('#subTitle').textContent = '共 ' + total + ' 个审查任务 · 高风险 ' +
    (br.high || 0) + ' 个 · 解析受阻 ' + (st.blocked || 0) +
    ' 个 · 点下方统计卡片可查看明细';
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
  window.__LAST_DRILL = cfg;
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
    return '<tr class="clickable" data-go="' + c.id + '">' +
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
        '<button class="btn sm ghost" onclick="CR.download(&quot;/api/export&quot;)">导出全量数据(JSON)</button>' +
        '</div>'
      : '<div class="empty" style="padding:26px 8px">该口径下暂无任务。</div>');

  CR.motion.enter(CR.$('#drillBody'));   // 弹窗内容作为独立容器播一次入场
  // 行点击跳转用事件委托，避免把路径拼进 onclick 字符串（引号易出错）
  CR.$$('#drillBody tr[data-go]').forEach(function (tr) {
    tr.onclick = function () { location.href = '/workbench?id=' + tr.dataset.go; };
  });
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
}

/* 三张分布图：数据全部来自当前筛选结果，不做任何虚构 */
function renderViz(br) {
  const items = CASES || [];

  // ① 风险等级分布
  const riskItems = [
    { label: '高风险', value: items.filter(c => c.risk_level === 'high').length, color: 'var(--high)' },
    { label: '中风险', value: items.filter(c => c.risk_level === 'medium').length, color: 'var(--mid)' },
    { label: '低风险', value: items.filter(c => c.risk_level === 'low').length, color: 'var(--low)' },
    { label: '无风险', value: items.filter(c => !c.risk_level || c.risk_level === 'none').length, color: 'var(--line)' }
  ];
  if (!items.length) {
    CR.$('#vizRisk').innerHTML = '<div class="empty" style="padding:22px 8px">当前没有可统计的任务</div>';
  } else {
    CH.donut(CR.$('#vizRisk'), riskItems, {
      centerValue: items.length, centerLabel: '份合同', aria: '风险等级分布'
    });
  }

  // ② 合同类型分布（数量 + 平均金额）
  const byType = {};
  items.forEach(c => {
    const k = c.business_type_label || BTYPE[c.business_type] || '其他';
    (byType[k] = byType[k] || { n: 0, sum: 0 }).n++;
    byType[k].sum += Number(c.amount || 0);
  });
  const typeKeys = Object.keys(byType);
  if (!typeKeys.length) {
    CR.$('#vizType').innerHTML = '<div class="empty" style="padding:22px 8px">当前没有可统计的任务</div>';
  } else {
    const palette = ['var(--brand)', 'var(--cyan)', 'var(--brand-2)', 'var(--teal)'];
    CH.hbars(CR.$('#vizType'), typeKeys.map((k, i) => ({
      label: k,
      value: byType[k].n,
      display: byType[k].n + ' 份',
      sub: '均 ' + CR.money(byType[k].sum / byType[k].n).replace(/<[^>]+>/g, '').trim(),
      color: 'linear-gradient(90deg,' + palette[i % palette.length] + ',var(--brand-2))'
    })));
  }

  // ③ 合同金额区间分布
  const BK = [
    [0, 100000, '< 10 万'],
    [100000, 500000, '10–50 万'],
    [500000, 1000000, '50–100 万'],
    [1000000, 5000000, '100–500 万'],
    [5000000, Infinity, '≥ 500 万']
  ];
  const buckets = BK.map(b => ({ label: b[2], value: 0 }));
  items.forEach(c => {
    const v = Number(c.amount || 0);
    for (let i = 0; i < BK.length; i++) {
      if (v >= BK[i][0] && v < BK[i][1]) { buckets[i].value++; break; }
    }
  });
  if (!items.length) {
    CR.$('#vizAmount').innerHTML = '<div class="empty" style="padding:22px 8px">当前没有可统计的任务</div>';
  } else {
    CH.hbars(CR.$('#vizAmount'), buckets.map((b, i) => ({
      label: b.label,
      value: b.value,
      display: b.value + ' 份',
      color: 'linear-gradient(90deg,var(--cyan),var(--brand))',
      textColor: b.value ? 'var(--ink)' : 'var(--faint)'
    })));
  }
}

/* drill：传入 {key, val, title} 即把卡片变成可点击的下钻入口（键盘可达） */
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
}

function renderTable() {
  const tb = CR.$('#tbody');
  if (!CASES.length) {
    tb.innerHTML = '';
    CR.$('#empty').style.display = 'block';
    return;
  }
  CR.$('#empty').style.display = 'none';
  tb.innerHTML = CASES.map(c => {
    const blocked = c.review_status === 'blocked';
    return '<tr data-id="' + c.id + '" data-risk="' + (c.risk_level || 'none') +
      '" class="clickable" onclick="rowClick(event,' + c.id + ')">' +
      '<td><input type="checkbox" class="ck" value="' + c.id +
        '" onclick="event.stopPropagation();selInfo()" aria-label="选择该合同"></td>' +
      '<td><div class="t-title">' + CR.esc(c.title) + '</div><div class="t-sub">' +
        CR.esc(c.case_no) + ' · ' + CR.esc(c.source_label) + '</div></td>' +
      '<td>' + CR.esc(c.applicant || '-') + '<div class="t-sub">' +
        CR.esc(c.send_dept || '') + '</div></td>' +
      '<td>' + CR.esc(c.business_type_label) + '</td>' +
      '<td class="right num">' + CR.money(c.amount, c.currency) + '</td>' +
      '<td>' + CR.statusTag(c.review_status, c.review_status_label) + '</td>' +
      '<td>' + CR.riskTag(c.risk_level, c.risk_level_label) + '</td>' +
      '<td>' + CR.wbTag(c.writeback_status, c.writeback_status_label) + '</td>' +
      '<td class="num faint" style="font-size:12px">' + CR.esc(c.created_at) + '</td>' +
      '<td class="right" onclick="event.stopPropagation()">' +
        '<a class="btn sm primary" href="/workbench?id=' + c.id + '">审查</a> ' +
        (blocked
          ? '<button class="btn sm danger" onclick="retry(' + c.id + ')">重新审查</button>'
          : '<button class="btn sm" onclick="rerun(' + c.id + ')">重审</button>') +
      '</td></tr>';
  }).join('');
  selInfo();
}

function rowClick(ev, id) {
  if (ev.target.tagName === 'INPUT' || ev.target.closest('button')
      || ev.target.closest('a')) return;
  location.href = '/workbench?id=' + id;
}

function selInfo() {
  const n = CR.$$('.ck:checked').length;
  CR.$('#selInfo').textContent = n ? ('已选 ' + n + ' 项') : '';
}

function toggleAll(ck) {
  CR.$$('.ck').forEach(c => { c.checked = ck.checked; });
  selInfo();
}

function selected() { return CR.$$('.ck:checked').map(c => Number(c.value)); }

/* ------------------------------------------------------------------ 操作 */
async function pullTodo() {
  const b = CR.$('#btnPull');
  CR.busy(b, true, '拉取中');
  CR.overlay(true, '正在拉取待办并执行自动审查…',
    '解析 → 条款切分 → 规则审查 → 摘要生成，逐个审批单串行处理。');
  try {
    const d = await CR.api('POST', '/api/pull', { auto_review: true });
    if (!d.ok) throw new Error(d.error || '拉取失败');
    const created = (d.created || []).length, skipped = (d.skipped || []).length;
    const blocked = (d.reviewed || []).filter(r => r.blocked).length;
    const highs = (d.reviewed || []).filter(r => r.risk_level === 'high').length;
    CR.toast('已拉取并自动审查 <b>' + created + '</b> 个待办审批单' +
      (skipped ? '，跳过 ' + skipped + ' 个（幂等控制）' : '') +
      (highs ? '<br>其中高风险 <b>' + highs + '</b> 个' : '') +
      (blocked ? '，解析受阻 ' + blocked + ' 个' : ''), 'ok');
    await load();
  } catch (e) {
    CR.toast('拉取失败：' + CR.esc(e.message), 'err');
  } finally {
    CR.busy(b, false);
    CR.overlay(false);
  }
}

async function rerun(id) {
  CR.overlay(true, '正在重新审查…', '重新执行解析与规则审查。');
  try {
    const r = await CR.api('POST', '/api/cases/' + id + '/review');
    if (r && r.blocked) CR.toast('重新审查后仍受阻：' + CR.esc(r.reason || ''), 'err');
    else CR.toast('已完成重新审查，命中 ' + ((r && r.hits) || 0) + ' 项', 'ok');
    await load();
  } catch (e) { CR.toast('审查失败：' + CR.esc(e.message), 'err'); }
  finally { CR.overlay(false); }
}

async function retry(id) {
  CR.overlay(true, '正在重试解析…', '把任务重新放回流转流水线。');
  try {
    const r = await CR.api('POST', '/api/cases/' + id + '/retry');
    if (r && r.blocked) CR.toast('重试后仍受阻：' + CR.esc(r.reason || ''), 'err');
    else CR.toast('重试成功，已完成审查', 'ok');
    await load();
  } catch (e) { CR.toast('重试失败：' + CR.esc(e.message), 'err'); }
  finally { CR.overlay(false); }
}

async function batchReview() {
  const ids = selected();
  if (!ids.length) return CR.toast('请先勾选需要重新审查的合同', 'err');
  const failed = [];
  try {
    for (let i = 0; i < ids.length; i++) {
      CR.overlay(true, '正在批量审查… ' + (i + 1) + ' / ' + ids.length);
      try {
        await CR.api('POST', '/api/cases/' + ids[i] + '/review');
      } catch (e) { failed.push('#' + ids[i] + ' ' + e.message); }
    }
  } finally { CR.overlay(false); }
  if (failed.length) {
    CR.toast('完成 ' + (ids.length - failed.length) + ' 个，<b>' + failed.length +
      '</b> 个失败：' + CR.esc(failed.slice(0, 2).join('；')), 'err');
  } else {
    CR.toast('已完成 ' + ids.length + ' 个合同的重新审查', 'ok');
  }
  await load();
}

async function batchReport(fmt) {
  const ids = selected();
  if (!ids.length) return CR.toast('请先勾选需要导出的合同', 'err');
  for (const id of ids) {
    CR.download('/api/cases/' + id + '/report?format=' + fmt + '&download=1');
    await new Promise(r => setTimeout(r, 260));
  }
  CR.toast('已开始下载 ' + ids.length + ' 份报告（' + fmt.toUpperCase() + '）', 'ok');
}

/* ------------------------------------------------------------------ 示例集 */
let samplesLoaded = false;
async function toggleSamples() {
  const box = CR.$('#sampleBox'), b = CR.$('#btnSamples');
  const on = box.style.display === 'none';
  box.style.display = on ? 'block' : 'none';
  b.textContent = on ? '收起' : '展开';
  if (on && !samplesLoaded) {
    box.innerHTML = CR.skeleton(4);
    const d = await CR.api('GET', '/api/samples');
    const lvLabel = l => l === 'high' ? '高风险' : (l === 'medium' ? '中风险'
      : (l === 'low' ? '低风险' : '无风险'));
    box.innerHTML = '<div class="table-wrap"><table class="grid"><thead><tr>' +
      '<th style="width:44px">序号</th><th>示例文件</th><th style="width:96px">格式</th>' +
      '<th style="width:112px">类别</th><th style="width:112px">预期风险</th>' +
      '<th>用例说明</th></tr></thead><tbody>' +
      d.samples.map(s => {
        const e = s.expect || {};
        const lv = e.blocked ? 'none' : e.risk_level;
        return '<tr><td class="mono">' + CR.esc(s.code) + '</td>' +
          '<td class="mono" style="font-size:12px">' + CR.esc(s.file) + '</td>' +
          '<td>' + CR.esc(s.format) + '</td>' +
          '<td>' + CR.esc(BTYPE[s.business_type] || s.business_type) + '</td>' +
          '<td>' + (e.blocked ? '<span class="tag t-high">解析受阻</span>'
            : CR.riskTag(lv, lvLabel(lv))) + '</td>' +
          '<td class="muted" style="font-size:12.5px">' + CR.esc(e.note || '') +
          '</td></tr>';
      }).join('') + '</tbody></table></div>';
    samplesLoaded = true;
  }
}

/* ------------------------------------------------------------------ 上传 */
async function openUpload() {
  FILE = null;
  CR.$('#fileName').textContent = '';
  CR.$('#upHint').textContent = '支持 DOCX / PDF / PNG / JPG　单文件不超过 ' +
    CFG.max_upload_mb + ' MB';
  CR.openModal('mUpload');
}

function pickFile(f) {
  FILE = f;
  CR.$('#fileName').textContent = f
    ? (f.name + '  ·  ' + (f.size / 1024).toFixed(1) + ' KB') : '';
  if (f && !CR.$('#upTitle').value) {
    CR.$('#upTitle').value = f.name.replace(/\.(docx|pdf|png|jpe?g|bmp)$/i, '');
  }
}

(function () {
  const drop = CR.$('#drop');
  ['dragover', 'dragenter'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.style.borderColor = 'var(--brand)';
  }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.style.borderColor = '';
  }));
  drop.addEventListener('drop', e => {
    if (e.dataTransfer.files && e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]);
  });
})();

async function doUpload() {
  if (!FILE) return CR.toast('请先选择合同文件', 'err');
  if (FILE.size > CFG.max_upload_mb * 1024 * 1024) {
    return CR.toast('文件超过 ' + CFG.max_upload_mb + ' MB 限制', 'err');
  }
  const b = CR.$('#btnUp');
  CR.busy(b, true, '上传中');
  CR.overlay(true, '正在上传并解析…', '解析 → 条款切分 → 规则审查。');
  try {
    const buf = await FILE.arrayBuffer();
    let bin = '';
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    const content = btoa(bin);
    const d = await CR.api('POST', '/api/upload', {
      filename: FILE.name, content: content,
      meta: { business_type: CR.$('#upType').value, title: CR.$('#upTitle').value },
      auto_review: true
    });
    CR.closeModal('mUpload');
    if (d.duplicated) {
      CR.toast('文件指纹重复，已复用既有任务 #' + d.case_id, 'ok');
      await load();
    } else {
      CR.toast('上传并审查完成，任务 #' + d.case_id, 'ok');
      location.href = '/workbench?id=' + d.case_id;
    }
  } catch (e) {
    CR.toast('上传失败：' + CR.esc(e.message), 'err');
  } finally {
    CR.busy(b, false);
    CR.overlay(false);
  }
}

/* ------------------------------------------------------------------ 自检 / 指标 / 通知 */
async function runSelftest() {
  CR.openModal('mSelf');
  CR.$('#selfBody').innerHTML = CR.skeleton(8);
  try {
    const d = await CR.api('GET', '/api/selftest');
    const rows = (d.checks || []).map(c =>
      '<tr><td style="width:76px">' + (c.passed
        ? '<span class="tag t-ok">通过</span>' : '<span class="tag t-high">未通过</span>') +
      '</td><td style="width:220px;font-weight:560">' + CR.esc(c.name) + '</td>' +
      '<td class="muted" style="font-size:12.5px">' + CR.esc(c.detail || '') + '</td></tr>').join('');
    CR.$('#selfBody').innerHTML =
      '<div class="row" style="margin-bottom:12px"><span class="tag ' +
      (d.passed ? 't-ok' : 't-high') + '">' + (d.passed ? '全部通过' : '存在未通过项') +
      '</span><span class="muted">共 ' + (d.checks || []).length + ' 项验收对照，通过 ' +
      (d.passed_count || 0) + ' 项 · 耗时 ' + d.elapsed_ms + ' ms</span></div>' +
      '<div class="table-wrap"><table class="grid"><tbody>' + rows + '</tbody></table></div>';
  } catch (e) {
    CR.$('#selfBody').innerHTML = '<div class="empty">自检失败：' + CR.esc(e.message) + '</div>';
  }
}

async function openMetrics() {
  CR.openModal('mMetrics');
  CR.$('#metricsBody').innerHTML = CR.skeleton(6);
  try {
    const m = await CR.api('GET', '/api/metrics');
    const top = (m.top_paths || []).slice(0, 8);
    const maxMs = Math.max.apply(null, top.map(t => t.avg_ms).concat([1]));
    CR.$('#metricsBody').innerHTML =
      '<div class="stats" style="margin-bottom:14px">' +
      card('累计请求', m.requests, '自 ' + m.started_at + ' 起', '', 'pulse', undefined, 'var(--brand)') +
      card('平均响应', CR.fmtMs(m.avg_ms), '全部请求历史均值', '', 'clock', undefined, 'var(--cyan)') +
      card('错误数', m.errors, '错误率 ' + (m.error_rate * 100).toFixed(2) + '%',
        m.errors ? 'hl' : '', 'alert', undefined, 'var(--high)') +
      card('运行时长', Math.round(m.uptime_sec / 60) + ' 分钟', '进程启动至今', '', 'gauge', undefined, 'var(--teal)') +
      card('响应总量', (m.bytes_out / 1048576).toFixed(2) + ' MB', '压缩后实际发送', '', 'server', undefined, 'var(--brand-2)') +
      card('任务总数', m.task_total, '数据库内审查任务', '', 'db', undefined, 'var(--brand)') +
      '</div>' +
      '<div style="font-size:11.5px;color:var(--sub);margin-bottom:8px">' +
      '热点接口（按累计耗时排序）</div>' +
      top.map(t => '<div class="metric"><div class="mh"><span class="p">' + CR.esc(t.path) +
        '</span><span class="v">' + t.count + ' 次 · 均 ' + CR.fmtMs(t.avg_ms) +
        ' · 峰值 ' + CR.fmtMs(t.max_ms) + (t.errors ? ' · 错误 ' + t.errors : '') +
        '</span></div><div class="bar"><i style="width:' +
        Math.max(4, t.avg_ms / maxMs * 100) + '%"></i></div></div>').join('') +
      ((m.slow_requests || []).length
        ? '<div style="font-size:11.5px;color:var(--sub);margin:12px 0 6px">' +
          '慢请求（≥ 1s）</div><div class="log">' + m.slow_requests.map(s =>
            '<div class="r"><span class="t">' + CR.esc(s.at) + '</span>' +
            '<span class="s failed">' + s.ms + 'ms</span><span>' + CR.esc(s.path) +
            '</span></div>').join('') + '</div>'
        : '<div class="faint" style="font-size:12px;margin-top:10px">' +
          '暂无慢请求记录。审查与 OCR 属于耗时操作，正常会出现在这里。</div>');
  } catch (e) {
    CR.$('#metricsBody').innerHTML = '<div class="empty">读取指标失败：' +
      CR.esc(e.message) + '</div>';
  }
}

async function openNotify() {
  CR.openModal('mNotify');
  CR.$('#notifyBody').innerHTML = CR.skeleton(4);
  try {
    const s = await CR.api('GET', '/api/notify/status');
    const recent = s.recent || [];
    CR.$('#notifyBody').innerHTML =
      '<div class="kv-list">' +
      '<div class="r"><div class="k">启用状态</div><div class="v">' +
        (s.enabled
          ? (s.configured ? '<span class="tag t-ok">已启用且已配置</span>'
                          : '<span class="tag t-mid">已启用但未填 Webhook</span>')
          : '<span class="tag t-none">未启用</span>') + '</div></div>' +
      '<div class="r"><div class="k">渠道 / 加签</div><div class="v">' + CR.esc(s.channel) +
        ' ｜ 加签 ' + (s.has_secret ? '已配置' : '未配置') + '</div></div>' +
      '<div class="r"><div class="k">Webhook</div><div class="v mono" style="font-size:11.5px">' +
        (CR.esc(s.webhook) || '（未填写）') + '</div></div>' +
      '<div class="r"><div class="k">发送统计</div><div class="v">入队 ' + s.stats.queued +
        ' ｜ 成功 ' + s.stats.sent + ' ｜ 失败 ' + s.stats.failed +
        ' ｜ 跳过 ' + s.stats.skipped + '</div></div>' +
      '<div class="r"><div class="k">事件开关</div><div class="v">' +
        Object.keys(s.events || {}).map(k => '<span class="tag ' +
          (s.events[k] ? 't-ok' : 't-none') + '" style="margin:1px 3px 1px 0">' +
          CR.esc(k) + '</span>').join('') + '</div></div></div>' +
      (s.configured ? '' : '<div class="notice warn" style="margin-top:10px">要启用钉钉通知：' +
        '编辑 <span class="mono">config/app.json</span>，把 ' +
        '<span class="mono">notify.enabled</span> 设为 true 并填写 ' +
        '<span class="mono">notify.webhook</span>；若机器人安全设置选了「加签」，再把 ' +
        '<span class="mono">notify.secret</span> 填上 SEC 开头的密钥。</div>') +
      (recent.length ? '<div class="field" style="margin-top:10px">' +
        '<div class="lab">最近发送记录</div><div class="log">' +
        recent.map(r => '<div class="r"><span class="t">' + CR.esc(r.at) +
          '</span><span class="s ' + CR.esc(r.status === 'sent' ? 'ok'
            : (r.status === 'failed' ? 'failed' : 'skipped')) + '">' + CR.esc(r.status) +
          '</span><span>' + CR.esc(r.event_label) + ' · ' + CR.esc(r.title || '') +
          (r.detail ? ' — ' + CR.esc(r.detail) : '') + '</span></div>').join('') +
        '</div></div>' : '');
  } catch (e) {
    CR.$('#notifyBody').innerHTML = '<div class="empty">读取通知状态失败：' +
      CR.esc(e.message) + '</div>';
  }
}

async function sendNotifyTest() {
  const b = CR.$('#btnNotifyTest');
  CR.busy(b, true, '发送中');
  try {
    const r = await CR.api('POST', '/api/notify/test', { message: '测试消息已送达。' });
    CR.toast(r.status === 'sent' ? '测试通知发送成功，请查看机器人所在群'
      : ('发送结果：' + CR.esc(r.detail || '')), 'ok');
  } catch (e) {
    CR.toast('测试发送失败：' + CR.esc(e.message), 'err');
  } finally {
    CR.busy(b, false);
    openNotify();
  }
}

/* ------------------------------------------------- v2.0 权限矩阵与新增面板 */
const CAP_ROLE = {
  view_cases: ['legal', 'applicant', 'admin'],
  view_detail: ['legal', 'applicant', 'admin'],
  export_report: ['legal', 'applicant', 'admin'],
  submit_contract: ['applicant', 'admin'],
  pull_todo: ['applicant', 'admin'],
  edit_suggestion: ['legal'],
  add_opinion: ['legal'],
  writeback: ['legal'],
  rerun_review: ['legal', 'admin'],
  retry_blocked: ['admin'],
  edit_rules: ['admin'],
  export_audit: ['legal', 'admin'],
  notify_test: ['admin'],
  fault_inject: ['admin'],
  reset_data: ['admin'],
  run_selftest: ['admin'],
  view_metrics: ['admin'],
  view_users: ['admin'],
  manage_scheduler: ['admin']
};

function openAuth() {
  CR.openModal('mAuth');
  const me = CR.me() || {};
  const roles = me.roles || [];
  const caps = Object.keys(CAP_ROLE);
  const head = '<tr><th>权限能力</th><th>说明</th>' +
    roles.map(r => '<th style="text-align:center">' + CR.esc(r.label) + '</th>').join('') +
    '</tr>';
  const body = caps.map(function (c) {
    const allow = CAP_ROLE[c];
    return '<tr><td><code style="font-size:11.4px">' + c + '</code></td>' +
      '<td>' + CR.esc((me.caps_label || {})[c] || c) + '</td>' +
      roles.map(function (r) {
        const yes = allow.indexOf(r.role) >= 0;
        const mine = r.role === me.role;
        return '<td class="c ' + (yes ? 'perm-yes' : 'perm-no') + '">' +
          (yes ? '✓' : '—') + (mine ? '<div class="faint" style="font-size:10px">当前</div>' : '') +
          '</td>';
      }).join('') + '</tr>';
  }).join('');
  CR.$('#authBody').innerHTML =
    '<div class="kv-grid" style="margin-bottom:14px">' +
    '<div class="kv-cell"><div class="k">当前身份</div><div class="v" style="font-size:13.5px">' +
    CR.esc(me.role_label || '-') + ' <small>' + CR.esc(me.username || '') + '</small></div></div>' +
    '<div class="kv-cell"><div class="k">会话到期</div><div class="v" style="font-size:12.5px">' +
    CR.esc(me.session_expires_at || '-') + '</div></div>' +
    '<div class="kv-cell"><div class="k">已授权能力</div><div class="v">' +
    (me.caps || []).length + ' <small>/ ' + caps.length + '</small></div></div>' +
    '<div class="kv-cell"><div class="k">演示身份切换</div><div class="v" style="font-size:13px">' +
    (me.demo_switch ? '已开启' : '已关闭') + '</div></div>' +
    '</div>' +
    '<div style="font-size:11.5px;color:var(--sub);margin-bottom:8px">' +
    '角色能力矩阵 · 勾选为允许。权限的最终执行点在服务端路由表，前端仅做显隐。</div>' +
    '<table class="perm-tbl">' + head + body + '</table>' +
    '<div class="mode-note">' + CR.esc(me.role_desc || '') + '</div>';
  CR.$('#authFoot').textContent =
    '越权请求返回 403 并写入审计（tool=auth.guard）；未登记的写接口按失败关闭处理。';
}

/* ---------------- 自动流转（定时拉取） ---------------- */
async function loadSched() {
  try {
    const s = await CR.api('GET', '/api/scheduler');
    const sw = CR.$('#swSched');
    if (sw) { sw.classList.toggle('on', !!s.enabled); sw.setAttribute('aria-checked', String(!!s.enabled)); }
    CR.$('#schedSub').textContent = s.enabled
      ? '运行中 · 每 ' + s.interval_sec + ' 秒拉取一次（退避上限 ' + s.max_interval_sec + ' 秒）'
      : '默认关闭 —— 否则演示数据会在无人操作时自己变化';
    const nxt = s.next_run_in_sec > 0
      ? Math.floor(s.next_run_in_sec / 60) + ' 分 ' + (s.next_run_in_sec % 60) + ' 秒后'
      : '—';
    CR.$('#schedKv').innerHTML =
      kv('累计运行', s.runs + ' 轮', s.manual_runs + ' 轮为手动触发') +
      kv('本轮间隔', s.current_interval + 's', '退避倍数 ' + s.backoff_factor + '×') +
      kv('下一轮', nxt, s.next_run_at || '未排程') +
      kv('累计新建', s.created_total + ' 个', '自动审查 ' + s.reviewed_total + ' 个') +
      kv('连续失败', s.consecutive_failures + ' 次', s.last_run_at || '尚未运行');
    CR.$('#schedNote').innerHTML = s.last_error
      ? '<span style="color:var(--high)">最近一次失败：' + CR.esc(s.last_error) + '</span>'
      : '连续失败后间隔按退避倍数递增并封顶；成功一次即复位。命中「拉取失败」时会在审计里留痕。';
  } catch (e) {
    CR.$('#schedKv').innerHTML = '<div class="empty" style="grid-column:1/-1">读取调度状态失败：' +
      CR.esc(e.message) + '</div>';
  }
}

function kv(k, v, sub) {
  return '<div class="kv-cell"><div class="k">' + CR.esc(k) + '</div><div class="v">' +
    CR.esc(v) + (sub ? '<small>' + CR.esc(sub) + '</small>' : '') + '</div></div>';
}

async function toggleSched(force) {
  const sw = CR.$('#swSched');
  const target = (typeof force === 'boolean') ? force : !sw.classList.contains('on');
  try {
    const r = await CR.api('POST', '/api/scheduler/config', { enabled: target });
    CR.toast(target ? '已开启自动拉取' : '已关闭自动拉取', 'ok');
    if (r && r.status) { loadSched(); return; }
  } catch (e) {
    CR.toast('设置失败：' + e.message, 'err');
  }
  loadSched();
}

async function schedRunNow() {
  const b = CR.$('#btnSchedRun');
  CR.busy(b, true, '拉取中…');
  try {
    const r = await CR.api('POST', '/api/scheduler/run', {});
    const t = r.trigger_result || {};
    CR.toast('本轮：新建 ' + (t.created || 0) + ' / 跳过 ' + (t.skipped || 0) +
      ' / 审查 ' + (t.reviewed || 0), t.ok ? 'ok' : 'err');
    await load();
  } catch (e) {
    CR.toast('拉取失败：' + e.message, 'err');
  } finally {
    CR.busy(b, false);
    loadSched();
  }
}

/* ---------------- 审查队列与配额 ---------------- */
async function loadQueue() {
  try {
    const q = await CR.api('GET', '/api/queue');
    const pct = function (a, b) { return b > 0 ? Math.min(100, a / b * 100) : 0; };
    CR.$('#qWorkers').textContent = '并发度 ' + q.workers + ' · 队列上限 ' + q.maxsize;
    CR.$('#queueKv').innerHTML =
      kv('工作线程', q.workers + ' 个', q.enabled ? '队列已启用' : '队列已关闭') +
      kv('排队中', q.queued + ' 个', '在飞 ' + (q.inflight || []).length + ' 个') +
      kv('待处理案件', q.pending_cases + ' 个', '上限 ' + q.max_pending) +
      kv('附件占用', q.uploads_mb + ' MB', '上限 ' + q.max_total_mb + ' MB') +
      '<div class="kv-cell" style="grid-column:1/-1"><div class="k">队列水位</div>' +
      '<div class="v" style="font-size:13px">' + q.queued + ' / ' + q.maxsize + '</div>' +
      '<div class="qbar"><i style="width:' + pct(q.queued, q.maxsize) + '%"></i></div></div>' +
      '<div class="kv-cell" style="grid-column:1/-1"><div class="k">附件存储水位</div>' +
      '<div class="v" style="font-size:13px">' + q.uploads_mb + ' / ' + q.max_total_mb + ' MB</div>' +
      '<div class="qbar"><i style="width:' + pct(q.uploads_mb, q.max_total_mb) + '%"></i></div></div>';
  } catch (e) {
    CR.$('#queueKv').innerHTML = '<div class="empty" style="grid-column:1/-1">读取队列状态失败：' +
      CR.esc(e.message) + '</div>';
  }
}

/* ------------------------------------------------------------------ 启动 */
(async function () {
  let envText = '演示环境';
  let envDetail = '';
  try {
    const [h, c] = await Promise.all([
      CR.api('GET', '/api/health'), CR.api('GET', '/api/config')
    ]);
    CFG = c || CFG;
    // 顶栏只放最必要的信息（太长会把按钮挤到第二行）；
    // 完整信息放 title，鼠标悬停可看。
    envText = '演示环境 · v' + h.version;
    envDetail = '审批通道：' +
      (h.approval_channel === 'feishu_mock' ? '内置模拟审批' : String(h.approval_base)) +
      ' ｜ OCR：' + h.ocr_backend +
      ' ｜ PDF：' + (h.pdf_channels && h.pdf_channels.effective === 'stdlib'
        ? '服务端标准库直出' : '浏览器无头打印') +
      ' ｜ 版本：v' + h.version;
  } catch (e) { }
  // 先取当前身份与能力，再渲染顶栏并执行权限显隐（隐藏只是体验，拦截在服务端）
  try { await CR.initAuth('/', envText, envDetail); }
  catch (e) { CR.renderTopbar('/', envText, envDetail); }
  await load();
  loadSched();
  loadQueue();
  setInterval(loadQueue, 5000);
  setInterval(loadSched, 15000);
  CR.initReveal();
})();
