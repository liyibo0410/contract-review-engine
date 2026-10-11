
let RULES = null;      // 当前类型的规则库
let CUR = 'purchase';
let EDIT_IDX = -1;
let DIRTY = false;

CR.motion.enter();
CR.initAuth('/rules', '规则维护 · 修改前自动备份 .bak');

async function loadRules(type) {
  CUR = type || CUR;
  RULES = await CR.api('GET', '/api/rules?type=' + CUR);
  if (DIRTY && !confirm('当前规则库有未保存的修改，切换类型将放弃这些修改，是否继续？')) return;
  DIRTY = false;
  renderAll();
  CR.initReveal();
}

function renderAll() {
  const types = RULES.types || [];
  const cur = RULES.current;
  const labels = { purchase: '采购合同', sales: '销售合同', service: '服务合同', labor: '劳动合同' };
  const tIcon = { purchase: 'layers', sales: 'scale', service: 'workflow', labor: 'users' };
  CR.$('#typeList').innerHTML = types.map(t =>
    '<div class="row clickable" style="padding:8px 10px;border-radius:8px;margin-bottom:5px;' +
    'transition:background .18s;' +
    (t === cur ? 'background:linear-gradient(135deg,rgba(79,140,255,.16),rgba(124,92,255,.10));' +
      'box-shadow:inset 0 0 0 1px rgba(79,140,255,.28);color:var(--ink);font-weight:650'
             : 'color:var(--sub)') +
    '" onclick="loadRules(\'' + t + '\')" onmouseover="this.style.background=\'var(--panel-3)\'"' +
    ' onmouseout="this.style.background=\'' +
    (t === cur ? 'linear-gradient(135deg,rgba(79,140,255,.16),rgba(124,92,255,.10))' : '') + '\'">' +
    CR.icon(tIcon[t] || 'doc', '', 15) +
    '<span>' + CR.esc(labels[t] || t) + '</span><div class="spacer" style="flex:1"></div>' +
    '<span class="mono faint" style="font-size:11px">' + CR.esc(t) + '.json</span></div>').join('');

  // 规则统计：数字 + 等级分布条（数据全部来自规则库本身）
  const s = RULES.stats || {};
  CR.$('#ruleStats').innerHTML =
    '<div class="row" style="gap:16px;margin-bottom:12px">' +
    '<div><div class="faint" style="font-size:11.5px">规则总数</div>' +
    '<div class="num" style="font-size:24px;font-weight:800" data-count="' + (s.count || 0) + '">' + (s.count || 0) + '</div></div>' +
    '<div><div class="faint" style="font-size:11.5px">高风险</div>' +
    '<div class="num" style="font-size:24px;font-weight:800;color:var(--high)" data-count="' + (s.high || 0) + '">' + (s.high || 0) + '</div></div>' +
    '</div>' +
    '<div id="ruleBars"></div>' +
    '<div class="faint" style="font-size:11.5px;margin-top:10px">匹配方式：' +
      CR.esc((s.modes || []).join(' / ')) + '</div>';
  const total = (s.high || 0) + (s.medium || 0) + (s.low || 0) || 1;
  CH.hbars(CR.$('#ruleBars'), [
    { label: '高风险', value: s.high || 0, display: (s.high || 0) + ' 条',
      color: 'linear-gradient(90deg,var(--high),#ff9d6b)' },
    { label: '中风险', value: s.medium || 0, display: (s.medium || 0) + ' 条',
      color: 'linear-gradient(90deg,var(--mid),#ffe08a)' },
    { label: '低风险', value: s.low || 0, display: (s.low || 0) + ' 条',
      color: 'linear-gradient(90deg,var(--low),var(--cyan))' }
  ], { max: total });

  CR.$$('#ruleStats [data-count]').forEach(el => {
    CR.countUp(el, parseFloat(el.dataset.count) || 0, { duration: 1200 });
  });

  const ct = RULES.clause_types || { types: [] };
  CR.$('#ctCount').textContent = ct.types.length + ' 类';
  CR.$('#ctList').innerHTML = ct.types.map(t =>
    '<div style="padding:7px 0;border-bottom:1px dashed var(--line-2)">' +
    '<div style="font-size:12.5px;font-weight:600">' + CR.esc(t.label) +
    ' <span class="faint mono" style="font-size: 11px">' + CR.esc(t.code) + '</span></div>' +
    '<div class="faint" style="font-size:11.5px">' + CR.esc(t.keywords.join('、')) + '</div></div>').join('');

  const rd = RULES.rules || {};
  CR.$('#curTypeLabel').innerHTML = CR.icon('rules', '', 15) + ' ' +
    CR.esc(rd.type_label || cur) + ' · 审查规则清单';
  CR.$('#curStance').textContent = rd.stance ? ('审查立场：' + rd.stance) : '';
  CR.$('#verInfo').textContent = '规则版本 ' + (rd.version || '-') + ' · 共 ' + ((rd.rules || []).length) + ' 条';

  CR.$('#ruleBody').innerHTML = (rd.rules || []).map((r, i) => {
    const pats = (r.patterns || []).concat(r.patterns_alt || []);
    return '<tr class="rule-row">' +
      '<td class="mono" style="font-size:11.5px">' + CR.esc(r.id) + '</td>' +
      '<td style="font-weight:600">' + CR.esc(r.name) + '</td>' +
      '<td>' + CR.riskTag(r.risk_level, r.risk_level === 'high' ? '高' : r.risk_level === 'medium' ? '中' : '低') + '</td>' +
      '<td style="font-size:12px">' + CR.esc(r.clause_type) + '</td>' +
      '<td><span class="tag t-plain mono" style="font-size:11px">' + CR.esc(r.mode) + '</span></td>' +
      '<td class="cond">' + pats.map(p => CR.esc(p)).join('<br>') +
        (r.mode === 'numeric' ? '<div class="faint">阈值：' + CR.esc(r.numeric_op) + ' ' + CR.esc(r.numeric_threshold) + '</div>' : '') +
        '</td>' +
      '<td class="right"><button class="btn sm" onclick="editRule(' + i + ')">编辑</button></td>' +
      '</tr>';
  }).join('');

  const lib = (RULES.library || {}).library || {};
  const keys = Object.keys(lib);
  CR.$('#libBox').innerHTML = keys.length ? keys.map(k =>
    '<div style="margin-bottom:12px;padding-bottom:12px;border-bottom:1px dashed var(--line-2)">' +
    '<div class="mono" style="font-size:11.5px;color:var(--cyan)">' + CR.esc(k) + '</div>' +
    lib[k].map(o => '<div style="margin-top:6px"><b style="font-size:12.5px">' + CR.esc(o.title) + '</b>' +
      '<div class="quote">' + CR.esc(o.text) + '</div>' +
      '<div class="faint" style="font-size:11.5px;margin-top:3px">' + CR.esc(o.note || '') + '</div></div>').join('') +
    '</div>').join('')
    : '<div class="muted" style="font-size:12.5px">该类型暂无专属示范条款，命中规则时将直接采用规则内置的默认建议条款。</div>';
}

