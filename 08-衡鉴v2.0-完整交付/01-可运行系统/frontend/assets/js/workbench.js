/* ============================================================================
   智能审查工作台
   三块联动：① 左侧合同正文（后端渲染的段落 + 字符坐标）② 右侧风险卡片
             ③ 审批协同与回写区
   联动的实现要点：DOM 段落带 data-start/data-end，风险项带 char_start/char_end，
   双方靠"字符区间求交"建立映射 —— 因此 docx / pdf / OCR 三种来源共用一套高亮逻辑。
   ========================================================================== */
'use strict';

const W = {
  id: null, task: null, doc: null, findings: [], review: null,
  writeback: {}, active: null, tab: 'findings', es: null,
  clauseRanges: [],
};

const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ 加载
async function loadTasks() {
  const r = await HJ.api('/api/contracts?limit=200');
  const sel = $('taskSel');
  sel.innerHTML = (r.items || []).map(it =>
    `<option value="${it.id}">${HJ.esc(it.name)}　·　${HJ.RISK[it.risk_level] || ''}　·　${HJ.esc(it.code)}</option>`
  ).join('') || '<option value="">（暂无任务）</option>';
  const qid = Number(HJ.qs('id'));
  if (qid) sel.value = String(qid);
  sel.onchange = () => loadAll(Number(sel.value));
  if (sel.value) await loadAll(Number(sel.value));
}

async function loadAll(id) {
  if (!id) return;
  W.id = id;
  if (W.es) { W.es.close(); W.es = null; }
  try {
    const [detail, doc, fs] = await Promise.all([
      HJ.api('/api/contracts/' + id),
      HJ.api(`/api/contracts/${id}/document`).catch(() => null),
      HJ.api(`/api/contracts/${id}/findings`).catch(() => ({ items: [] })),
    ]);
    W.task = detail.task; W.review = detail.review; W.writeback = detail.writeback || {};
    W.meta = detail.meta || []; W.profile = detail.profile || {};
    W.doc = doc; W.findings = fs.items || [];
    W.clauseRanges = (detail.clauses || []).map(c => ({
      t: c.clause_type, s: c.char_start, e: c.char_end,
    }));
  } catch (e) {
    HJ.toast('加载失败：' + e.message, 'err');
    return;
  }
  renderHead();
  renderBlocked();
  renderDoc();
  renderRight();
  if (['pending', 'parsing', 'reviewing'].includes(W.task.status)) watch();
}

function watch() {
  $('taskTags').insertAdjacentHTML('beforeend',
    '<span class="tag s-parsing" id="liveTag"><span class="spin"></span> 流水线运行中</span>');
  W.es = HJ.streamEvents(W.id, (ev) => {
    if (['completed', 'blocked', 'error'].includes(ev.stage)) {
      W.es.close(); W.es = null;
      setTimeout(() => loadAll(W.id), 400);
    }
  });
}

function renderHead() {
  const t = W.task;
  $('taskTags').innerHTML = `
    ${HJ.statusTag(t.status)}
    ${HJ.riskTag(t.risk_level)}
    <span class="tag plain">${HJ.esc(t.biz_type || '其他')}</span>
    ${t.conclusion ? `<span class="tag ${t.risk_level}">${HJ.esc(t.conclusion)}</span>` : ''}
    <span class="small muted mono">${HJ.esc(t.code)}</span>`;
  $('fCnt').textContent = W.findings.length;
}

function renderBlocked() {
  const box = $('blockedBox');
  if (W.task.status !== 'blocked') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = `<div class="banner">
    <div style="flex:1">
      <strong>任务已阻塞（${HJ.esc(W.task.block_reason ? '需人工处理' : '')}）</strong>
      <div class="small" style="margin-top:4px">${HJ.esc(W.task.block_reason || '未知原因')}</div>
      <div class="small muted" style="margin-top:4px">
        处理建议：加密文档请去除密码后重新上传；扫描件请提供更清晰的版本；OCR 引擎缺失请安装 rapidocr-onnxruntime。</div>
    </div>
    <button class="btn danger" id="btnRetry">重新解析 / 重试</button>
  </div>`;
  $('btnRetry').onclick = async () => {
    $('btnRetry').disabled = true;
    HJ.toast('已提交重试…');
    try {
      await HJ.api(`/api/contracts/${W.id}/retry?wait=false`, { method: 'POST' });
      setTimeout(() => loadAll(W.id), 600);
    } catch (e) { HJ.toast('重试失败：' + e.message, 'err'); $('btnRetry').disabled = false; }
  };
}

