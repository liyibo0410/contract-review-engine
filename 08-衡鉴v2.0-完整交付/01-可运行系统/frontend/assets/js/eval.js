/* ============================================================================
   评测与观测页
   ========================================================================== */
'use strict';

const $ = (id) => document.getElementById(id);

function metricCard(k, v, cls, d) {
  return `<div class="stat"><div class="k">${k}</div>
    <div class="v ${cls || ''}">${v}</div><div class="d">${d}</div></div>`;
}

async function loadScore() {
  const r = await HJ.api('/api/eval/score');
  if (!r.has_result) {
    $('metrics').innerHTML = `<div class="card" style="grid-column:1/-1">
      <div class="banner warn">${HJ.esc(r.message)}</div></div>`;
    $('caseBody').innerHTML = '';
    return;
  }
  const s = r.run;
  $('metrics').innerHTML = [
    metricCard('高风险召回率', (s.high_recall * 100).toFixed(1) + '%',
      s.high_recall >= 1 ? 'ok' : 'high', `${s.high_hit} / ${s.high_total} 项黄金标注命中`),
    metricCard('误报数', s.false_positive, s.false_positive ? 'high' : 'ok',
      '命中高风险但不在期望清单中'),
    metricCard('准确率 Precision', (s.precision * 100).toFixed(1) + '%',
      s.precision >= 1 ? 'ok' : 'medium', '高风险判定的可信度'),
    metricCard('解析成功率', (s.parse_ok / s.total_cases * 100).toFixed(0) + '%', 'ok',
      `${s.parse_ok} / ${s.total_cases} 份（含加密件正确阻塞）`),
    metricCard('平均解析耗时', s.avg_ms + ' ms', '', '单份合同解析 + 条款切分'),
    metricCard('综合判定', s.verdict.toUpperCase(),
      s.verdict === 'pass' ? 'ok' : 'medium', `规则版本 v${HJ.esc(s.rule_version)}`),
  ].join('');
  $('runHint').textContent = `评测时间 ${HJ.dt(s.created_at)} · 规则版本 v${s.rule_version}`;

  $('caseBody').innerHTML = (r.cases || []).map((c, i) => {
    const parse = c.blocked ? `<span class="tag s-blocked">blocked</span>` : `<span class="tag s-completed">成功</span>`;
    const ok = c.ok ? '' : 'style="background:var(--high-bg)"';
    return `<tr ${ok}>
      <td class="mono">${i + 1}</td>
      <td><div style="font-weight:600">${HJ.esc(c.name)}</div>
          <div class="small muted">${HJ.esc(c.file)}</div></td>
      <td class="small mono">${HJ.esc(c.engine || '—')}</td>
      <td>${parse}${c.blocked ? `<div class="small muted">${HJ.esc(c.block_code || '')}</div>` : ''}</td>
      <td style="text-align:right" class="mono">${c.clauses || 0}</td>
      <td style="text-align:right" class="mono">${c.chars || 0}</td>
      <td style="text-align:right" class="mono">${c.cost_ms || 0}</td>
      <td class="small mono">${(c.high_found || []).join(', ') || '—'}</td>
      <td class="small mono" style="color:var(--high)">${(c.high_miss || []).join(', ') || '—'}</td>
      <td class="small mono" style="color:var(--medium)">${(c.high_false || []).join(', ') || '—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" class="empty">无明细数据</td></tr>';
}

async function loadObs() {
  const [o, h] = await Promise.all([
    HJ.api('/api/stats/overview'),
    HJ.api('/api/system/health'),
  ]);
  $('obs').innerHTML = `<div class="kv">
    <div class="k">审查任务</div><div class="v mono">${o.total} 个（完成 ${o.completed} / 受阻 ${o.blocked}）</div>
    <div class="k">风险命中</div><div class="v mono">${o.findings} 条（高风险 ${o.high_findings}）</div>
    <div class="k">解析成功率</div><div class="v mono">${(o.parse_success_rate * 100).toFixed(1)}%</div>
    <div class="k">平均解析耗时</div><div class="v mono">${o.avg_parse_ms} ms</div>
    <div class="k">规则库</div><div class="v mono">v${HJ.esc(h.rules.version)} · ${h.rules.total} 条</div>
    <div class="k">语义层</div><div class="v">${h.llm.configured
      ? `<span class="tag none">已接入</span> ${HJ.esc(h.llm.model)}（超时 ${h.llm.timeout}s）`
      : '<span class="tag medium">未配置 · 已降级模板生成器</span>'}</div>
    <div class="k">OCR 引擎</div><div class="v">${h.ocr.available
      ? '<span class="tag none">就绪</span> 最低置信度 ' + h.ocr.min_confidence
      : '<span class="tag high">不可用</span> 扫描件将进入 blocked'}</div>
    <div class="k">数据库</div><div class="v mono small" style="word-break:break-all">${HJ.esc(h.db)}</div>
  </div>
  <div class="small muted mt">回写状态分布：${Object.entries(o.by_writeback || {})
    .map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}</div>`;
}

async function runEval() {
  const btn = $('btnRun');
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> 评测运行中（约需 10 秒）';
  try {
    const r = await HJ.api('/api/eval/run', { method: 'POST', body: { cases: 0, persist: true } });
    const s = r.summary;
    HJ.toast(`评测完成：高风险召回 ${(s.high_recall * 100).toFixed(1)}% · 误报 ${s.false_positive} · ${s.verdict.toUpperCase()}`,
      s.verdict === 'pass' ? 'ok' : 'err', 7000);
    await Promise.all([loadScore(), loadObs()]);
  } catch (e) {
    HJ.toast('评测失败：' + e.message, 'err', 7000);
  } finally {
    btn.disabled = false; btn.textContent = '运行评测（全量 13 份）';
  }
}

(function init() {
  HJ.mountTopbar('eval');
  $('btnRun').onclick = runEval;
  $('btnRefresh').onclick = () => { loadScore(); loadObs(); };
  loadScore();
  loadObs();
})();