/* ------------------------------------------------------------------ 编辑 */
function editRule(i) {
  const r = RULES.rules.rules[i];
  EDIT_IDX = i;
  CR.$('#mRuleTitle').textContent = '编辑规则 ' + r.id;
  CR.$('#eId').value = r.id || '';
  CR.$('#eName').value = r.name || '';
  CR.$('#eRisk').value = r.risk_level || 'medium';
  CR.$('#eMode').value = r.mode || 'regex';
  CR.$('#eClause').value = r.clause_type || '';
  CR.$('#ePatterns').value = (r.patterns || []).join('\n');
  CR.$('#eAlt').value = (r.patterns_alt || []).join('\n');
  CR.$('#eOp').value = r.numeric_op || 'gt';
  CR.$('#eTh').value = (r.numeric_threshold === undefined ? '' : r.numeric_threshold);
  CR.$('#eReason').value = r.reason || '';
  CR.$('#eBasis').value = r.legal_basis || '';
  CR.$('#eSugg').value = r.suggestion_default || '';
  CR.$('#mRule').querySelector('.btn.danger').style.visibility = 'visible';
  onModeChange();
  CR.openModal('mRule');
}
function onModeChange() {
  const m = CR.$('#eMode').value;
  CR.$('#numRow').style.display = (m === 'numeric') ? 'flex' : 'none';
  CR.$('#eAlt').parentElement.style.display = (m === 'pair') ? 'block' : 'none';
}
document.addEventListener('change', function (e) { if (e.target && e.target.id === 'eMode') onModeChange(); });

