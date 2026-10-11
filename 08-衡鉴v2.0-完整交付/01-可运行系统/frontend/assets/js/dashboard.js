/* ============================================================================
   审查大盘页
   ========================================================================== */
'use strict';

const state = { items: [], selected: null, timer: null, es: null };

const $ = (id) => document.getElementById(id);

async function loadStats() {
  const o = await HJ.api('/api/stats/overview');
  const cards = [
    { k: '审查任务总数', v: o.total, d: `已完成 ${o.completed} · 受阻 ${o.blocked}` },
    { k: '风险命中总数', v: o.findings, cls: o.high_findings ? 'high' : 'ok',
      d: `其中高风险 ${o.high_findings} 条` },
    { k: '解析成功率', v: (o.parse_success_rate * 100).toFixed(0) + '%',
      cls: o.parse_success_rate >= 0.99 ? 'ok' : 'medium', d: `平均耗时 ${o.avg_parse_ms} ms` },
    { k: '回写状态', v: (o.by_writeback.success || 0),
      cls: 'ok',
      d: `预览 ${(o.by_writeback.not_written || 0)} · 失败 ${(o.by_writeback.failed || 0)}` },
  ];
  $('stats').innerHTML = cards.map(c => `
    <div class="stat">
      <div class="k">${c.k}</div>
      <div class="v ${c.cls || ''}">${c.v}</div>
      <div class="d">${c.d}</div>
    </div>`).join('');

  const dist = [
    ['pending', '待处理'], ['parsing', '解析中'], ['reviewing', '审查中'],
    ['blocked', '解析受阻'], ['completed', '审查完成'],
  ].map(([k, label]) => {
    const n = (o.by_status || {})[k] || 0;
    const pct = o.total ? (n / o.total * 100) : 0;
    return `<div style="margin-bottom:8px">
      <div class="row between" style="font-size:12px">
        <span>${label}</span><span class="mono">${n}</span></div>
      <div class="pbar"><i style="width:${pct.toFixed(1)}%;background:${
        k === 'blocked' ? 'var(--high)' : k === 'completed' ? 'var(--ok)' : 'var(--brand)'}"></i></div>
    </div>`;
  }).join('');
  $('statusDist').innerHTML = dist;
}

async function loadList(keepSel) {
  const p = new URLSearchParams();
  if ($('fStatus').value) p.set('status', $('fStatus').value);
  if ($('fRisk').value) p.set('risk', $('fRisk').value);
  if ($('kw').value.trim()) p.set('keyword', $('kw').value.trim());
  const r = await HJ.api('/api/contracts?' + p.toString());
  state.items = r.items || [];
  const tb = $('tbody');
  $('empty').style.display = state.items.length ? 'none' : 'block';
  $('listHint').textContent = `共 ${state.items.length} 条`;

  tb.innerHTML = state.items.map(it => {
    const counts = `${it.high_cnt}/${it.medium_cnt}/${it.low_cnt}`;
    return `<tr data-id="${it.id}" class="${state.selected === it.id ? 'sel' : ''}">
      <td>
        <div style="font-weight:600;color:var(--text)">${HJ.esc(it.name)}</div>
        <div class="small muted mono">${HJ.esc(it.code)} · ${HJ.esc(it.file_type || '-')}</div>
      </td>
      <td><span class="tag plain">${HJ.esc(it.biz_type || '其他')}</span></td>
      <td style="text-align:right" class="mono">${HJ.money(it.amount, it.currency)}</td>
      <td>${HJ.statusTag(it.status)}</td>
      <td>${HJ.riskTag(it.risk_level, it.risk_level === 'none' ? null : '')}</td>
      <td class="mono small">${it.high_cnt || it.medium_cnt || it.low_cnt ? counts : '—'}</td>
      <td class="small muted mono">${HJ.dt(it.created_at)}</td>
    </tr>`;
  }).join('');

  [...tb.querySelectorAll('tr')].forEach(tr => {
    tr.addEventListener('click', () => selectTask(Number(tr.dataset.id)));
  });
  if (!keepSel && state.items.length && state.selected == null) {
    selectTask(state.items[0].id);
  }
}

function renderPipeline(events, status) {
  const tl = $('pipeTl');
  if (!events.length && !status) {
    tl.innerHTML = '<div class="muted small">暂无事件</div>';
    return;
  }
  const items = events.map((e, i) => {
    const last = i === events.length - 1;
    const cls = e.level === 'error' ? 'err' : (last && !['completed'].includes(e.stage) ? 'cur' : 'done');
    return `<div class="it ${cls}">
      <div class="m">${HJ.esc(e.message)}</div>
      <div class="s">${HJ.esc(e.ts)} · ${HJ.esc(e.stage)}</div>
    </div>`;
  });
  tl.innerHTML = items.join('');
  tl.scrollTop = tl.scrollHeight;
}