// ------------------------------------------------------------------ 文档渲染
function renderDoc() {
  const body = $('docBody');
  if (!W.doc || !W.doc.paragraphs || !W.doc.paragraphs.length) {
    body.innerHTML = `<div class="empty">${W.task.status === 'blocked'
      ? '文档解析受阻，暂无正文可展示' : '暂无正文数据'}</div>`;
    return;
  }
  let lastPage = 0;
  const html = W.doc.paragraphs.map((p, i) => {
    let head = '';
    if (p.page !== lastPage) {
      lastPage = p.page;
      head = `<div class="pg">— 第 ${p.page} 页 —<span class="muted">${HJ.esc(p.clause_label || '')}</span></div>`;
    }
    return head + `<p id="p-${i}" class="${p.is_heading ? 'heading' : ''}"
      data-idx="${i}" data-start="${p.char_start}" data-end="${p.char_end}"
      data-clause="${HJ.esc(p.clause_type)}">${HJ.esc(p.text)}
      <span class="cq">${HJ.esc(p.clause_label || '')}</span></p>`;
  }).join('');
  body.innerHTML = `<div class="doc">${html}</div>`;
  applyHighlights();
}

function overlap(a1, a2, b1, b2) { return a1 < b2 && b1 < a2; }

function applyHighlights() {
  const ps = [...$('docBody').querySelectorAll('p[data-start]')];
  ps.forEach(p => {
    p.classList.remove('hl-high', 'hl-medium', 'hl-low');
    const s = Number(p.dataset.start), e = Number(p.dataset.end);
    let best = null;
    for (const f of W.findings) {
      if (overlap(s, e, f.char_start, f.char_end)) {
        if (!best || rank(f.level) < rank(best)) best = f.level;
      }
    }
    if (best) p.classList.add('hl-' + best);
  });
}
const rank = (l) => ({ high: 0, medium: 1, low: 2 }[l] ?? 3);

function focusFinding(f) {
  W.active = f.id;
  [...$('docBody').querySelectorAll('p')].forEach(p => p.classList.remove('pulse'));
  const target = [...$('docBody').querySelectorAll('p[data-start]')].find(p =>
    overlap(Number(p.dataset.start), Number(p.dataset.end), f.char_start, f.char_end));
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => target.classList.add('pulse'), 260);
  }
  [...document.querySelectorAll('.fcard')].forEach(c =>
    c.classList.toggle('active', Number(c.dataset.id) === f.id));
  const card = document.querySelector(`.fcard[data-id="${f.id}"]`);
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// 划选原文 → 反查涉及的风险项
document.addEventListener('mouseup', () => {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return;
  const node = sel.anchorNode;
  const p = node && (node.nodeType === 1 ? node : node.parentNode);
  if (!p || !p.closest || !p.closest('#docBody')) return;
  const para = p.closest('p[data-start]') || p;
  if (!para || !para.dataset || !para.dataset.start) return;
  const text = sel.toString().trim();
  if (text.length < 4) return;
  const s = Number(para.dataset.start);
  const idx = para.textContent.indexOf(text);
  if (idx < 0) return;
  const cs = s + idx, ce = cs + text.length;
  const hits = W.findings.filter(f => overlap(cs, ce, f.char_start, f.char_end));
  const type = W.clauseRanges.find(c => overlap(cs, ce, c.s, c.e));
  HJ.modal('片段研判', `
    <div class="small muted">选中片段（第 ${para.previousElementSibling ? '' : ''}${para.dataset.clause} 条款）</div>
    <div style="margin:10px 0;padding:10px 12px;background:var(--bg);border-left:2px solid var(--brand);border-radius:4px">
      ${HJ.esc(text)}</div>
    <div class="small muted">字符区间 [${cs}, ${ce})　·　条款类型：${HJ.esc(HJ.CLAUSE[type ? type.t : 'other'] || '其他')}</div>
    <div style="margin-top:14px">
      <strong>涉及风险项（${hits.length}）</strong>
      ${hits.length ? hits.map(f => `<div class="fcard ${f.level}" data-goto="${f.id}">
          <div class="t">${HJ.riskTag(f.level)} ${HJ.esc(f.title)}</div>
          <div class="meta"><span class="mono">${HJ.esc(f.rule_id)}</span></div>
          <div class="ev">${HJ.esc(f.evidence_text)}</div>
        </div>`).join('') : '<div class="muted small" style="margin-top:8px">该片段未命中规则；如需针对性意见，请由法务人工补充批注。</div>'}
    </div>`, `<button class="btn" data-close>关闭</button>`, (mask) => {
    mask.querySelectorAll('[data-goto]').forEach(el => el.onclick = () => {
      const f = W.findings.find(x => x.id === Number(el.dataset.goto));
      mask.remove();
      W.tab = 'findings'; renderRight();
      setTimeout(() => focusFinding(f), 60);
    });
  });
});