function addRule() {
  EDIT_IDX = -1;
  CR.$('#mRuleTitle').textContent = '新增审查规则';
  ['eId', 'eName', 'eClause', 'ePatterns', 'eAlt', 'eTh', 'eReason', 'eBasis', 'eSugg'].forEach(id => CR.$('#' + id).value = '');
  CR.$('#eRisk').value = 'medium';
  CR.$('#eMode').value = 'regex';
  CR.$('#mRule').querySelector('.btn.danger').style.visibility = 'hidden';
  onModeChange();
  CR.openModal('mRule');
}

function applyRule() {
  const lines = v => v.split('\n').map(s => s.trim()).filter(Boolean);
  const r = {
    id: CR.$('#eId').value.trim(), name: CR.$('#eName').value.trim(),
    risk_level: CR.$('#eRisk').value, clause_type: CR.$('#eClause').value.trim(),
    mode: CR.$('#eMode').value, patterns: lines(CR.$('#ePatterns').value),
    reason: CR.$('#eReason').value.trim(), legal_basis: CR.$('#eBasis').value.trim(),
    suggestion_default: CR.$('#eSugg').value.trim()
  };
  if (!r.id || !r.name || !r.patterns.length) return CR.toast('规则编号、名称、触发条件均为必填', 'err');
  const alt = lines(CR.$('#eAlt').value);
  if (r.mode === 'pair') { if (!alt.length) return CR.toast('pair 模式必须填写相对方一侧的触发条件', 'err'); r.patterns_alt = alt; }
  if (r.mode === 'numeric') {
    r.numeric_op = CR.$('#eOp').value;
    r.numeric_threshold = Number(CR.$('#eTh').value || 0);
  }
  // 保存前先做一次正则可编译性校验
  if (r.mode !== 'keyword') {
    const bad = (r.patterns || []).concat(r.patterns_alt || [])
      .map(p => checkPattern(p)).filter(Boolean);
    if (bad.length) return CR.toast('正则写法有误：' + CR.esc(bad[0]), 'err');
  }
  if (EDIT_IDX >= 0) RULES.rules.rules[EDIT_IDX] = r; else RULES.rules.rules.push(r);
  DIRTY = true;
  CR.closeModal('mRule');
  renderAll();
  CR.toast('已修改（尚未落盘），点右上角「保存规则库」写入配置文件', 'ok');
}

function delRule() {
  if (EDIT_IDX < 0) return;
  const r = RULES.rules.rules[EDIT_IDX];
  if (!confirm('确认删除规则 ' + r.id + ' ？')) return;
  RULES.rules.rules.splice(EDIT_IDX, 1);
  DIRTY = true;
  CR.closeModal('mRule');
  renderAll();
}

/* 正则可编译性校验：写坏的正则在后端会被跳过并记入诊断，
   这里在保存前就拦下来，避免"改了规则却静默失效"。 */
function checkPattern(pat) {
  try { new RegExp(pat); return ''; }
  catch (e) { return e.message; }
}

function validateRules(rules) {
  const bad = [];
  rules.forEach(r => {
    const pats = (r.patterns || []).concat(r.patterns_alt || []);
    pats.forEach(p => {
      // keyword 模式会把关键词转义处理，不需要按正则校验
      if (r.mode === 'keyword') return;
      const err = checkPattern(p);
      if (err) bad.push({ id: r.id, pattern: p, error: err });
    });
  });
  return bad;
}

async function saveRules() {
  const bad = validateRules(RULES.rules.rules);
  if (bad.length) {
    const msg = bad.slice(0, 3).map(b => b.id + ' → ' + b.error).join('；');
    if (!confirm('检测到 ' + bad.length + ' 处正则写法有问题，这些规则在审查时会被跳过：\n\n' +
                 msg + '\n\n仍要保存吗？（建议先修正）')) return;
  }
  try {
    const d = await CR.api('POST', '/api/rules/' + CUR, {
      rules: RULES.rules.rules, version: RULES.rules.version
    });
    DIRTY = false;
    CR.toast('规则库已保存（写入 config/rules/' + CUR + '.json，原文件备份为 .bak），共 ' +
      d.count + ' 条规则', 'ok');
    await loadRules(CUR);
  } catch (e) { CR.toast('保存失败：' + CR.esc(e.message), 'err'); }
}

window.addEventListener('beforeunload', function (e) {
  if (DIRTY) { e.preventDefault(); e.returnValue = ''; }
});

loadRules('purchase');
