/* ============================================================================
   合规规则与模板页
   ========================================================================== */
'use strict';

const R = { tree: [], flat: [], current: null, templates: [] };
const $ = (id) => document.getElementById(id);

async function load() {
  const [t, tpl] = await Promise.all([
    HJ.api('/api/rules/tree'),
    HJ.api('/api/templates'),
  ]);
  R.tree = t.tree || [];
  R.flat = R.tree.flatMap(g => g.rules);
  R.templates = tpl.items || [];

  const s = t.stats;
  $('ruleStats').innerHTML = `
    <div class="stat"><div class="k">规则总数</div><div class="v">${s.total}</div>
      <div class="d">版本 v${HJ.esc(s.version)} · 更新 ${HJ.esc(s.updated_at)}</div></div>
    <div class="stat"><div class="k">高风险规则</div><div class="v high">${s.by_level.high || 0}</div>
      <div class="d">直接决定审查结论</div></div>
    <div class="stat"><div class="k">中风险规则</div><div class="v medium">${s.by_level.medium || 0}</div>
      <div class="d">建议谈判修正</div></div>
    <div class="stat"><div class="k">示范条款</div><div class="v ok">${s.template_count}</div>
      <div class="d">可被风险项直接引用</div></div>`;
  $('ruleVer').textContent = `v${s.version}`;
  renderTree();
  renderTemplates();
}

function renderTree() {
  const lv = $('fLevel').value;
  const kw = $('fKw').value.trim().toLowerCase();
  let groups = R.tree.map(g => ({
    ...g,
    rules: g.rules.filter(r =>
      (!lv || r.level === lv) &&
      (!kw || (r.id + r.title + (r.tags || []).join('')).toLowerCase().includes(kw))),
  })).filter(g => g.rules.length);

  if (!groups.length) { $('tree').innerHTML = '<div class="empty">无匹配规则</div>'; return; }

  $('tree').innerHTML = groups.map((g, gi) => `
    <div class="grp">
      <div class="gh" data-g="${gi}">
        <span>${HJ.esc(g.label)}</span>
        <span class="small muted">${g.rules.length} 条 ▾</span>
      </div>
      <div class="rl">
        ${g.rules.map(r => `
          <div style="padding:5px 0;cursor:pointer" data-rule="${HJ.esc(r.id)}">
            <span class="id">${HJ.esc(r.id)}</span>
            <span class="tag ${r.level}" style="font-size:10px">${HJ.RISK[r.level]}</span>
            <span style="margin-left:6px">${HJ.esc(r.title)}</span>
          </div>`).join('')}
      </div>
    </div>`).join('');

  $('tree').querySelectorAll('[data-rule]').forEach(el => el.onclick = () => {
    const r = R.flat.find(x => x.id === el.dataset.rule);
    R.current = r; renderDetail(r);
    [...$('tree').querySelectorAll('[data-rule]')].forEach(x =>
      x.style.background = x === el ? 'var(--bg-3)' : '');
  });
}

function renderDetail(r) {
  if (!r) return;
  const p = r.params || {};
  $('ruleDetail').innerHTML = `
    <div class="card-title"><span class="em">${HJ.esc(r.title)}</span>
      <span class="tag ${r.level}">${HJ.RISK[r.level]}</span>
      <span class="tag plain">${HJ.esc(r.type)}</span>
      <span class="small muted mono">${HJ.esc(r.id)}</span></div>

    <div class="grid c2" style="gap:12px">
      <div>
        <label class="f">规则等级</label>
        <select id="edLevel">
          ${['high', 'medium', 'low'].map(l => `<option value="${l}" ${l === r.level ? 'selected' : ''}>${HJ.RISK[l]}</option>`).join('')}
        </select>
        <label class="f" style="margin-top:10px">条款类型</label>
        <select id="edClause">
          ${Object.keys(HJ.CLAUSE).map(k => `<option value="${k}" ${k === r.clause_type ? 'selected' : ''}>${HJ.CLAUSE[k]}</option>`).join('')}
        </select>
        <label class="f" style="margin-top:10px">关联示范条款</label>
        <div class="mono small" style="padding:7px 10px;background:var(--bg);border-radius:5px">
          ${HJ.esc(r.advice_ref || '—')}</div>
      </div>
      <div>
        <label class="f">风险成因（审查意见中展示）</label>
        <textarea id="edReason" style="min-height:74px">${HJ.esc(r.reason || '')}</textarea>
        <label class="f" style="margin-top:10px">法律依据</label>
        <textarea id="edBasis" style="min-height:52px">${HJ.esc(r.legal_basis || '')}</textarea>
      </div>
    </div>

    <label class="f" style="margin-top:12px">判定参数（YAML · 只读展示）</label>
    <pre class="code">${HJ.esc(JSON.stringify(p, null, 2))}</pre>

    <div class="row mt" style="gap:8px">
      <button class="btn primary" id="btnSave">保存修改（升级规则版本）</button>
      <span class="small muted">保存后请到「评测与观测」页跑一遍黄金样例集，确认命中率未退化。</span>
    </div>`;
  $('btnSave').onclick = async () => {
    try {
      const res = await HJ.api('/api/rules/' + r.id, {
        method: 'PUT',
        body: {
          level: $('edLevel').value,
          clause_type: $('edClause').value,
          reason: $('edReason').value,
          legal_basis: $('edBasis').value,
        },
      });
      HJ.toast(`规则 ${r.id} 已保存，当前规则版本 ${res.version}`, 'ok', 5000);
      await load();
      R.current = R.flat.find(x => x.id === r.id);
      renderDetail(R.current);
    } catch (e) { HJ.toast('保存失败：' + e.message, 'err'); }
  };
}

function renderTemplates() {
  $('tplHint').textContent = `共 ${R.templates.length} 条`;
  $('tplBox').innerHTML = R.templates.map(t => `
    <div class="card" style="background:var(--bg-2)">
      <div class="row between" style="margin-bottom:6px">
        <strong style="font-size:13px">${HJ.esc(t.title)}</strong>
        <span class="mono small muted">${HJ.esc(t.id)}</span>
      </div>
      <div class="small muted" style="margin-bottom:8px">
        ${HJ.esc(HJ.CLAUSE[t.clause_type] || t.clause_type)}</div>
      <div class="ev small" style="border-left:2px solid var(--line-2);padding-left:8px;color:var(--text-2)">
        ${HJ.esc(t.text)}</div>
      ${t.note ? `<div class="small muted mt">${HJ.esc(t.note)}</div>` : ''}
    </div>`).join('');
}

(function init() {
  HJ.mountTopbar('rules');
  $('fLevel').onchange = renderTree;
  $('fKw').oninput = renderTree;
  load();
})();