// ------------------------------------------------------------------ 右侧面板
function renderRight() {
  [...document.querySelectorAll('#rightTabs .t')].forEach(t =>
    t.classList.toggle('on', t.dataset.tab === W.tab));
  const box = $('rightBody');
  if (W.tab === 'findings') box.innerHTML = viewFindings();
  else if (W.tab === 'summary') box.innerHTML = viewSummary();
  else box.innerHTML = viewMeta();

  if (W.tab === 'findings') {
    box.querySelectorAll('.fcard').forEach(c => {
      c.onclick = (e) => {
        if (e.target.closest('button')) return;
        focusFinding(W.findings.find(f => f.id === Number(c.dataset.id)));
      };
    });
    box.querySelectorAll('[data-adopt]').forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      adopt(Number(b.dataset.adopt));
    });
    box.querySelectorAll('[data-copy]').forEach(b => b.onclick = (e) => {
      e.stopPropagation();
      const f = W.findings.find(x => x.id === Number(b.dataset.copy));
      navigator.clipboard.writeText(f.advice || '').then(
        () => HJ.toast('修改建议已复制到剪贴板', 'ok'),
        () => HJ.toast('复制失败，请手动选择文本', 'err'));
    });
  }
  if (W.tab === 'summary') bindSummary();
  if (W.tab === 'meta') {
    box.querySelectorAll('[data-jump]').forEach(b => b.onclick = () => {
      const p = [...$('docBody').querySelectorAll('p[data-start]')].find(x =>
        overlap(Number(x.dataset.start), Number(x.dataset.end),
                Number(b.dataset.s), Number(b.dataset.e)));
      if (p) { p.scrollIntoView({ behavior: 'smooth', block: 'center' });
               setTimeout(() => p.classList.add('pulse'), 250); }
    });
  }
}

function viewFindings() {
  if (!W.findings.length) {
    return `<div class="empty">${W.task.status === 'completed'
      ? '未命中任何风险项，合同条款齐备。' : '尚未产生审查结果。'}</div>`;
  }
  const groups = [['high', '高风险'], ['medium', '中风险'], ['low', '低风险']];
  return groups.map(([lv, label]) => {
    const items = W.findings.filter(f => f.level === lv);
    if (!items.length) return '';
    return `<div style="margin:6px 2px 8px;font-size:12px;color:var(--text-3)">
        ${label}　<span class="mono">${items.length}</span> 项</div>` +
      items.map(f => `
      <div class="fcard ${lv} ${W.active === f.id ? 'active' : ''}" data-id="${f.id}">
        <div class="t">${HJ.riskTag(lv)}<span>${HJ.esc(f.title)}</span></div>
        <div class="meta">
          <span class="mono">${HJ.esc(f.rule_id)}</span>
          <span>${HJ.esc(f.clause_label)}</span>
          <span>第 ${f.page} 页 · 段 #${f.para_index}</span>
          ${f.adopted ? '<span class="tag none">已采纳</span>' : ''}
        </div>
        <div class="ev">${HJ.esc(f.evidence_text)}</div>
        <details style="margin-top:8px">
          <summary class="small muted" style="cursor:pointer">成因 / 依据 / 示范条款</summary>
          <div class="small" style="margin-top:8px;line-height:1.8;color:var(--text-2)">
            <div><strong style="color:var(--text)">成因：</strong>${HJ.esc(f.reason || '—')}</div>
            <div style="margin-top:4px"><strong style="color:var(--text)">依据：</strong>${HJ.esc(f.legal_basis || '—')}</div>
            ${f.advice ? `<div style="margin-top:8px;padding:9px 11px;background:var(--bg);border-radius:4px;
              border-left:2px solid var(--brand);color:#C9D3E1">${HJ.esc(f.advice)}
              <div class="small muted" style="margin-top:6px">来源：${f.advice_source === 'llm' ? '大模型润色' : '标准条款库'}</div>
              ${f.adopted && f.adopted_text ? `<div class="small" style="margin-top:8px;color:var(--ok)">已采纳文本：${HJ.esc(f.adopted_text)}</div>` : ''}
            </div>` : ''}
          </div>
        </details>
        <div class="act">
          <button class="btn sm ${f.adopted ? '' : 'primary'}" data-adopt="${f.id}">
            ${f.adopted ? '撤销采纳' : '采纳建议'}</button>
          <button class="btn sm ghost" data-copy="${f.id}">复制条款</button>
        </div>
      </div>`).join('');
  }).join('');
}

