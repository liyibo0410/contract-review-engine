/* ==========================================================================
   衡鉴 V2.1.7 · 前端公共脚本（原生 JS，零第三方依赖）
   v2.0：内置图标系统、动效工具、品牌顶栏与版权页脚。
   对外接口集中在 window.CR，所有页面共用；页面脚本只做各自的业务渲染。
   ========================================================================== */
(function (global) {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));

  const BRAND = {
    name: '衡鉴',
    full: '衡鉴 V2.1.7',        // 对外完整名称（品牌 + 对外版本标识）
    version: 'V2.1.7',
    owner: '衡鉴',
    en: 'HENGJIAN',
    desc: '企业合同智能审查系统'
  };

  /* ------------------------------------------------------------ 图标集
     统一 24×24 stroke 风格（currentColor 上色），避免用字符图形导致的
     「每个页面图标长得都不一样」与基线错位问题。 */
  const ICONS = {
    board: '<rect x="3" y="3" width="7.5" height="8.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="5" rx="1.6"/><rect x="13.5" y="11" width="7.5" height="10" rx="1.6"/><rect x="3" y="14.5" width="7.5" height="6.5" rx="1.6"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.4-4.4"/>',
    pulse: '<path d="M3 12h4l2.5-7 4 15 2.5-8h5"/>',
    shield: '<path d="M12 3l7.5 3v6c0 4.4-3 8.3-7.5 9.7C7.5 20.3 4.5 16.4 4.5 12V6z"/><path d="M9 12l2 2 4-4"/>',
    rules: '<path d="M4 6h16M4 12h16M4 18h10"/><circle cx="18.5" cy="18" r="2.2"/>',
    upload: '<path d="M12 16V4"/><path d="M7.5 8.5L12 4l4.5 4.5"/><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v4h-4"/>',
    download: '<path d="M12 4v12"/><path d="M7.5 11.5L12 16l4.5-4.5"/><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    bell: '<path d="M18 9a6 6 0 1 0-12 0c0 5-2 6.5-2 6.5h16S18 14 18 9z"/><path d="M10.5 19.5a2 2 0 0 0 3 0"/>',
    gauge: '<path d="M20.5 15a9 9 0 1 0-17 0"/><path d="M12 12.5l4-4"/><circle cx="12" cy="14" r="1.4"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
    checkCircle: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.2l2.6 2.6 4.6-5"/>',
    alert: '<path d="M12 4.5l8.5 15h-17z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.4" r=".9" fill="currentColor" stroke="none"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    cpu: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3"/>',
    db: '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6"/><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
    server: '<rect x="3" y="4" width="18" height="6.5" rx="1.8"/><rect x="3" y="13.5" width="18" height="6.5" rx="1.8"/><path d="M7 7.2h.01M7 16.8h.01"/>',
    scale: '<path d="M12 4v16"/><path d="M6 8h12"/><path d="M6 8l-3 5.5h6z"/><path d="M18 8l-3 5.5h6z"/><path d="M8.5 20h7"/>',
    lock: '<rect x="4.5" y="10.5" width="15" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14.5v2"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 6.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h1.5"/>',
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.2"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22"/>',
    chevron: '<path d="M6 9.5l6 6 6-6"/>',
    chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    save: '<path d="M5 3.5h11L20.5 8v12.5H5z"/><path d="M8.5 3.5v6h7v-6"/><path d="M8.5 20.5v-6h7v6"/>',
    trash: '<path d="M4.5 6.5h15"/><path d="M9 6.5V4.5h6v2"/><path d="M6.5 6.5l1 13h9l1-13"/><path d="M10.5 10.5v6M13.5 10.5v6"/>',
    link: '<path d="M10.5 13.5a4 4 0 0 0 5.6 0l3-3a4 4 0 0 0-5.6-5.6l-1 1"/><path d="M13.5 10.5a4 4 0 0 0-5.6 0l-3 3a4 4 0 0 0 5.6 5.6l1-1"/>',
    sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    bolt: '<path d="M13.5 3L5.5 13.5h5L9.5 21l8.2-10.5h-5.2z"/>',
    layers: '<path d="M12 3.5l8.5 4.6-8.5 4.6L3.5 8.1z"/><path d="M3.5 12.5L12 17l8.5-4.5"/><path d="M3.5 16.5L12 21l8.5-4.5"/>',
    filter: '<path d="M4 5.5h16l-6.2 7.3V19l-3.6 1.8v-8z"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    workflow: '<rect x="3" y="3.5" width="6.5" height="6" rx="1.6"/><rect x="14.5" y="14.5" width="6.5" height="6" rx="1.6"/><path d="M9.5 6.5h5.5a2 2 0 0 1 2 2v6"/><circle cx="17" cy="6.5" r="2.6"/>',
    users: '<circle cx="9" cy="8" r="3.4"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5.5a3.4 3.4 0 0 1 0 6.6"/><path d="M17.5 14.2A6 6 0 0 1 21 20"/>',
    eye: '<path d="M2.5 12S6 6.5 12 6.5 21.5 12 21.5 12 18 17.5 12 17.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    pen: '<path d="M4 20l.8-4.2L15.5 5a2.1 2.1 0 0 1 3 3L7.8 18.7z"/><path d="M13.5 7l3 3"/>',
    dot: '<circle cx="12" cy="12" r="4"/>'
  };

  function icon(name, cls, size) {
    const p = ICONS[name] || ICONS.dot;
    const s = size ? ' style="width:' + size + 'px;height:' + size + 'px"' : '';
    return '<svg class="ic' + (cls ? ' ' + cls : '') + '" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true"' + s + '>' + p + '</svg>';
  }

  /* ------------------------------------------------------------ 主题 */
  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem('cr-theme'); } catch (e) { saved = null; }
    if (saved !== 'light' && saved !== 'dark') saved = 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    return saved;
  }
  function toggleTheme() {
    const cur = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', cur);
    try { localStorage.setItem('cr-theme', cur); } catch (e) { }
    paintThemeBtn();
    toast(cur === 'dark' ? '已切换到深色控制台主题' : '已切换到浅色主题（适合长文本阅读与打印）', 'ok');
    return cur;
  }
  function paintThemeBtn() {
    const b = $('#themeBtn');
    if (!b) return;
    const dark = currentTheme() === 'dark';
    b.innerHTML = icon(dark ? 'sun' : 'moon', '', 14) + '<span>' + (dark ? '浅色' : '深色') + '</span>';
    b.title = dark ? '切换到浅色主题' : '切换到深色主题';
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
    el.innerHTML = (type === 'ok' ? icon('checkCircle', '', 15) + ' '
      : type === 'err' ? icon('alert', '', 15) + ' ' : '') + msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .28s'; }, 3400);
    setTimeout(() => el.remove(), 3780);
  }

  /* ------------------------------------------------------------ 格式化 */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function esc2(s) { return esc(s).replace(/\n/g, '<br>'); }

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

  /* 紧凑金额：给「宽度不可控」的位置用（统计卡、图表标签、窄屏表格）。
     合同金额可能只有几千，也可能上亿 —— 原来的 money() 会把上亿金额写成
     「¥1,234,567,890.00（123456.8 万）」共 27 个字符，在 170px 的统计卡里必然
     被 overflow:hidden 裁掉，用户看到的是半截数字。这里按中文读数习惯降档：
     亿 / 万 / 元，并保留 2 位有效小数，最长不超过 9 个字符。 */
  function moneyBrief(v, cur) {
    if (v === null || v === undefined || v === '') return '-';
    const n = Number(v);
    if (!isFinite(n)) return '-';
    const sym = (cur || 'CNY') === 'CNY' ? '¥' : '$';
    const unit = '<span class="unit">';
    if (n >= 1e8) return sym + (n / 1e8).toFixed(2) + unit + '亿</span>';
    if (n >= 1e4) {
      const w = n / 1e4;
      // 万位留 1 位小数足够（460.0 万 / 12.3 万），四位以上整数不再带小数
      return sym + (w >= 1000 ? Math.round(w).toLocaleString('zh-CN') : w.toFixed(1)) + unit + '万</span>';
    }
    return sym + n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* 自适应字号：容器放不下就把字号逐级往下压，压到下限仍放不下才允许折行。
     纯靠媒体查询给固定字号挡不住「数据驱动的超长文本」—— 金额长度由合同决定，
     不是设计能预设的。任何「数字/单号塞进固定宽度卡片」的地方都可以用它兜底。
     注意：调用前元素必须是「宽度已定」的块级元素（clientWidth 才有意义）。 */
  function fitText(el, opt) {
    if (!el) return;
    opt = opt || {};
    const min = opt.min || 13;
    const max = opt.max || 0;
    el.classList.remove('wrap');
    el.style.fontSize = '';
    const avail = el.clientWidth;
    if (!avail) return;
    const base = max || parseFloat(getComputedStyle(el).fontSize) || 26;
    let size = base;
    el.style.whiteSpace = 'nowrap';
    let guard = 0;
    while (el.scrollWidth > avail + 0.5 && size > min && guard++ < 80) {
      size -= 0.5;
      el.style.fontSize = size + 'px';
    }
    if (el.scrollWidth > avail + 0.5) {
      // 压到下限还是放不下 —— 交给折行，宁可两行也不要被裁掉半截
      el.style.fontSize = '';
      el.style.whiteSpace = '';
      el.classList.add('wrap');
    } else {
      el.style.fontSize = (size >= base - 0.01) ? '' : size + 'px';
    }
  }

  /* 统计卡统一做一次自适应。
     字号是**算出来的**，不会自己跟着容器变，所以必须在容器宽度变化时重算。
     这里用 ResizeObserver 而不是只监听 window.resize：媒体查询切换、父容器伸缩、
     无头浏览器改设备指标等场景都可能改变可用宽度，却不一定派发 window.resize。
     自激是安全的 —— `.v` 是块级元素，宽度由容器决定，改字号只影响高度，
     而回调只在**宽度**变化时才重算。 */
  function observeFit(el) {
    if (!window.ResizeObserver || el.__fitObs) return;
    el.__fitObs = new ResizeObserver(function () {
      const w = el.clientWidth;
      if (w && w !== el.__fitW) {
        el.__fitW = w;
        fitText(el, { min: 14 });
      }
    });
    el.__fitW = el.clientWidth;
    el.__fitObs.observe(el);
  }

  let _fitTimer = null;
  function fitStats(root) {
    const scope = root || document;
    const els = scope.querySelectorAll('.stat.amt .v, .stat .v[data-fit]');
    for (let i = 0; i < els.length; i++) {
      fitText(els[i], { min: 14 });
      observeFit(els[i]);
    }
  }
  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('resize', function () {
      clearTimeout(_fitTimer);
      _fitTimer = setTimeout(function () { fitStats(); }, 160);
    });
  }

  const RISK_CLS = { high: 't-high', medium: 't-mid', low: 't-low', none: 't-none' };
  const RISK_IC = { high: 'alert', medium: 'bolt', low: 'eye', none: 'checkCircle' };
  const RISK_COLOR = { high: 'var(--high)', medium: 'var(--mid)', low: 'var(--low)', none: 'var(--faint)' };

  function riskTag(level, label) {
    return '<span class="tag ' + (RISK_CLS[level] || 't-none') + '">' +
      icon(RISK_IC[level] || 'dot', '', 11) + esc(label || level) + '</span>';
  }
  const ST_CLS = {
    pending: 't-plain', parsing: 't-low', reviewing: 't-low', blocked: 't-high', completed: 't-ok'
  };
  const ST_IC = {
    pending: 'clock', parsing: 'refresh', reviewing: 'search', blocked: 'alert', completed: 'checkCircle'
  };
  function statusTag(status, label) {
    return '<span class="tag ' + (ST_CLS[status] || 't-plain') + '">' +
      icon(ST_IC[status] || 'dot', '', 11) + esc(label || status) + '</span>';
  }
  const WB_CLS = { not_written: 't-plain', writing: 't-low', success: 't-ok', failed: 't-high' };
  const WB_IC = { not_written: 'pen', writing: 'refresh', success: 'checkCircle', failed: 'alert' };
  function wbTag(status, label) {
    return '<span class="tag ' + (WB_CLS[status] || 't-plain') + '">' +
      icon(WB_IC[status] || 'dot', '', 11) + esc(label || status) + '</span>';
  }

  /* ------------------------------------------------------------ 弹层 */
  function openModal(id) { const m = $('#' + id); if (m) m.classList.add('on'); }
  function closeModal(id) { const m = $('#' + id); if (m) m.classList.remove('on'); }

  /* ------------------------------------------------------------ 品牌顶栏 */
  const NAV = [
    ['/', '审查大盘', 'board'],
    ['/workbench', '智能审查工作台', 'doc'],
    ['/twin', '动态孪生看板', 'pulse'],
    ['/rules', '合规规则与模板', 'shield']
  ];

  function renderTopbar(active, envText, envDetail) {
    const theme = currentTheme();
    const logoSrc = theme === 'light' ? '/assets/logo-sm-light.png' : '/assets/logo-sm.png';
    const html = [
      '<a class="brand" href="/" title="返回审查大盘">',
      '<img class="brand-logo" src="' + logoSrc + '" alt="' + BRAND.full + ' 标识" width="30" height="31">',
      '<span class="brand-txt"><b>' + BRAND.full + '</b>',
      // 副标题只放中文描述（英文全称进 title）：原来把英文一起塞进来，240px 上限下
      // 必然被 ellipsis 截断，而截断处又没有 title 兜底 —— 用户永远看不到全文。
      '<i title="' + BRAND.full + ' · ' + BRAND.desc + ' · intelligent contract review">' +
      BRAND.desc + '</i></span>',
      '</a>',
      '<nav class="nav" aria-label="主导航">' + NAV.map(n =>
        '<a href="' + n[0] + '"' + (n[0] === active ? ' class="active" aria-current="page"' : '') + '>' +
        n[1] + '</a>').join('') + '</nav>',
      '<div class="spacer"></div>',
      '<span class="role-slot"></span>',
      '<span class="env-tag" id="envTag" title="' +
        esc(envDetail || envText || '') + '">' + esc(envText || '本地演示环境') + '</span>',
      '<a class="btn sm ghost hide-md" href="/mock" title="模拟审批系统（美化查看页）">' +
        icon('external', '', 13) + '模拟审批系统</a>',
      '<button class="btn sm ghost compact-xs" id="themeBtn" onclick="CR.toggleTheme()"></button>'
    ].join('');
    const bar = $('#topbar');
    if (bar) bar.innerHTML = html;
    paintThemeBtn();
    mountFooter();
    renderRoleBar();
  }

  /* 版权页脚：所有页面统一注入，无需各页面各写一份 */
  function mountFooter() {
    if ($('.site-footer')) return;
    const f = document.createElement('footer');
    f.className = 'site-footer';
    const theme = currentTheme();
    f.innerHTML =
      '<div class="fl">' +
      '<img src="' + (theme === 'light' ? '/assets/logo-sm-light.png' : '/assets/logo-sm.png') + '" alt="' + BRAND.full + '">' +
      '<span class="sig">（' + BRAND.en + '）</span>' +
      '<span>© ' + new Date().getFullYear() + ' ' + BRAND.full + ' · 保留所有权利</span>' +
      '</div>' +
      '<div class="spacer"></div>' +
      '<div class="fl"><span>规则引擎判定 · 大模型辅以措辞 · 全程可离线运行</span>' +
      '<span class="dot-sep">|</span><a href="/twin">动态孪生看板</a>' +
      '<span class="dot-sep">|</span><a href="/mock/feishu/api/todo" target="_blank" rel="noopener">模拟审批系统</a>' +
      '</div>';
    document.body.appendChild(f);
  }

  /* 装饰层：光斑 + 扫描线（纯视觉，注入一次） */
  function mountDecor() {
    if (!$('.glow')) {
      const g1 = document.createElement('div'); g1.className = 'glow g1'; document.body.appendChild(g1);
      const g2 = document.createElement('div'); g2.className = 'glow g2'; document.body.appendChild(g2);
    }
    if (!$('.scanline')) {
      const s = document.createElement('div'); s.className = 'scanline'; document.body.appendChild(s);
    }
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

  function qs(name) { return new URLSearchParams(location.search).get(name); }

  function busy(btn, on, text) {
    if (!btn) return;
    if (on) {
      btn.dataset.old = btn.innerHTML;
      btn.innerHTML = '<span class="loading"></span> ' + (text || '处理中');
      btn.disabled = true;
    } else {
      if (btn.dataset.old !== undefined) btn.innerHTML = btn.dataset.old;
      btn.disabled = false;
    }
  }

  /* ------------------------------------------------------------ 请求进度 */
  let _progVal = 0, _progTimer = null, _progCount = 0;

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

  /* ------------------------------------------------------------ 遮罩 */
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

  /* ------------------------------------------------------------ 复制 */
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
      } catch (e2) { return false; }
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

  /* ------------------------------------------------------------ 存储 */
  function save(key, val) {
    try { localStorage.setItem('cr-' + key, JSON.stringify(val)); } catch (e) { }
  }
  function load(key, dft) {
    try {
      const v = localStorage.getItem('cr-' + key);
      return v === null ? dft : JSON.parse(v);
    } catch (e) { return dft; }
  }

  /* ------------------------------------------------------------ 入场动效
     观察器负责滚动入场；另外在页面稳定后补一次「视口内元素」检查，
     防止极端情况下（观察器回调丢失）元素停在隐藏态。 */
  let _io = null;
  function initReveal(scope) {
    const els = $$('.reveal', scope || document);
    if (!('IntersectionObserver' in window)) {
      els.forEach(e => e.classList.add('in'));
      return;
    }
    if (!_io) {
      _io = new IntersectionObserver(ents => {
        ents.forEach(en => {
          if (en.isIntersecting) { en.target.classList.add('in'); _io.unobserve(en.target); }
        });
      }, { rootMargin: '0px 0px -6% 0px', threshold: 0.05 });
    }
    els.forEach(e => { if (!e.classList.contains('in')) _io.observe(e); });
    setTimeout(() => {
      els.forEach(e => {
        if (e.classList.contains('in')) return;
        const r = e.getBoundingClientRect();
        if (r.top < window.innerHeight && r.bottom > 0) e.classList.add('in');
      });
    }, 700);
  }

  /* ------------------------------------------------------------ 数字滚动 */
  function countUp(el, target, opt) {
    if (!el) return;
    opt = opt || {};
    const dur = opt.duration || 1600;
    const dec = opt.decimal || 0;
    const suffix = opt.suffix || '';
    const prefix = opt.prefix || '';
    const gen = opt.thousands ? 'toLocaleString' : 'toString';
    const fmt = v => prefix + (dec > 0 ? v.toFixed(dec)
      : (opt.thousands ? Math.round(v).toLocaleString('zh-CN') : String(Math.round(v)))) +
      (suffix ? '<span class="unit">' + suffix + '</span>' : '');
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      el.innerHTML = fmt(target);
      return;
    }
    const t0 = performance.now();
    function tick(now) {
      const p = Math.min((now - t0) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.innerHTML = fmt(target * eased);
      if (p < 1) requestAnimationFrame(tick);
      else el.innerHTML = fmt(target);
    }
    requestAnimationFrame(tick);
  }

  /* ------------------------------------------------------------ 迷你柱状图 */
  function spark(container, values, color) {
    if (!container) return;
    const vals = (values && values.length) ? values : [40, 55, 35, 70, 50, 80, 60, 92];
    const max = Math.max.apply(null, vals.concat([1]));
    container.innerHTML = vals.map((v, i) =>
      '<i class="bar" style="height:' + Math.max(6, v / max * 100) + '%;background:' +
      (typeof color === 'function' ? color(i, v) : (color || 'var(--brand)')) + '"></i>'
    ).join('');
  }

  /* ------------------------------------------------------------ 初始化 */
  initTheme();
  mountDecor();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initReveal(); cfxEnter(); cfxWatch();
    });
  } else {
    initReveal();
    cfxEnter();
    cfxWatch();
  }

  /* ------------------------------------------------------------ v2.1.3 卡片动效
     入场错峰只播一次（按容器记忆），常驻效果交给 CSS 的 :is(...) 选择器 ——
     这样定时刷新重建的节点仍带悬停效果，但不会重播入场动画。 */
  const CFX_ENTER_SEL = [
    '.panel', '.card', '.stat', '.kpi', '.tk', '.kv-cell', '.risk-card',
    '.mode-col', '.mock-item', '.mock-kv .cell', '.mock-comment',
    '.login-card', '.role-item', '.deny-card', '.deny-cell',
    '.meter', '.pipe-node', '.sec-item', '.model-card', '.store-card'
  ].join(',');
  /* 上浮只给小卡片；大面板只提亮边框 —— 大块位移在栅格里很晃眼 */
  const CFX_LIFT_SEL = [
    '.stat', '.kpi', '.tk', '.kv-cell', '.risk-card', '.mode-col', '.mock-item',
    '.role-item', '.meter', '.deny-cell', '.mock-kv .cell', '.store-card'
  ].join(',');
  /* 这些卡片的 ::before 是强调条，适合让光沿着它缓缓流过 */
  const CFX_BEAM_SEL = '.panel, .stat, .kpi, .tk';
  const CFX_PREV = {};

  function cfxDecorate(el) {
    if (el.__cfx) { return; }
    el.__cfx = 1;
    el.classList.add('cfx');
    if (el.matches(CFX_LIFT_SEL)) { el.classList.add('cfx-lift'); }
    if (el.matches(CFX_BEAM_SEL)) { el.classList.add('cfx-beam'); }
  }

  /* 自动补装饰：页面上的卡片大量是**异步渲染 / 交互后重建**出来的
     （大盘的自动流转与队列面板、模拟审批页点选后重建的列表……），
     靠「在每个渲染点手调一次」必然漏 —— 实测就漏了 6 个 kv-cell 和 8 个 mock-item。
     用一个 MutationObserver 兜住所有新插入的节点，只做装饰、不做入场动画
     （入场仍然只播一次，见 cfxEnter）。 */
  function cfxWatch() {
    if (!global.MutationObserver || !document.body) { return null; }
    const ob = new MutationObserver(function (muts) {
      let n = 0;
      for (let i = 0; i < muts.length; i++) {
        const added = muts[i].addedNodes;
        for (let j = 0; j < added.length; j++) {
          const node = added[j];
          if (!node || node.nodeType !== 1) { continue; }
          if (node.matches && node.matches(CFX_ENTER_SEL)) { cfxDecorate(node); n++; }
          if (node.querySelectorAll) {
            const inner = node.querySelectorAll(CFX_ENTER_SEL);
            for (let k = 0; k < inner.length; k++) { cfxDecorate(inner[k]); n++; }
          }
        }
      }
      return n;
    });
    ob.observe(document.body, { childList: true, subtree: true });
    return ob;
  }

  /* 卡片入场 + 常驻效果。
     两个关键点（都是踩坑后改的）：
       1) **常驻效果每次都补** —— 页面是动态渲染的，卡片会反复重建；
          若只在首次补类，之后新建的卡片就没有悬停/流光效果；
       2) **入场动画用内联样式写** —— 靠运行时加类再靠 CSS 命中，会受层叠顺序影响
          （实测：规则在样式表里、类也加上了，计算样式却是 animation-name: none）。
          直接写内联 animation 最可靠，跑完清掉，不残留。 */
  function cfxEnter(scope) {
    const s = scope || document;
    const cards = $$(CFX_ENTER_SEL, s);
    cards.forEach(cfxDecorate);                 // ① 常驻效果：每次都补
    if (s.__cfx) { return cards.length; }       // ② 入场：同一容器只播一次
    s.__cfx = 1;
    cards.forEach(function (el, i) {
      const delay = Math.min(i, 14) * 32;
      el.style.animation = 'cfxIn .52s cubic-bezier(.22, .68, .36, 1) both';
      el.style.animationDelay = delay + 'ms';
      setTimeout(function () {
        el.style.animation = '';
        el.style.animationDelay = '';
      }, 560 + delay);
    });
    return cards.length;
  }

  /* 数值变化检测：返回 true 表示"这次和上次不一样"。
     比对表放在 JS 里而不是 DOM 上 —— innerHTML 重写会把 DOM 上的记忆冲掉。 */
  function cfxChanged(key, val) {
    const had = Object.prototype.hasOwnProperty.call(CFX_PREV, key);
    const changed = had && CFX_PREV[key] !== val;
    CFX_PREV[key] = val;
    return changed;
  }

  /* ------------------------------------------------------------ v2.0 角色与权限
     说明：前端只做**显隐与提示**，真正的拦截一律在服务端（app/auth.py 的路由权限表）。
     把按钮藏起来是体验，不是安全边界 —— 手工构造请求照样会被 403 拦下并写审计。 */
  let ME = null;

  function authMe(force) {
    if (ME && !force) return Promise.resolve(ME);
    return api('GET', '/api/auth/me').then(function (m) { ME = m; return m; });
  }

  function can(cap) {
    if (!ME) return false;
    if (!cap || cap === 'auth') return !!ME.role;
    return (ME.caps || []).indexOf(cap) >= 0;
  }

  /* 把 [data-cap] 元素按能力显隐；可选把无权限元素直接移除（remove=true） */
  function gate(scope, remove) {
    $$('[data-cap]', scope || document).forEach(function (el) {
      var ok = can(el.getAttribute('data-cap'));
      if (ok) { el.classList.remove('gated'); el.removeAttribute('title'); return; }
      if (remove) { el.remove(); return; }
      el.classList.add('gated');
      el.setAttribute('title', '当前角色无此权限：' + (el.getAttribute('data-cap-label') || el.getAttribute('data-cap')));
      if (el.tagName === 'BUTTON') el.disabled = true;
    });
  }

  /* 按角色隐藏无权限的导航项 */
  function gateNav() {
    $$('.nav a').forEach(function (a) {
      var path = (a.getAttribute('href') || '').split('?')[0];
      if (path === '/rules' && !can('edit_rules')) a.style.display = 'none';
    });
  }

  function roleSwitch(role, next) {
    return api('POST', '/api/auth/switch', { role: role }).then(function () {
      location.href = next || location.pathname + location.search;
    });
  }

  function logout() {
    return api('POST', '/api/auth/logout', {}).then(function () {
      location.href = '/login';
    });
  }

  function paintRoleChip() {
    var el = $('#roleChip');
    if (el && ME) {
      el.innerHTML = '<span class="rc-role">' + esc(ME.role_label) + '</span>' +
        '<span class="rc-user">' + esc(ME.username) + '</span>';
      el.title = ME.role_desc || '';
    }
    var sw = $('#roleSwitch');
    if (sw) {
      sw.innerHTML = (ME && ME.demo_switch ? (ME.roles || []).map(function (r) {
        return '<option value="' + r.role + '"' + (r.role === ME.role ? ' selected' : '') +
          '>' + esc(r.label) + '</option>';
      }).join('') : '');
      sw.style.display = (ME && ME.demo_switch) ? '' : 'none';
    }
  }

  function initAuth(active, envText, envDetail) {
    return authMe(true).then(function (m) {
      renderTopbar(active, envText, envDetail);
      paintRoleChip();
      gateNav();
      gate();
      return m;
    });
  }

  /* ------------------------------------------------------------ 顶栏扩展 */
  function renderRoleBar() {
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
  }

  global.CR = {
    $, $$, api, toast, esc, esc2, money, moneyBrief, riskTag, statusTag, wbTag,
    openModal, closeModal, renderTopbar, download, qs, busy, toggleTheme,
    overlay, progressStart, progressDone, fmtMs, skeleton, copyText, distBar,
    save, load, icon, countUp, spark, initReveal, fitText, fitStats,
    motion: { enter: cfxEnter, changed: cfxChanged },
    authMe, can, gate, gateNav, roleSwitch, logout, initAuth, renderRoleBar,
    me: function () { return ME; },
    RISK_CLS, ST_CLS, WB_CLS, RISK_COLOR, BRAND
  };
})(window);
