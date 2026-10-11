/* ============================================================================
   衡鉴 · 前端公共层
   零构建：不使用任何框架与 CDN，全部原生 DOM + fetch。
   这一层只做三件事：调接口、渲染公共部件、管通知与模态。
   ========================================================================== */
'use strict';

const HJ = (() => {

  // ---------------------------------------------------------------- 请求
  async function api(path, { method = 'GET', body, form, raw } = {}) {
    const opt = { method, headers: {} };
    if (form) {
      opt.body = form;                       // FormData，交给浏览器设置 boundary
    } else if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const res = await fetch(path, opt);
    if (raw) return res;
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) || ('HTTP ' + res.status);
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  // ---------------------------------------------------------------- 格式化
  const RISK = { high: '高风险', medium: '中风险', low: '低风险', none: '无风险' };
  const STATUS = {
    pending: '待处理', parsing: '解析中', reviewing: '审查中',
    blocked: '解析受阻', completed: '审查完成',
  };
  const CLAUSE = {
    subject: '签约主体', subject_matter: '标的物', payment: '价款支付',
    delivery: '交付验收', liability: '违约责任', confidentiality: '保密义务',
    ip: '知识产权', data_security: '数据安全', dispute: '争议管辖',
    validity: '合同效力', force_majeure: '不可抗力', other: '其他',
  };

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const money = (v, cur) => {
    if (v == null || v === '') return '—';
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    return (cur === 'USD' ? '$' : cur === 'EUR' ? '€' : '¥') + n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  };

  const dt = (s) => (s || '').replace('T', ' ').slice(0, 19);

  function riskTag(level, cnt) {
    const l = level || 'none';
    return `<span class="tag ${l}">${RISK[l] || l}${cnt != null ? ' ' + cnt : ''}</span>`;
  }
  function statusTag(s) {
    return `<span class="tag s-${esc(s)}">${STATUS[s] || esc(s)}</span>`;
  }

  // ---------------------------------------------------------------- 通知
  function toast(msg, kind = '', ms = 3600) {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = esc(msg);
    document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, ms - 320);
    setTimeout(() => el.remove(), ms);
  }

  // ---------------------------------------------------------------- 模态
  function modal(title, bodyHtml, footHtml, onMount) {
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `<div class="modal">
      <div class="mh"><strong>${esc(title)}</strong>
        <button class="btn sm ghost" data-close>关闭</button></div>
      <div class="mb">${bodyHtml}</div>
      ${footHtml ? `<div class="mf">${footHtml}</div>` : ''}
    </div>`;
    mask.addEventListener('click', (e) => {
      if (e.target === mask || e.target.hasAttribute('data-close')) mask.remove();
    });
    document.body.appendChild(mask);
    if (onMount) onMount(mask);
    return mask;
  }

  // ---------------------------------------------------------------- 顶栏
  const NAV = [
    { href: '/', label: '审查大盘', key: 'dashboard' },
    { href: '/pages/workbench.html', label: '智能审查工作台', key: 'workbench' },
    { href: '/pages/rules.html', label: '合规规则与模板', key: 'rules' },
    { href: '/pages/eval.html', label: '评测与观测', key: 'eval' },
  ];

  function mountTopbar(activeKey) {
    const nav = NAV.map(n => `<a href="${n.href}" class="${n.key === activeKey ? 'active' : ''}">${n.label}</a>`).join('');
    const html = `
      <div class="brand">
        <span class="cn">衡鉴</span><span class="en">HengJian</span>
        <span class="tag">合同智能审查 v2.0</span>
      </div>
      <nav class="nav">${nav}</nav>
      <div class="spacer"></div>
      <div class="health" id="healthBox"><span class="spin"></span></div>`;
    const bar = document.getElementById('topbar');
    bar.classList.add('topbar');
    bar.innerHTML = html;
    loadHealth();
  }

  async function loadHealth() {
    const box = document.getElementById('healthBox');
    if (!box) return;
    try {
      const h = await api('/api/system/health');
      const llmOn = h.llm && h.llm.configured;
      const ocrOn = h.ocr && h.ocr.available;
      box.innerHTML = `
        <span title="规则库版本与条数"><span class="dot on"></span>规则 ${h.rules.total} 条 / v${esc(h.rules.version)}</span>
        <span title="大模型语义增强状态"><span class="dot ${llmOn ? 'on' : 'off'}"></span>语义层 ${llmOn ? '在线' : '降级模板'}</span>
        <span title="扫描件 OCR 引擎"><span class="dot ${ocrOn ? 'on' : 'off'}"></span>OCR ${ocrOn ? '就绪' : '不可用'}</span>
        <a href="/api/docs" target="_blank">API 文档</a>`;
    } catch (e) {
      box.innerHTML = `<span class="dot off"></span>服务未就绪`;
    }
  }

  // ---------------------------------------------------------------- SSE
  function streamEvents(contractId, onEvent, { fromSeq = 0 } = {}) {
    const es = new EventSource(`/api/contracts/${contractId}/events?from_seq=${fromSeq}`);
    es.onmessage = (ev) => {
      try { onEvent(JSON.parse(ev.data)); } catch (e) { /* 忽略心跳 */ }
    };
    es.addEventListener('end', () => es.close());
    es.onerror = () => es.close();
    return es;
  }

  // ---------------------------------------------------------------- 雷达图
  function radar(dims, size = 240) {
    const cx = size / 2, cy = size / 2, r = size / 2 - 42;
    const n = dims.length || 1;
    const pt = (i, v) => {
      const a = (Math.PI * 2 * i / n) - Math.PI / 2;
      const rr = r * Math.max(0, Math.min(100, v)) / 100;
      return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
    };
    const grid = [25, 50, 75, 100].map(p => {
      const pts = dims.map((_, i) => pt(i, p).map(x => x.toFixed(1)).join(',')).join(' ');
      return `<polygon points="${pts}" fill="none" stroke="#2E394B" stroke-width="1"/>`;
    }).join('');
    const axes = dims.map((_, i) => {
      const [x, y] = pt(i, 100);
      return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#2E394B"/>`;
    }).join('');
    const poly = dims.map((d, i) => pt(i, d.score).map(x => x.toFixed(1)).join(',')).join(' ');
    const labels = dims.map((d, i) => {
      const [x, y] = pt(i, 118);
      const anchor = x < cx - 6 ? 'end' : x > cx + 6 ? 'start' : 'middle';
      return `<text x="${x.toFixed(1)}" y="${(y + 3).toFixed(1)}" fill="#A7B0C0" font-size="10"
        text-anchor="${anchor}">${esc(d.label)}</text>`;
    }).join('');
    const values = dims.map((d, i) => {
      const [x, y] = pt(i, Math.max(12, d.score));
      return `<text x="${x.toFixed(1)}" y="${(y + 3).toFixed(1)}" fill="#4C8DFF" font-size="9.5"
        font-family="monospace" text-anchor="middle">${d.score}</text>`;
    }).join('');
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      ${grid}${axes}
      <polygon points="${poly}" fill="rgba(76,141,255,.22)" stroke="#4C8DFF" stroke-width="1.6"/>
      ${values}${labels}
    </svg>`;
  }

  // ---------------------------------------------------------------- Markdown 极简渲染
  function md(text) {
    let s = esc(text);
    s = s.replace(/^### (.*)$/gm, '<strong style="font-size:14px">$1</strong>');
    s = s.replace(/^## (.*)$/gm, '<strong style="font-size:15px">$1</strong>');
    s = s.replace(/^# (.*)$/gm, '<strong style="font-size:16px">$1</strong>');
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`([^`]+)`/g, '<code style="background:#161C27;padding:1px 5px;border-radius:3px">$1</code>');
    s = s.replace(/^&gt; (.*)$/gm, '<span style="color:#A7B0C0;border-left:2px solid #2E394B;padding-left:8px;display:inline-block">$1</span>');
    s = s.replace(/^- (.*)$/gm, '· $1');
    return s;
  }

  const qs = (k) => new URLSearchParams(location.search).get(k);

  return {
    api, esc, money, dt, riskTag, statusTag, toast, modal, mountTopbar, loadHealth,
    streamEvents, radar, md, qs, RISK, STATUS, CLAUSE,
  };
})();