function viewSummary() {
  const r = W.review || {};
  const wb = W.writeback || {};
  const statusLabel = {
    not_written: '未回写', writing: '回写中', success: '回写成功', failed: '回写失败',
  }[wb.status || 'not_written'];
  const stats = { high: 0, medium: 0, low: 0 };
  W.findings.forEach(f => stats[f.level]++);
  return `
    <div class="card" style="background:var(--bg-2)">
      <div class="card-title"><span class="em">综合审查结论</span></div>
      <div class="row" style="gap:8px;margin-bottom:10px">
        ${HJ.riskTag(W.task.risk_level)}
        <span class="tag plain">高风险 ${stats.high}</span>
        <span class="tag plain">中风险 ${stats.medium}</span>
        <span class="tag plain">低风险 ${stats.low}</span>
        <span class="tag plain">生成方式：${r.generated_by === 'llm' ? '大模型语义增强' : '规则模板'}</span>
      </div>
      <div style="font-size:13.5px;line-height:1.9;color:var(--text)">
        ${HJ.esc(r.risk_summary || '（尚未生成审查摘要）')}</div>
      ${r.conclusion ? `<div style="margin-top:12px;font-size:16px;font-weight:700;
        color:${r.conclusion === '建议拒绝' ? 'var(--high)' : r.conclusion === '建议通过' ? 'var(--ok)' : 'var(--medium)'}">
        ${HJ.esc(r.conclusion)}</div>` : ''}
      ${(r.focus_points || []).length ? `
        <div style="margin-top:14px">
          <div class="small muted" style="margin-bottom:6px">审批关注要点</div>
          ${r.focus_points.map(p => `<div class="small" style="padding:6px 0;border-top:1px solid var(--line)">• ${HJ.esc(p)}</div>`).join('')}
        </div>` : ''}
    </div>

    <div class="wb-box">
      <div class="card-title"><span class="em">法务批注</span></div>
      <textarea id="note" placeholder="补充法务意见（将随审查意见一起写回审批单评论区）">${HJ.esc(r.reviewer_note || '')}</textarea>
      <button class="btn sm mt" id="btnNote">保存批注</button>
    </div>

    <div class="wb-box">
      <div class="card-title"><span class="em">人工审核（HITL）</span>
        <span class="small muted" id="hitlState"></span></div>
      <div id="hitlBox" class="small muted">加载中…</div>
    </div>

    <div class="wb-box">
      <div class="card-title"><span class="em">审批协同与回写</span></div>
      <div class="kv" style="margin-bottom:10px">
        <div class="k">回写状态</div><div class="v">
          <span class="tag ${wb.status === 'success' ? 'none' : wb.status === 'failed' ? 'high' : 'plain'}">${statusLabel}</span>
          ${wb.dry_run ? '<span class="small muted">预览模式（DRY_RUN）</span>' : ''}</div>
        <div class="k">幂等键</div><div class="v mono small" style="word-break:break-all">${HJ.esc(wb.idem_key || '—')}</div>
        <div class="k">评论 ID</div><div class="v mono">${HJ.esc(wb.platform_comment_id || '—')}</div>
        ${wb.error ? `<div class="k">错误</div><div class="v" style="color:var(--high)">${HJ.esc(wb.error)}</div>` : ''}
      </div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <button class="btn" id="btnPreview">预览意见（DRY_RUN）</button>
        <button class="btn primary" id="btnWrite">写回审批意见</button>
        ${wb.status === 'failed' ? '<button class="btn danger" id="btnWbRetry">重试回写</button>' : ''}
      </div>
      <div class="small muted mt">
        安全边界：系统仅向审批单写入评论，不具备通过 / 拒绝 / 转交 / 撤回能力；重复触发由幂等键自动跳过。
      </div>
      <div class="mt" id="cmtBox"></div>
    </div>`;
}

