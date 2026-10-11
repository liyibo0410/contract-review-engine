/* ==========================================================================
   动态图表组件库（charts.js）· 纯内联 SVG + CSS，零第三方依赖
   --------------------------------------------------------------------------
   设计铁律：组件返回的元素几何全为「终态」，动画只负责运动。
   因此图表在任何情况下都是可见的，不会因动画未执行而消失。
   对外接口：window.CH
   ========================================================================== */
(function (global) {
  'use strict';

  const esc = s => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const svgOpen = (w, h, cls) =>
    '<svg class="' + (cls || '') + '" viewBox="0 0 ' + w + ' ' + h + '" ' +
    'preserveAspectRatio="xMidYMid meet" role="img">';

  /* ------------------------------------------------------------ 甜甜圈 */
  function donut(el, items, opt) {
    if (!el) return;
    opt = opt || {};
    const size = opt.size || 132;
    const sw = opt.stroke || 14;
    const r = (size - sw) / 2 - 1;
    const c = 2 * Math.PI * r;
    const cx = size / 2;
    const total = items.reduce((a, b) => a + (b.value || 0), 0);

    let acc = 0;
    const segs = items.map(it => {
      const v = it.value || 0;
      const len = total ? (v / total) * c : 0;
      const off = -acc;
      acc += len;
      return '<circle class="seg" cx="' + cx + '" cy="' + cx + '" r="' + r +
        '" fill="none" stroke="' + (it.color || 'var(--brand)') + '" stroke-width="' + sw +
        '" stroke-dasharray="' + len.toFixed(2) + ' ' + (c - len).toFixed(2) +
        '" stroke-dashoffset="' + off.toFixed(2) + '"><title>' +
        esc(it.label) + '：' + v + '</title></circle>';
    }).join('');

    const centerBig = opt.centerValue !== undefined ? opt.centerValue : total;
    const centerSm = opt.centerLabel || '';

    el.innerHTML = '<div class="donut" style="width:' + size + 'px;height:' + size + 'px">' +
      '<svg viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="' +
      esc(opt.aria || '分布图') + '">' +
      '<circle cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" ' +
      'stroke="var(--line-2)" stroke-width="' + sw + '"/>' + segs + '</svg>' +
      '<div class="ctr"><span class="big num">' + esc(centerBig) + '</span>' +
      (centerSm ? '<span class="sm">' + esc(centerSm) + '</span>' : '') + '</div></div>';

    if (opt.legend !== false) {
      const lg = document.createElement('div');
      lg.className = 'donut-legend';
      lg.innerHTML = items.map(it => {
        const pct = total ? Math.round((it.value || 0) / total * 100) : 0;
        return '<div class="lg"><span class="dot" style="background:' + (it.color || 'var(--brand)') +
          '"></span><span class="nm">' + esc(it.label) + '</span>' +
          '<span class="vl">' + (it.value || 0) + ' <span class="faint">' + pct + '%</span></span></div>';
      }).join('');
      el.appendChild(lg);
    }
    el.classList.add('donut-wrap');
  }

  /* ------------------------------------------------------------ 环形仪表 */
  function gauge(pct, opt) {
    opt = opt || {};
    const size = opt.size || 68;
    const sw = opt.stroke || 6;
    const r = (size - sw) / 2 - 1;
    const c = 2 * Math.PI * r;
    const v = Math.max(0, Math.min(100, Number(pct) || 0));
    const color = opt.color || 'var(--cyan)';
    const cx = size / 2;
    return '<svg viewBox="0 0 ' + size + ' ' + size + '" style="width:' + size + 'px;height:' + size + 'px">' +
      '<circle class="track" cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" stroke-width="' + sw + '"/>' +
      '<circle class="prog" cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" ' +
      'stroke="' + color + '" stroke-width="' + sw + '" stroke-dasharray="' + c.toFixed(1) + '" ' +
      'stroke-dashoffset="' + (c * (1 - v / 100)).toFixed(1) + '" ' +
      'style="filter:drop-shadow(0 0 6px ' + color + ')"/>' +
      '</svg>';
  }

  /* 更新一个已渲染的仪表（用于实时刷新） */
  function setGauge(progEl, valEl, pct, opt) {
    opt = opt || {};
    const v = Math.max(0, Math.min(100, Number(pct) || 0));
    if (progEl) {
      const r = parseFloat(progEl.getAttribute('r')) || 26;
      const c = 2 * Math.PI * r;
      progEl.setAttribute('stroke-dashoffset', (c * (1 - v / 100)).toFixed(1));
    }
    if (valEl) valEl.textContent = Math.round(v) + '%';
  }

  /* ------------------------------------------------------------ 横向条形 */
  function hbars(el, items, opt) {
    if (!el) return;
    opt = opt || {};
    const max = opt.max || Math.max.apply(null, items.map(i => i.value || 0).concat([1]));
    el.className = (el.className || '') + ' hbars';
    el.innerHTML = items.map(it => {
      const v = it.value || 0;
      const w = Math.max(2, v / max * 100);
      const color = it.color || 'linear-gradient(90deg,var(--cyan),var(--brand))';
      return '<div class="hbar"><div class="hb-top">' +
        '<span class="nm">' + esc(it.label) + '</span>' +
        (it.sub ? '<span class="sub">' + esc(it.sub) + '</span>' : '') +
        '<span class="vl" style="color:' + (it.textColor || 'var(--ink)') + '">' +
        esc(it.display !== undefined ? it.display : v) + '</span></div>' +
        '<div class="hb-track"><div class="hb-fill" style="width:' + w.toFixed(1) + '%;background:' + color + '"></div></div>' +
        '</div>';
    }).join('');
    requestAnimationFrame(() => el.classList.add('in'));
  }

  /* ------------------------------------------------------------ 面积趋势图 */
  function area(el, series, opt) {
    if (!el) return;
    opt = opt || {};
    const W = 640, H = opt.height || 170, PT = 16, PB = 26, PL = 30, PR = 12;
    const vals = series.values || [];
    if (!vals.length) { el.innerHTML = ''; return; }
    const max = opt.max || (Math.max.apply(null, vals) * 1.18 || 1);
    const min = 0;
    const iw = W - PL - PR, ih = H - PT - PB;
    const step = vals.length > 1 ? iw / (vals.length - 1) : 0;
    const xy = (v, i) => [
      PL + i * step,
      PT + ih - ((v - min) / (max - min)) * ih
    ];
    const pts = vals.map((v, i) => xy(v, i));
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const areaPath = line + ' L' + (PL + iw).toFixed(1) + ' ' + (PT + ih) + ' L' + PL + ' ' + (PT + ih) + ' Z';
    const color = series.color || 'var(--brand)';
    const gid = 'ag' + Math.random().toString(36).slice(2, 8);

    // 网格线 + y 轴刻度
    let grid = '', axis = '';
    for (let i = 0; i <= 3; i++) {
      const y = PT + ih * (i / 3);
      const val = Math.round(max * (1 - i / 3));
      grid += '<line class="grid-l" x1="' + PL + '" y1="' + y.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + y.toFixed(1) + '"/>';
      axis += '<text class="axis" x="' + (PL - 6) + '" y="' + (y + 3.5).toFixed(1) + '" text-anchor="end">' + val + '</text>';
    }
    // x 轴标签（最多 7 个）
    const labels = series.labels || [];
    const stride = Math.max(1, Math.ceil(labels.length / 7));
    labels.forEach((lb, i) => {
      if (i % stride) return;
      axis += '<text class="axis" x="' + (PL + i * step).toFixed(1) + '" y="' + (H - 8) +
        '" text-anchor="middle">' + esc(lb) + '</text>';
    });

    const dots = pts.map((p, i) =>
      '<circle class="pt" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3" ' +
      'fill="var(--panel-solid)" stroke="' + color + '"><title>' +
      esc((labels[i] || ('#' + (i + 1)))) + '：' + vals[i] + '</title></circle>').join('');

    el.innerHTML = svgOpen(W, H, '') +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + color + '" stop-opacity=".38"/>' +
      '<stop offset="100%" stop-color="' + color + '" stop-opacity="0"/></linearGradient></defs>' +
      grid + axis +
      '<path class="area" d="' + areaPath + '" fill="url(#' + gid + ')"/>' +
      '<path class="line" d="' + line + '" stroke="' + color + '"/>' +
      dots + '</svg>';
    el.classList.add('trend');
  }

  /* ------------------------------------------------------------ 分组柱状 */
  function groupBars(el, groups, opt) {
    if (!el) return;
    opt = opt || {};
    el.innerHTML = groups.map(g => {
      const max = Math.max.apply(null, g.values.concat([1]));
      const bars = g.values.map((v, i) =>
        '<div class="pb" style="height:' + Math.max(3, v / max * 100) + '%;background:' +
        (g.color || 'var(--brand)') + '"><span class="pl">' +
        Math.round(v) + (g.unit || '') + '</span><span class="pl-x">' +
        esc((g.xlabels && g.xlabels[i]) || (g.labels && g.labels[i]) || '') + '</span></div>').join('');
      return '<div class="perf-block"><div class="perf-head"><span class="pl">' + esc(g.title) +
        '</span><span class="pv">' + esc(g.summary || '') + '</span></div>' +
        '<div class="perf-bars">' + bars + '</div></div>';
    }).join('');
  }

  /* ------------------------------------------------------------ 处理流水线 */
  function pipeline(el, nodes, opt) {
    if (!el) return;
    opt = opt || {};
    const n = nodes.length;
    const NW = 120, NH = 76, GAP = 25, X0 = 30, Y0 = 62;
    const W = X0 * 2 + n * NW + (n - 1) * GAP;
    const H = opt.height || 232;
    const cy = Y0 + NH / 2;

    let conns = '';
    for (let i = 0; i < n - 1; i++) {
      const x1 = X0 + i * (NW + GAP) + NW;
      const x2 = X0 + (i + 1) * (NW + GAP);
      conns += '<line class="conn" x1="' + x1 + '" y1="' + cy + '" x2="' + x2 + '" y2="' + cy +
        '" marker-end="url(#parr)"/>';
    }

    // 异常分支：解析受阻 → 阻塞重试（虚线回路）
    const bx = X0 + 1 * (NW + GAP) + NW / 2;
    const loop = '<path class="conn-loop" d="M' + bx + ' ' + (Y0 + NH) +
      ' V' + (Y0 + NH + 26) + ' H' + (bx + 150) + ' V' + (Y0 + NH + 26) +
      '" marker-end="url(#parrA)"/>' +
      '<rect x="' + (bx + 152) + '" y="' + (Y0 + NH + 14) + '" width="132" height="24" rx="7" ' +
      'fill="var(--mid-bg)" stroke="var(--mid-line)"/>' +
      '<text x="' + (bx + 218) + '" y="' + (Y0 + NH + 30) + '" text-anchor="middle" ' +
      'font-size="11" fill="var(--mid)">解析受阻 · 阻塞重试</text>';

    const nodeHtml = nodes.map((nd, i) => {
      const x = X0 + i * (NW + GAP);
      const color = nd.color || 'var(--brand)';
      return '<g class="node-g" id="' + (nd.id || ('node' + (i + 1))) + '" style="color:' + color + '">' +
        '<rect class="nd-glow" x="' + (x - 3) + '" y="' + (Y0 - 3) + '" width="' + (NW + 6) +
        '" height="' + (NH + 6) + '" rx="14" fill="none" stroke="' + color + '" stroke-width="2"/>' +
        '<rect class="nd-rect" x="' + x + '" y="' + Y0 + '" width="' + NW + '" height="' + NH +
        '" rx="11" fill="var(--panel-2)" stroke="var(--line)"/>' +
        '<rect x="' + x + '" y="' + Y0 + '" width="3" height="' + NH + '" rx="1.5" fill="' + color + '"/>' +
        '<circle class="nd-no" cx="' + (x + 20) + '" cy="' + (Y0 + 22) + '" r="10" fill="' + color + '"/>' +
        '<text x="' + (x + 20) + '" y="' + (Y0 + 26) + '" text-anchor="middle" font-size="10" ' +
        'font-weight="700" fill="#04101f">' + (i + 1) + '</text>' +
        '<text x="' + (x + 36) + '" y="' + (Y0 + 26) + '" font-size="12.5" font-weight="650" ' +
        'fill="var(--ink)">' + esc(nd.title) + '</text>' +
        '<text x="' + (x + 14) + '" y="' + (Y0 + 56) + '" font-size="10.5" fill="var(--faint)">' +
        esc(nd.sub || '') + '</text>' +
        '</g>';
    }).join('');

    // 沿线流动的光点
    let dots = '';
    for (let i = 0; i < n - 1; i++) {
      const x1 = X0 + i * (NW + GAP) + NW;
      const x2 = X0 + (i + 1) * (NW + GAP);
      dots += '<circle class="flow-dot" r="2.6" fill="var(--cyan)" style="color:var(--cyan)">' +
        '<animateMotion dur="2.4s" repeatCount="indefinite" begin="' + (i * 0.3).toFixed(2) + 's" ' +
        'path="M' + x1 + ' ' + cy + ' L' + x2 + ' ' + cy + '"/></circle>';
    }

    el.innerHTML = svgOpen(W, H, 'pipeline-svg') +
      '<defs>' +
      '<marker id="parr" markerWidth="7" markerHeight="7" refX="5.4" refY="3" orient="auto">' +
      '<path d="M0 0L6 3L0 6z" fill="rgba(79,140,255,.6)"/></marker>' +
      '<marker id="parrA" markerWidth="7" markerHeight="7" refX="5.4" refY="3" orient="auto">' +
      '<path d="M0 0L6 3L0 6z" fill="rgba(251,191,36,.7)"/></marker>' +
      '</defs>' +
      '<text x="' + X0 + '" y="26" font-size="12" font-weight="700" fill="var(--sub)" ' +
      'letter-spacing="1">审查流水线 · PIPELINE</text>' +
      '<text x="' + (W - X0) + '" y="26" text-anchor="end" font-size="11" fill="var(--faint)">' +
      '虚线为实时数据流，异常时走下方分支</text>' +
      conns + loop + nodeHtml + dots + '</svg>';
    return { width: W, height: H, ids: nodes.map((nd, i) => nd.id || ('node' + (i + 1))) };
  }

  /* ------------------------------------------------------------ 实时时钟 */
  function clock(elTime, elDate) {
    const WD = ['日', '一', '二', '三', '四', '五', '六'];
    function paint() {
      const d = new Date();
      const p = n => String(n).padStart(2, '0');
      if (elTime) elTime.textContent = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
      if (elDate) elDate.textContent = d.getFullYear() + ' 年 ' + (d.getMonth() + 1) + ' 月 ' +
        d.getDate() + ' 日 · 星期' + WD[d.getDay()];
    }
    paint();
    setInterval(paint, 1000);
  }

  /* ------------------------------------------------------------ 模拟波动 */
  function walk(cur, min, max, delta) {
    const next = cur + (Math.random() - 0.5) * 2 * (delta || 4);
    return Math.max(min, Math.min(max, next));
  }

  global.CH = { donut, gauge, setGauge, hbars, area, groupBars, pipeline, clock, walk, esc };
})(window);
