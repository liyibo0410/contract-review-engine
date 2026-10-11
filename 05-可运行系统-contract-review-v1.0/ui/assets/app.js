/* ==========================================================================
   合同智能审查系统 v1.0 — 前端公共脚本（原生 JS，无任何第三方依赖）
   ========================================================================== */
(function (global) {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));

  /* ------------------------------------------------------------ 主题 */
  function initTheme() {
    let saved = localStorage.getItem('cr-theme');
    if (!saved) {
      // 首次访问跟随系统偏好（不再默认亮色）
      saved = (window.matchMedia
        && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', saved);
    return saved;
  }
  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', cur);
    localStorage.setItem('cr-theme', cur);
    const b = $('#themeBtn');
    if (b) b.textContent = cur === 'dark' ? '浅色' : '深色';
    return cur;
  }

  /* ------------------------------------------------------------ 请求 */
  async function api(method, path, body, opts) {
    const init = { method: method, headers: {} };
    if (body !== undefined && body !== null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (opts && opts.signal) init.signal = opts.signal;
    let resp;
    progressStart();
    try {
      resp = await fetch(path, init);
    } catch (e) {
      progressDone();
      throw new Error('网络请求失败：' + e.message);
    }
    try {
      return await _unwrap(resp);
    } finally {
      progressDone();
    }
  }

  async function _unwrap(resp) {
    const ct = resp.headers.get('Content-Type') || '';
    if (!ct.includes('application/json')) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp;
    }
    const j = await resp.json();
    if (j.code !== 0) {
      const err = new Error(j.msg || ('接口错误 code=' + j.code));
      err.code = j.code;
      err.payload = j.data;
      throw err;
    }
    return j.data;
  }

  /* ------------------------------------------------------------ 提示 */
  function toast(msg, type) {
    let box = $('#toast');
    if (!box) { box = document.createElement('div'); box.id = 'toast'; document.body.appendChild(box); }
    const el = document.createElement('div');
    el.className = 'toast ' + (type || '');
    el.innerHTML = msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, 3400);
    setTimeout(() => el.remove(), 3750);
  }

  /* ------------------------------------------------------------ 格式化 */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function money(v, cur) {
    if (v === null || v === undefined || v === '') return '-';
    const n = Number(v);
    if (!isFinite(n)) return '-';
    const sym = (cur || 'CNY') === 'CNY' ? '¥' : '$';
    if (n >= 10000) {
      return sym + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        + ' <span class="faint">(' + (n / 10000).toFixed(1) + ' 万)</span>';
    }
    return sym + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  const RISK_CLS = { high: 't-high', medium: 't-mid', low: 't-low', none: 't-none' };
  function riskTag(level, label) {
    return '<span class="tag ' + (RISK_CLS[level] || 't-none') + '">' + esc(label || level) + '</span>';
  }
  const ST_CLS = {
    pending: 't-plain', parsing: 't-low', reviewing: 't-low', blocked: 't-high', completed: 't-ok'
  };
  function statusTag(status, label) {
    return '<span class="tag ' + (ST_CLS[status] || 't-plain') + '">' + esc(label || status) + '</span>';
  }
  const WB_CLS = { not_written: 't-plain', writing: 't-low', success: 't-ok', failed: 't-high' };
  function wbTag(status, label) {
    return '<span class="tag ' + (WB_CLS[status] || 't-plain') + '">' + esc(label || status) + '</span>';
  }
  function esc2(s) { return esc(s).replace(/\n/g, '<br>'); }

  /* ------------------------------------------------------------ 弹层 */
  function openModal(id) { const m = $('#' + id); if (m) m.classList.add('on'); }
  function closeModal(id) { const m = $('#' + id); if (m) m.classList.remove('on'); }

  /* ------------------------------------------------------------ 顶栏 */
  function renderTopbar(active, envText) {
    const nav = [
      ['/', '审查大盘'],
      ['/workbench', '智能审查工作台'],
      ['/rules', '合规规则与模板']
    ];
    const html = [
      '<div class="brand"><span class="mk">◆</span>合同智能审查系统<small>v1.0</small></div>',
      '<div class="nav">' + nav.map(n =>
        '<a href="' + n[0] + '"' + (n[0] === active ? ' class="active"' : '') + '>' + n[1] + '</a>'
      ).join('') + '</div>',
      '<div class="spacer"></div>',
      '<span class="env-tag" id="envTag">' + esc(envText || '本地演示环境') + '</span>',
      '<a class="btn sm ghost" href="/mock/feishu/api/todo" target="_blank">模拟审批接口</a>',
      '<button class="btn sm ghost" id="themeBtn" onclick="CR.toggleTheme()">深色</button>'
    ].join('');
    const bar = $('#topbar');
    if (bar) bar.innerHTML = html;
    const b = $('#themeBtn');
    if (b) b.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '浅色' : '深色';
  }

  /* ------------------------------------------------------------ 下载 */
  function download(url) {
    const a = document.createElement('a');
    a.href = url;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function qs(name) {
    const u = new URLSearchParams(location.search);
    return u.get(name);
  }

  function busy(btn, on, text) {
    if (!btn) return;
    if (on) {
      btn.dataset.old = btn.innerHTML;
      btn.innerHTML = '<span class="loading"></span> ' + (text || '处理中');
      btn.disabled = true;
    } else {
      if (btn.dataset.old) btn.innerHTML = btn.dataset.old;
      btn.disabled = false;
    }
  }

  /* ------------------------------------------------------------ 请求进度 */
  let _progVal = 0;
  let _progTimer = null;
  let _progCount = 0;

  function _progEl() {
    let el = document.getElementById('topbar-progress');
    if (!el) {
      el = document.createElement('div');
      el.id = 'topbar-progress';
      document.body.appendChild(el);
    }
    return el;
  }

  function progressStart() {
    _progCount += 1;
    const el = _progEl();
    _progVal = Math.max(_progVal, 8);
    el.style.width = _progVal + '%';
    el.classList.add('on');
    clearInterval(_progTimer);
    _progTimer = setInterval(() => {
      _progVal = Math.min(_progVal + (92 - _progVal) * 0.1, 92);
      el.style.width = _progVal + '%';
    }, 200);
  }

  function progressDone() {
    _progCount = Math.max(0, _progCount - 1);
    if (_progCount > 0) return;
    const el = _progEl();
    clearInterval(_progTimer);
    _progVal = 100;
    el.style.width = '100%';
    setTimeout(() => { el.classList.remove('on'); el.style.width = '0'; _progVal = 0; }, 220);
  }

  /* ------------------------------------------------------------ 全屏遮罩（长任务） */
  let _ovTimer = null;

  function fmtMs(ms) {
    if (ms === null || ms === undefined) return '-';
    return ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(1) + ' s';
  }

  function overlay(on, text, detail) {
    let el = document.getElementById('overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'overlay';
      el.className = 'overlay';
      el.innerHTML = '<div class="box" role="status" aria-live="polite">' +
        '<div class="spin"></div><div class="t" id="ovText"></div>' +
        '<div class="d" id="ovDetail"></div></div>';
      document.body.appendChild(el);
    }
    clearInterval(_ovTimer);
    if (on) {
      document.getElementById('ovText').textContent = text || '处理中…';
      const d = document.getElementById('ovDetail');
      const t0 = Date.now();
      d.innerHTML = detail || '';
      el.classList.add('on');
      _ovTimer = setInterval(() => {
        d.innerHTML = (detail || '') +
          ' <span class="elapsed">已用时 ' + fmtMs(Date.now() - t0) + '</span>';
      }, 250);
    } else {
      el.classList.remove('on');
    }
  }

  /* ------------------------------------------------------------ 骨架屏 */
  function skeleton(rows, seedWidths) {
    const widths = seedWidths || [92, 78, 96, 70, 88, 74, 94, 66];
    let h = '<div class="skeleton" aria-hidden="true">';
    for (let i = 0; i < (rows || 6); i++) {
      h += '<div class="r" style="width:' + widths[i % widths.length] + '%"></div>';
    }
    return h + '</div>';
  }

  /* ------------------------------------------------------------ 复制（带降级） */
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand && document.execCommand('copy');
        ta.remove();
        return !!ok;
      } catch (e2) {
        return false;
      }
    }
  }

  /* ------------------------------------------------------------ 风险分布条 */
  function distBar(d) {
    d = d || {};
    const total = (d.high || 0) + (d.medium || 0) + (d.low || 0) + (d.none || 0);
    const pct = v => (total ? ((v || 0) / total * 100).toFixed(1) : '0') + '%';
    return '<div class="dist" role="img" aria-label="风险分布">' +
      '<i class="h" style="width:' + pct(d.high) + '"></i>' +
      '<i class="m" style="width:' + pct(d.medium) + '"></i>' +
      '<i class="l" style="width:' + pct(d.low) + '"></i>' +
      '<i class="n" style="width:' + pct(d.none) + '"></i></div>';
  }

  /* ------------------------------------------------------------ 客户端安全存储 */
  function save(key, val) {
    try { localStorage.setItem('cr-' + key, JSON.stringify(val)); } catch (e) { }
  }

  function load(key, dft) {
    try {
      const v = localStorage.getItem('cr-' + key);
      return v === null ? dft : JSON.parse(v);
    } catch (e) { return dft; }
  }

  initTheme();

  global.CR = {
    $, $$, api, toast, esc, esc2, money, riskTag, statusTag, wbTag,
    openModal, closeModal, renderTopbar, download, qs, busy, toggleTheme,
    overlay, progressStart, progressDone, fmtMs, skeleton, copyText, distBar,
    save, load,
    RISK_CLS, ST_CLS, WB_CLS
  };
})(window);