function viewMeta() {
  const meta = W.meta || [];
  const prof = W.profile || {};
  const d = W.doc || {};
  const dims = prof.dimensions || [];
  return `
    <div class="card" style="background:var(--bg-2)">
      <div class="card-title"><span class="em">合同元数据（点击可跳转原文）</span></div>
      ${meta.length ? meta.map(m => `
        <div class="row between" style="padding:7px 0;border-bottom:1px solid var(--line)">
          <div style="min-width:120px" class="small muted">${HJ.esc(m.label)}</div>
          <div style="flex:1;font-size:13px">${HJ.esc(m.value)}</div>
          <div class="small muted mono nowrap">${m.page ? '第 ' + m.page + ' 页' : '—'}
            ${m.char_start >= 0 ? `<button class="btn sm ghost" data-jump data-s="${m.char_start}" data-e="${m.char_end}">定位</button>` : ''}
          </div>
        </div>`).join('') : '<div class="muted small">未抽取到元数据</div>'}
    </div>

    <div class="card mt" style="background:var(--bg-2)">
      <div class="card-title"><span class="em">合同画像</span>
        <span class="small muted">综合分 ${prof.overall != null ? prof.overall : '—'} / 100</span></div>
      ${dims.length ? `<div class="radar-wrap">
        ${HJ.radar(dims)}
        <div class="radar-legend">
          ${dims.map(x => `<div class="li">
            <span style="min-width:96px">${HJ.esc(x.label)}</span>
            <span class="bar"><i style="width:${x.score}%;background:${
              x.score >= 75 ? 'var(--ok)' : x.score >= 45 ? 'var(--brand)' : 'var(--high)'}"></i></span>
            <span class="mono small">${x.score}</span>
          </div>`).join('')}
        </div></div>` : '<div class="muted small">画像尚未生成</div>'}
    </div>

    <div class="card mt" style="background:var(--bg-2)">
      <div class="card-title"><span class="em">解析信息</span></div>
      <div class="kv">
        <div class="k">文件</div><div class="v">${HJ.esc(d.file_name || '—')}</div>
        <div class="k">格式</div><div class="v">${HJ.esc(d.file_type || '—')}</div>
        <div class="k">解析引擎</div><div class="v mono">${HJ.esc(d.parse_engine || '—')}</div>
        <div class="k">页数 / 字数</div><div class="v mono">${d.page_count || 0} 页 / ${d.char_count || 0} 字</div>
        <div class="k">SHA256</div><div class="v mono small" style="word-break:break-all">${HJ.esc((d.sha256 || '').slice(0, 32))}…</div>
        ${d.ocr_confidence != null ? `<div class="k">OCR 置信度</div><div class="v mono">${d.ocr_confidence}</div>` : ''}
        ${d.block_reason ? `<div class="k">阻塞原因</div><div class="v" style="color:var(--medium)">${HJ.esc(d.block_reason)}</div>` : ''}
      </div>
      <div class="small muted mt">快捷键：<b>j / k</b> 切换风险项　<b>Enter</b> 采纳　<b>/</b> 聚焦检索</div>
    </div>`;
}

function bindSummary() {
  const r = W.review || {};
  const n = $('btnNote');
  if (n) n.onclick = async () => {
    await HJ.api(`/api/contracts/${W.id}/annotations`,
      { method: 'POST', body: { note: $('note').value, reviewer: '' } });
    HJ.toast('批注已保存', 'ok');
  };
  const pv = $('btnPreview');
  if (pv) pv.onclick = async () => {
    const r2 = await HJ.api(`/api/contracts/${W.id}/writeback/preview`);
    HJ.modal('审查意见预览（DRY_RUN · 未写入审批系统）',
      `<div class="small muted" style="margin-bottom:8px">幂等键：<span class="mono">${HJ.esc(r2.idem_key)}</span></div>
       <pre class="code md">${HJ.esc(r2.preview)}</pre>`,
      `<button class="btn" data-close>关闭</button>`);
  };
  const w = $('btnWrite');
  if (w) w.onclick = async () => {
    w.disabled = true; w.innerHTML = '<span class="spin"></span> 写入中';
    try {
      const res = await HJ.api(`/api/contracts/${W.id}/writeback`, {
        method: 'POST',
        body: { dry_run: false, operator: '李奕波', reviewer_note: ($('note') || {}).value || '' },
      });
      HJ.toast(res.message || '回写完成', res.status === 'success' ? 'ok' : 'err', 6000);
      await loadAll(W.id); W.tab = 'summary'; renderRight();
    } catch (e) {
      HJ.toast('回写失败：' + e.message, 'err', 7000);
      w.disabled = false; w.textContent = '写回审批意见';
    }
  };
  const wr = $('btnWbRetry');
  if (wr) wr.onclick = async () => {
    try {
      const res = await HJ.api(`/api/contracts/${W.id}/writeback/retry`, { method: 'POST' });
      HJ.toast(res.message || '已重试', 'ok');
      await loadAll(W.id); W.tab = 'summary'; renderRight();
    } catch (e) { HJ.toast('重试失败：' + e.message, 'err'); }
  };
  loadComments();
  loadHitl();
}