async function selectTask(id) {
  state.selected = id;
  [...document.querySelectorAll('#tbody tr')].forEach(tr =>
    tr.classList.toggle('sel', Number(tr.dataset.id) === id));
  $('pipeHint').textContent = '正在跟踪任务进度…';
  if (state.es) { state.es.close(); state.es = null; }

  let detail = null;
  try { detail = await HJ.api('/api/contracts/' + id); } catch (e) { /* 任务可能刚被删 */ }
  const status = detail && detail.task ? detail.task.status : '';
  $('pipeBar').style.width = ({ pending: 4, parsing: 30, reviewing: 70, blocked: 100, completed: 100 }[status] || 0) + '%';

  if (['pending', 'parsing', 'reviewing'].includes(status)) {
    state.es = HJ.streamEvents(id, (ev) => {
      $('pipeBar').style.width = (ev.progress || 0) + '%';
      const box = $('pipeTl');
      const div = document.createElement('div');
      div.className = 'it ' + (ev.level === 'error' ? 'err' : 'cur');
      div.innerHTML = `<div class="m">${HJ.esc(ev.message)}</div>
        <div class="s">${HJ.esc(ev.ts)} · ${HJ.esc(ev.stage)}</div>`;
      if (box.querySelector('.muted')) box.innerHTML = '';
      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
      if (['completed', 'blocked', 'error'].includes(ev.stage)) {
        state.es.close(); state.es = null;
        setTimeout(() => { loadList(true); loadStats(); }, 500);
      }
    });
    $('pipeTl').innerHTML = '<div class="it cur"><div class="m">等待流水线事件…</div></div>';
  } else {
    const a = await HJ.api(`/api/contracts/${id}/audit?limit=60`);
    renderPipeline((a.items || []).map(x => ({
      message: `${x.step}（${x.level}）`, ts: HJ.dt(x.created_at), stage: x.step,
      level: x.level,
    })), status);
    $('pipeBar').style.width = (status === 'completed' || status === 'blocked' ? 100 : 0) + '%';
  }
}

// ------------------------------------------------------------------ 上传
async function doUpload(file) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('name', $('upName').value.trim());
  fd.append('applicant', $('upApplicant').value.trim());
  fd.append('dept', $('upDept').value.trim());
  fd.append('biz_type', $('upBiz').value);
  fd.append('auto_run', 'true');
  HJ.toast(`正在上传 ${file.name}…`);
  try {
    const r = await HJ.api('/api/contracts/upload', { method: 'POST', form: fd });
    HJ.toast(r.message || '任务已创建', 'ok');
    await loadList();
    selectTask(r.contract_id);
  } catch (e) {
    HJ.toast('上传失败：' + e.message, 'err', 6000);
  }
}

function bind() {
  $('btnRefresh').onclick = () => { loadList(true); loadStats(); };
  $('fStatus').onchange = () => loadList(true);
  $('fRisk').onchange = () => loadList(true);
  let t = null;
  $('kw').oninput = () => { clearTimeout(t); t = setTimeout(() => loadList(true), 300); };

  $('drop').onclick = () => $('file').click();
  $('file').onchange = (e) => { if (e.target.files[0]) doUpload(e.target.files[0]); };
  ['dragenter', 'dragover'].forEach(ev => $('drop').addEventListener(ev, (e) => {
    e.preventDefault(); $('drop').classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => $('drop').addEventListener(ev, (e) => {
    e.preventDefault(); $('drop').classList.remove('over');
  }));
  $('drop').addEventListener('drop', (e) => {
    const f = e.dataTransfer.files[0];
    if (f) doUpload(f);
  });

  $('btnPull').onclick = async () => {
    try {
      const r = await HJ.api('/api/contracts/pull', { method: 'POST', body: { count: 3, auto_run: true } });
      HJ.toast(`已从审批系统拉取 ${r.count} 份待办合同`, 'ok');
      await loadList(true); loadStats();
    } catch (e) { HJ.toast('拉取失败：' + e.message, 'err', 6000); }
  };
}

(async function init() {
  HJ.mountTopbar('dashboard');
  bind();
  await loadStats();
  await loadList();
  state.timer = setInterval(() => {
    if (!state.es) { loadList(true); loadStats(); }
  }, 8000);
})();