const HITL_LABEL = { approve: '同意', edit: '修改后确认', reject: '驳回', resume: '恢复' };
const HITL_STATE = {
  pending_review: '待人工确认', approved: '已确认', edited: '已修改确认',
  rejected: '已驳回', blocked: '解析受阻', completed: '审查完成',
};

async function loadHitl() {
  const box = $('hitlBox');
  if (!box) return;
  let h;
  try { h = await HJ.api(`/api/contracts/${W.id}/hitl`); }
  catch (e) { box.innerHTML = `<span style="color:var(--high)">${HJ.esc(e.message)}</span>`; return; }
  const st = HITL_STATE[h.status] || h.status;
  if ($('hitlState')) $('hitlState').textContent = st;
  box.innerHTML = `
    <div class="kv" style="margin-bottom:10px">
      <div class="k">中断点</div><div class="v mono">${HJ.esc(h.interrupt_at || '—')}</div>
      <div class="k">原因</div><div class="v">${HJ.esc(h.interrupt_reason || '—')}</div>
      <div class="k">当前状态</div><div class="v"><span class="tag plain">${HJ.esc(st)}</span></div>
      <div class="k">可执行动作</div><div class="v">${h.available_actions.length
        ? h.available_actions.map(a => `<span class="tag plain">${HJ.esc(HITL_LABEL[a] || a)}</span>`).join(' ')
        : '<span class="muted">无（流水线运行中）</span>'}</div>
    </div>
    <div class="row" style="gap:8px;flex-wrap:wrap">
      ${h.available_actions.includes('approve') ? '<button class="btn sm primary" data-hitl="approve">同意结论</button>' : ''}
      ${h.available_actions.includes('edit') ? '<button class="btn sm" data-hitl="edit">修改后确认</button>' : ''}
      ${h.available_actions.includes('reject') ? '<button class="btn sm danger" data-hitl="reject">驳回并重新审查</button>' : ''}
      ${h.available_actions.includes('resume') ? '<button class="btn sm danger" data-hitl="resume">从阻塞恢复</button>' : ''}
    </div>
    ${h.history.length ? `<details style="margin-top:10px">
      <summary class="small muted" style="cursor:pointer">审核流水（${h.history.length} 条）</summary>
      ${h.history.slice().reverse().map(x => `<div class="cmt" style="margin-top:6px">
        <div class="hd"><span>${HJ.esc(HITL_LABEL[x.action] || x.action)} · ${HJ.esc(x.actor || '匿名')}</span>
          <span class="mono">${HJ.dt(x.created_at)}</span></div>
        <div class="bd">${HJ.esc(x.note || '（未填写意见）')}
          ${x.interrupt_at ? `<div class="small muted" style="margin-top:4px">中断点：${HJ.esc(x.interrupt_at)}</div>` : ''}</div>
      </div>`).join('')}
    </details>` : ''}`;

  box.querySelectorAll('[data-hitl]').forEach(b => b.onclick = () => hitlAction(b.dataset.hitl));
}

function hitlAction(action) {
  const note = (W.review && W.review.reviewer_note) || '';
  if (action === 'approve') {
    HJ.modal('确认审查结论', `
      <div class="small muted">确认后该审查结果允许写回审批系统，并记录到审核流水。</div>
      <label class="f" style="margin-top:10px">审核意见（可选）</label>
      <textarea id="hitlNote">${HJ.esc(note)}</textarea>`,
      `<button class="btn" data-close>取消</button>
       <button class="btn primary" id="ok">确认同意</button>`, (m) => {
        m.querySelector('#ok').onclick = () => submitHitl('approve', m.querySelector('#hitlNote').value, [], m);
      });
    return;
  }
  if (action === 'reject') {
    HJ.modal('驳回审查结果', `
      <div class="banner warn">驳回将清空本次审查结果，任务回到「待处理」，需重新跑一遍流水线。
        请确认系统结论确实存在实质错误。</div>
      <label class="f" style="margin-top:12px">驳回理由（会记录到审核流水）</label>
      <textarea id="hitlNote" placeholder="例如：第 5 条付款安排已在本合同附件中另行约定，不构成风险"></textarea>`,
      `<button class="btn" data-close>取消</button>
       <button class="btn danger" id="ok">确认驳回并重跑</button>`, (m) => {
        m.querySelector('#ok').onclick = () => {
          const n = m.querySelector('#hitlNote').value.trim();
          if (!n) { HJ.toast('请填写驳回理由', 'err'); return; }
          submitHitl('reject', n, [], m);
        };
      });
    return;
  }
  if (action === 'resume') {
    submitHitl('resume', note, []);
    return;
  }
  // edit：逐条改等级 / 改建议
  const rows = W.findings.map(f => `
    <div style="border-bottom:1px solid var(--line);padding:10px 0">
      <div class="row between">
        <div><span class="tag ${f.level}">${HJ.RISK[f.level]}</span>
          <span style="margin-left:8px">${HJ.esc(f.title)}</span></div>
        <select data-lv="${f.id}">
          <option value="">保持原等级</option>
          <option value="high">改为高风险</option>
          <option value="medium">改为中风险</option>
          <option value="low">改为低风险</option>
        </select>
      </div>
      <div class="small muted" style="margin-top:6px">系统建议：</div>
      <textarea data-adv="${f.id}" style="min-height:60px">${HJ.esc(f.advice)}</textarea>
    </div>`).join('');
  HJ.modal('人工修改审查结果', `
    <div class="small muted" style="margin-bottom:10px">
      修改后的等级与建议会直接写回审查意见，并重算综合风险等级与审查结论。</div>
    ${rows}
    <label class="f" style="margin-top:12px">修改说明</label>
    <textarea id="hitlNote">${HJ.esc(note)}</textarea>`,
    `<button class="btn" data-close>取消</button>
     <button class="btn primary" id="ok">应用修改</button>`, (m) => {
      m.querySelector('#ok').onclick = () => {
        const edits = [];
        W.findings.forEach(f => {
          const lv = m.querySelector(`[data-lv="${f.id}"]`).value;
          const adv = m.querySelector(`[data-adv="${f.id}"]`).value.trim();
          const e = { finding_id: f.id };
          let dirty = false;
          if (lv) { e.level = lv; dirty = true; }
          if (adv && adv !== f.advice) { e.advice = adv; dirty = true; }
          if (dirty) edits.push(e);
        });
        if (!edits.length) { HJ.toast('没有检测到修改', 'err'); return; }
        submitHitl('edit', m.querySelector('#hitlNote').value, edits, m);
      };
    });
}

async function submitHitl(action, note, edits, mask) {
  try {
    const r = await HJ.api(`/api/contracts/${W.id}/human-review`, {
      method: 'POST',
      body: { action, actor: '李奕波', note, edits },
    });
    if (mask) mask.remove();
    HJ.toast(r.message || '已提交', 'ok', 5000);
    await loadAll(W.id);
    W.tab = 'summary';
    renderRight();
  } catch (e) {
    HJ.toast('操作失败：' + e.message, 'err', 6000);
  }
}

async function loadComments() {
  const box = $('cmtBox');
  if (!box) return;
  try {
    const r = await HJ.api(`/api/contracts/${W.id}/comments`);
    if (!r.items.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="small muted" style="margin-bottom:6px">
      审批系统评论区（模拟器）· ${r.items.length} 条</div>` +
      r.items.map(c => `<div class="cmt">
        <div class="hd"><span>${HJ.esc(c.author)}</span><span class="mono">${HJ.dt(c.created_at)}</span></div>
        <div class="bd">${HJ.esc(c.body_md).slice(0, 700)}</div>
      </div>`).join('');
  } catch (e) { box.innerHTML = ''; }
}

// ------------------------------------------------------------------ 采纳
function adopt(fid) {
  const f = W.findings.find(x => x.id === fid);
  if (!f) return;
  if (f.adopted) {
    HJ.api(`/api/contracts/${W.id}/findings/${fid}/adopt`,
      { method: 'POST', body: { adopted: false } }).then(() => {
        f.adopted = false; f.adopted_text = '';
        HJ.toast('已撤销采纳');
        renderRight(); applyHighlights();
      });
    return;
  }
  HJ.modal('采纳修改建议 · 可编辑',
    `<div class="small muted" style="margin-bottom:6px">原条款位置：第 ${f.page} 页 · 段 #${f.para_index}</div>
     <div class="small" style="padding:9px 11px;background:var(--bg);border-left:2px solid var(--high);border-radius:4px;margin-bottom:12px">
       ${HJ.esc(f.evidence_text)}</div>
     <label class="f">建议条款（可编辑后采纳）</label>
     <textarea id="adviceEd" style="min-height:180px">${HJ.esc(f.advice)}</textarea>
     <div class="small muted" style="margin-top:8px">采纳后将生成"修改前后对照"，并计入审查意见书。</div>`,
    `<button class="btn" data-close>取消</button>
     <button class="btn primary" id="btnAdoptOk">确认采纳</button>`,
    (mask) => {
      mask.querySelector('#btnAdoptOk').onclick = async () => {
        const text = mask.querySelector('#adviceEd').value.trim();
        await HJ.api(`/api/contracts/${W.id}/findings/${fid}/adopt`,
          { method: 'POST', body: { adopted: true, adopted_text: text } });
        f.adopted = true; f.adopted_text = text;
        mask.remove();
        HJ.toast('已采纳修改建议', 'ok');
        renderRight(); applyHighlights();
      };
    });
}

// ------------------------------------------------------------------ 报告
async function exportReport() {
  const r = await HJ.api(`/api/contracts/${W.id}/report`);
  HJ.modal('审查意见报告（Markdown）',
    `<pre class="code md">${HJ.esc(r.markdown)}</pre>`,
    `<button class="btn" data-close>关闭</button>
     <button class="btn" id="btnCopyMd">复制 Markdown</button>
     <a class="btn primary" href="/api/contracts/${W.id}/report?download=true" target="_blank">下载 PDF</a>`,
    (mask) => {
      mask.querySelector('#btnCopyMd').onclick = () => {
        navigator.clipboard.writeText(r.markdown).then(() => HJ.toast('已复制', 'ok'));
      };
    });
}

// ------------------------------------------------------------------ 检索
async function nlq() {
  const q = $('nlq').value.trim();
  if (!q) return;
  const box = $('nlqBox');
  box.style.display = 'block';
  try {
    const r = await HJ.api(`/api/contracts/${W.id}/search`, { method: 'POST', body: { query: q } });
    box.innerHTML = `<div class="banner ok">${HJ.esc(r.answer)}</div>
      ${(r.items || []).map(it => `<div class="fcard low" data-s="${it.char_start}" data-e="${it.char_end}" style="margin-top:8px">
        <div class="t"><span class="tag plain">${HJ.esc(it.clause_label)}</span> 第 ${it.page} 页</div>
        <div class="ev">${HJ.esc(it.text.slice(0, 220))}</div></div>`).join('')}`;
    box.querySelectorAll('[data-s]').forEach(el => el.onclick = () => {
      const s = Number(el.dataset.s), e = Number(el.dataset.e);
      const p = [...$('docBody').querySelectorAll('p[data-start]')].find(x =>
        overlap(Number(x.dataset.start), Number(x.dataset.end), s, e));
      if (p) { p.scrollIntoView({ behavior: 'smooth', block: 'center' });
               setTimeout(() => p.classList.add('pulse'), 250); }
    });
  } catch (e) { box.innerHTML = `<div class="banner">检索失败：${HJ.esc(e.message)}</div>`; }
}

// ------------------------------------------------------------------ 键盘流
document.addEventListener('keydown', (e) => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
    if (e.key === 'Escape') document.activeElement.blur();
    return;
  }
  if (e.key === '/') { e.preventDefault(); $('nlq').focus(); return; }
  if (e.key === 'j' || e.key === 'k') {
    e.preventDefault();
    if (!W.findings.length) return;
    let i = W.findings.findIndex(f => f.id === W.active);
    i = e.key === 'j' ? Math.min(i + 1, W.findings.length - 1) : Math.max(i - 1, 0);
    if (W.tab !== 'findings') { W.tab = 'findings'; renderRight(); }
    focusFinding(W.findings[i]);
  }
  if (e.key === 'Enter' && W.active) {
    e.preventDefault();
    adopt(W.active);
  }
});

// ------------------------------------------------------------------ 启动
(async function init() {
  HJ.mountTopbar('workbench');
  [...document.querySelectorAll('#rightTabs .t')].forEach(t => t.onclick = () => {
    W.tab = t.dataset.tab; renderRight();
  });
  $('btnReport').onclick = exportReport;
  $('btnNlq').onclick = nlq;
  $('nlq').onkeydown = (e) => { if (e.key === 'Enter') nlq(); };
  await loadTasks();
})();
