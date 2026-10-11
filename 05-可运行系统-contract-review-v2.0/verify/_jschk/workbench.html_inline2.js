
let CASE_ID = Number(CR.qs('id') || 1);
let D = null;
let PAGE = 1;
let TAB = 'risk';
let RLEVEL = '';           // 风险等级筛选：'' / high / medium / low
let CURSOR = -1;           // 键盘导航游标
let PF = null;             // 当前弹层展示的评论正文（供复制）

CR.initAuth('/workbench', '演示环境 · 在线审查');

/* ------------------------------------------------------------------ 载入 */
async function load() {
  CR.$('#doc').innerHTML = CR.skeleton(10);
  CR.$('#tabBody').innerHTML = CR.skeleton(6);
  try {
    D = await CR.api('GET', '/api/cases/' + CASE_ID);
  } catch (e) {
    CR.$('#doc').innerHTML = '<div class="empty">加载失败：' + CR.esc(e.message) +
      '<br><button class="btn sm" style="margin-top:10px" onclick="load()">重试</button></div>';
    CR.$('#tabBody').innerHTML = '';
    return;
  }
  renderHead();
  renderBlocked();
  renderDiag();
  renderDoc();
  renderTabs();
  renderSide();
  renderWriteback();
  CR.motion.enter();            // 双栏面板与风险卡片错峰入场
  CR.motion.enter(CR.$('#tabBody'));
  fitDoc();                     // 内容变了，正文卡的高度上限按实测重算一次
  loadNotifyStatus();
  if (CR.gate) CR.gate();            // 动态渲染出来的按钮也要按能力显隐
  watchStatus();
  CR.initReveal();
}

/* -------------------------------------------------- v2.0 流转过程可见（M9）
   任务处于 pending / parsing / reviewing 时按 1s 轮询，把「正在解析 → 正在审查」
   的过程显示出来，而不是让用户对着一个静态页面猜系统是不是卡住了。 */
let WATCH = null;
const RUNNING = ['pending', 'parsing', 'reviewing'];

function watchStatus() {
  if (WATCH) { clearInterval(WATCH); WATCH = null; }
  if (!D || !D.case || RUNNING.indexOf(D.case.review_status) < 0) return;
  let ticks = 0;
  WATCH = setInterval(async function () {
    ticks++;
    try {
      const c = await CR.api('GET', '/api/cases/' + CASE_ID);
      const st = c.case ? c.case.review_status : '';
      if (RUNNING.indexOf(st) < 0 || ticks > 240) {
        clearInterval(WATCH); WATCH = null;
        if (RUNNING.indexOf(st) < 0) { await load(); CR.toast('审查已完成，页面已刷新', 'ok'); }
        return;
      }
      const lab = { pending: '待处理', parsing: '解析中', reviewing: '审查中' }[st] || st;
      const tag = CR.$('#hTags');
      if (tag) {
        let t = CR.$('#liveTag');
        if (!t) {
          t = document.createElement('span');
          t.id = 'liveTag';
          t.className = 'tag run';
          tag.appendChild(t);
        }
        t.textContent = lab + ' · 第 ' + ticks + ' 秒';
      }
    } catch (e) { /* 忽略单次轮询失败，下一轮继续 */ }
  }, 1000);
}

function renderHead() {
  const c = D.case;
  CR.$('#hTitle').innerHTML = CR.icon('doc', '', 21) + CR.esc(c.title);
  CR.$('#hSub').textContent = c.case_no + ' · ' + c.source_label +
    (c.approval_instance ? ' · ' + c.approval_instance : '') +
    ' · 文件指纹 ' + (c.doc_sha256 || '').slice(0, 16);
  CR.$('#hTags').innerHTML =
    CR.statusTag(c.review_status, c.review_status_label) +
    CR.riskTag(c.risk_level, c.risk_level_label) +
    '<span class="tag ' + (c.conclusion === 'reject' ? 't-high' : c.conclusion === 'rectify' ? 't-mid' : c.conclusion === 'pass' ? 't-ok' : 't-none') + '">' + CR.esc(c.conclusion_label) + '</span>' +
    CR.wbTag(c.writeback_status, c.writeback_status_label);
  document.title = c.title + ' · 智能审查工作台 · 亦大';
}

function renderBlocked() {
  const c = D.case;
  const box = CR.$('#blockedBox');
  if (c.review_status !== 'blocked') { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="notice bad" style="margin-bottom:14px">' +
    '<div class="row"><span class="tag t-high">解析受阻</span>' +
    '<b>合同未能进入自动审查</b><div class="spacer" style="flex:1"></div>' +
    '<button class="btn sm primary" onclick="doRetry()">重新审查</button></div>' +
    '<div style="font-size:12.5px;margin-top:6px">' + CR.esc(c.blocked_reason || '') + '</div>' +
    '<div style="font-size:12px;margin-top:6px;color:var(--sub)">' +
    '处理建议：① 加密文件请解密后重新上传；② 图片型 PDF 请上传清晰扫描件图片（系统走内置 OCR）；' +
    '③ 旧版 .doc 请另存为 .docx。修正文件后点「重新审查」，任务会重新进入流转流水线。</div></div>';
}

function renderDiag() {
  const box = CR.$('#diagBox');
  const diag = D.rule_diag || [];
  const parts = [];
  if (diag.length) {
    parts.push('<div class="notice warn" style="margin-bottom:14px">' +
      '<div class="nt">规则库存在无效正则（该规则已跳过匹配）</div>' +
      diag.slice(0, 6).map(d => CR.esc(d.where) + ' → ' + CR.esc(d.error) +
        '<br><span class="mono" style="font-size:11px">' + CR.esc(d.pattern) + '</span>').join('<br>') +
      '<div style="margin-top:6px"><a href="/rules">前往规则页修正 →</a></div></div>');
  }
  if (D.paragraphs_truncated) {
    parts.push('<div class="notice warn" style="margin-bottom:14px">文档段落数 ' +
      D.paragraphs_total + ' 超过展示上限，正文视窗仅显示前 ' +
      (D.paragraphs || []).length + ' 段（审查与命中锚点不受影响）。</div>');
  }
  box.innerHTML = parts.join('');
}

/* ------------------------------------------------------------------ 正文视窗 */
function risksByPara() {
  const map = {};
  (D.results || []).forEach(r => {
    if (r.char_start === null || r.char_start === undefined
        || r.para_index === null || r.para_index === undefined) return;
    (map[r.para_index] = map[r.para_index] || []).push(r);
  });
  return map;
}

function span(t) { return t ? CR.esc(t) : ''; }

function paraHtml(p, risks) {
  const text = p.text;
  if (!risks || !risks.length) return span(text);
  const S = p.char_start;
  const marks = risks.map(r => ({
    a: Math.max(0, r.char_start - S),
    b: Math.min(text.length, r.char_end - S),
    lv: r.risk_level, id: r.id
  })).filter(m => m.b > m.a).sort((x, y) => x.a - y.a);
  let out = '', cur = 0;
  marks.forEach(m => {
    if (m.a < cur) return;
    out += span(text.slice(cur, m.a));
    out += '<mark class="hl ' + m.lv + '" data-rid="' + m.id +
      '" tabindex="0" role="button" title="点击定位到右侧风险卡片"' +
      ' onclick="pick(' + m.id + ')"' +
      ' aria-label="定位到风险项 ' + m.id + '">' +
      CR.esc(text.slice(m.a, m.b)) + '</mark>';
    cur = m.b;
  });
  return out + span(text.slice(cur));
}

function pageList() {
  const pages = {};
  (D.paragraphs || []).forEach(p => { (pages[p.page] = pages[p.page] || []).push(p); });
  return Object.keys(pages).map(Number).sort((a, b) => a - b);
}

function renderPager(pageNums) {
  const cur = PAGE, total = pageNums.length;
  const nums = [];
  const push = n => { if (n >= 1 && n <= total && nums.indexOf(n) < 0) nums.push(n); };
  push(1); push(total);
  for (let i = cur - 1; i <= cur + 1; i++) push(i);
  if (cur <= 3) for (let i = 1; i <= Math.min(4, total); i++) push(i);
  if (cur >= total - 2) for (let i = Math.max(1, total - 3); i <= total; i++) push(i);
  nums.sort((a, b) => a - b);

  let html = '<button class="pg" onclick="goPage(' + Math.max(1, cur - 1) +
    ')" title="上一页" aria-label="上一页"' + (cur <= 1 ? ' disabled' : '') + '>‹</button>';
  let prev = 0;
  nums.forEach(n => {
    if (prev && n - prev > 1) html += '<span class="ellipsis">…</span>';
    html += '<button class="pg ' + (n === cur ? 'on' : '') +
      '" onclick="goPage(' + n + ')">' + n + '</button>';
    prev = n;
  });
  html += '<button class="pg" onclick="goPage(' + Math.min(total, cur + 1) +
    ')" title="下一页" aria-label="下一页"' + (cur >= total ? ' disabled' : '') + '>›</button>';
  html += '<span class="lab">跳至</span><input type="number" min="1" max="' + total +
    '" value="' + cur + '" onchange="goPage(Math.max(1,Math.min(' + total +
    ',+this.value||1)))" aria-label="跳转到页码">';
  CR.$('#pager').innerHTML = html;
}

function renderDoc() {
  const d = D.document;
  const pageNums = pageList();
  if (!pageNums.length) {
    CR.$('#doc').innerHTML = '<div class="empty">无可展示的正文段落' +
      (D.case.review_status === 'blocked' ? '（文档解析受阻）' : '') +
      '。<br><span class="faint" style="font-size:12px">' +
      '该合同可能尚未审查，或解析未产出有效文本。</span></div>';
    CR.$('#pager').innerHTML = '';
    CR.$('#docInfo').innerHTML = d ? (CR.esc(d.parse_engine) + ' · ' + d.parse_status) : '';
    return;
  }
  if (pageNums.indexOf(PAGE) < 0) PAGE = pageNums[0];

  CR.$('#docInfo').innerHTML = d
    ? (CR.esc(d.filename) + ' ｜ 引擎 <span class="mono">' + CR.esc(d.parse_engine) +
       '</span> ｜ ' + d.page_count + ' 页 / ' + d.para_count + ' 段 / ' + d.char_count + ' 字符' +
       (d.ocr_confidence ? (' ｜ OCR ' + d.ocr_confidence) : '') +
       (d.need_manual ? ' ｜ <span class="tag t-high">需人工复核</span>' : ''))
    : '';

  renderPager(pageNums);

  const map = risksByPara();
  const list = (D.paragraphs || []).filter(p => p.page === PAGE);
  CR.$('#doc').innerHTML = '<div class="doc-page">' +
    '<div class="pg-label">第 ' + PAGE + ' 页 / 共 ' + pageNums.length +
    ' 页　·　本页 ' + list.length + ' 段</div>' +
    list.map(p => '<p class="doc-para ' + (p.is_heading ? 'head' : '') +
      '" id="para-' + p.index + '" data-page="' + p.page + '">' +
      '<span class="pno">' + ('P' + p.page + '·' + String(p.index).padStart(3, '0')) + '</span>' +
      (p.is_heading ? '' : '<span class="faint" style="font-size:11px;margin-right:6px">' +
        CR.esc(p.clause_label || '') + '</span>') +
      paraHtml(p, map[p.index]) + '</p>').join('') + '</div>';

  // 点击用内联 onclick 绑在 mark 上（见 paraHtml），这里只补键盘可达性
  // （曾漏掉 onclick，导致「点正文里的风险没反应」；两处都绑会触发两次，所以只留一处）
  CR.$$('mark.hl').forEach(m => m.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(this.dataset.rid); }
  }));
  fitDoc();
}

/* 让正文卡的高度精确贴合「视口 − 卡片实际顶部」。
   不用 CSS 魔法数字（原来写死 calc(100vh - 258px)，改版一次就失准）。
   注意：**不能**直接量卡片的 rect.top —— 卡片自己是 sticky，其 top 会随滚动位置
   变化（吸附后恒为 76），据此算高度会随滚动抖动。改为量不参与 sticky 的
   `.wb-side` 的**文档坐标**，它等价于「页面卷到顶端时卡片的视口位置」，与滚动无关，
   且偏保守，因此卡片在任何滚动位置都不会被视口裁掉。 */
function fitDoc() {
  const p = document.querySelector('.wb-side > .panel');
  const side = document.querySelector('.wb-side');
  if (!p || !side) return;
  if (getComputedStyle(p).position !== 'sticky') { p.style.maxHeight = ''; return; }
  const hd = parseFloat(getComputedStyle(document.documentElement)
    .getPropertyValue('--hd')) || 62;
  const restTop = side.getBoundingClientRect().top + window.scrollY;  // 文档坐标
  const avail = window.innerHeight - Math.max(restTop, hd + 14) - 16;
  p.style.maxHeight = Math.max(280, Math.round(avail)) + 'px';
}
window.addEventListener('resize', fitDoc);

function goPage(n) {
  PAGE = n;
  renderDoc();
  CR.$('#doc').scrollTop = 0;
}

/* ------------------------------------------------------------------ Tab 与筛选 */
function setTab(t) {
  TAB = t;
  CR.$$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  renderTabs();
}

function visibleRisks() {
  let rs = (D.results || []).slice();
  if (RLEVEL) rs = rs.filter(r => r.risk_level === RLEVEL);
  const cb = CR.$('#onlyPending');
  if (cb && cb.checked) rs = rs.filter(r => !r.adopted);
  return rs;
}

function renderRiskChips() {
  const all = D.results || [];
  const cnt = lv => all.filter(r => r.risk_level === lv).length;
  const items = [['', '全部', all.length], ['high', '高', cnt('high')],
                 ['medium', '中', cnt('medium')], ['low', '低', cnt('low')]];
  CR.$('#riskChips').innerHTML = items
    .filter(it => it[0] === '' || it[2] > 0)
    .map(it => '<button class="chip ' + it[0] + (RLEVEL === it[0] ? ' on' : '') +
      '" onclick="setLevel(\'' + it[0] + '\')">' + it[1] +
      '<span class="n">' + it[2] + '</span></button>').join('');
}

function setLevel(lv) {
  RLEVEL = lv;
  CURSOR = -1;
  renderTabs();
}

function renderTabs() {
  const n = (D.results || []).length;
  CR.$('#nRisk').textContent = n ? '(' + n + ')' : '(0)';
  CR.$('#riskFilter').style.display = (TAB === 'risk') ? 'flex' : 'none';
  const body = CR.$('#tabBody');
  if (TAB === 'risk') {
    renderRiskChips();
    body.innerHTML = renderRiskList();
  } else if (TAB === 'clause') body.innerHTML = renderClauses();
  else if (TAB === 'meta') body.innerHTML = renderMeta();
  else body.innerHTML = renderAudit();
}

function renderRiskList() {
  const all = D.results || [];
  if (!all.length) {
    return '<div class="empty">' + CR.icon('checkCircle', '', 30) +
      '<div>未命中规则库中的风险项。</div>' +
      '<span class="faint" style="font-size:12px">规则版本 ' + CR.esc(D.rules_version || '-') +
      '，本类型共加载 ' + (D.rules_loaded || []).length + ' 条规则。</span></div>';
  }
  const rs = visibleRisks();
  if (!rs.length) return '<div class="empty">当前筛选条件下没有风险项。</div>';

  return rs.map((r, i) => {
    const loc = (r.page_no !== null && r.page_no !== undefined)
      ? ('第 ' + r.page_no + ' 页 · 第 ' + r.para_index + ' 段')
      : '<span class="faint">全文未检出（无定位锚点）</span>';
    const alts = (r.suggestion_alternatives || []);
    const ev = r.evidence || '';
    return '<div class="risk-card ' + r.risk_level + (r.adopted ? ' done' : '') +
      '" id="card-' + r.id + '" data-rid="' + r.id + '" data-idx="' + i +
      '" onclick="pick(' + r.id + ')">' +
      '<div class="rh">' + CR.riskTag(r.risk_level, r.risk_level_label) +
        '<span class="rn">' + (i + 1) + '. ' + CR.esc(r.rule_name) + '</span>' +
        '<span class="rid">' + CR.esc(r.rule_id) + '</span>' +
        (r.adopted ? '<span class="mark">' + CR.icon('check', '', 11) + '已采纳</span>' : '') +
        '<span class="acts">' +
          '<button class="ico" title="折叠 / 展开" aria-label="折叠或展开" onclick="event.stopPropagation();foldCard(' + r.id + ')">' + CR.icon('chevron', '', 13) + '</button>' +
          '<button class="ico" title="复制推荐条款" aria-label="复制推荐条款" onclick="event.stopPropagation();copySugg(' + r.id + ')">' + CR.icon('copy', '', 13) + '</button>' +
          ((r.page_no !== null && r.page_no !== undefined)
            ? '<button class="ico" title="定位原文" aria-label="定位原文" onclick="event.stopPropagation();pick(' + r.id + ')">' + CR.icon('target', '', 13) + '</button>' : '') +
        '</span>' +
      '</div>' +
      '<div class="meta"><span>条款类型：' + CR.esc(r.clause_type) + '</span>' +
        '<span>匹配方式：' + CR.esc(r.match_mode) + '</span>' +
        '<span>' + loc + '</span></div>' +
      '<div class="body">' +
        (ev ? '<div class="quote' + (ev.length > 160 ? ' clip' : '') +
          '" onclick="event.stopPropagation();this.classList.toggle(\'clip\')">原文片段：' +
          CR.esc2(ev) + '</div>' : '') +
        '<div class="field"><div class="lab">风险成因</div>' +
          '<div style="font-size:12.5px;line-height:1.85">' + CR.esc(r.reason) + '</div></div>' +
        '<div class="field"><div class="lab">法律 / 合规依据</div>' +
          '<div style="font-size:12.5px;line-height:1.85">' + CR.esc(r.legal_basis) + '</div></div>' +
        '<div class="field"><div class="lab">推荐修改条款（可编辑，编辑后点「采纳」保存）</div>' +
          '<textarea class="sugg" id="sg-' + r.id + '" rows="4" style="width:100%" ' +
          'aria-label="推荐修改条款（可编辑，编辑后点「采纳」保存）" ' +
          'onclick="event.stopPropagation()">' +
          CR.esc(r.suggestion_final || r.suggestion || '') + '</textarea>' +
          (alts.length ? '<details class="alt-opt" onclick="event.stopPropagation()">' +
            '<summary>备选方案 ' + alts.length + ' 套</summary>' +
            alts.map((a, ai) =>
              '<div style="margin-top:6px"><b>' + CR.esc(a.title) + '</b>' +
              '<div class="quote" data-alt="' + CR.esc(a.text) + '">' + CR.esc(a.text) + '</div>' +
              '<div class="faint" style="font-size:11.5px;margin-top:3px">' + CR.esc(a.note || '') + '</div>' +
              '<button class="btn sm" style="margin-top:5px" data-cap="edit_suggestion" data-cap-label="采纳/调整修改建议" onclick="useAlt(' + r.id + ',' + ai + ')">用此方案替换</button></div>').join('') +
            '</details>' : '') +
        '</div>' +
        '<div class="field"><div class="lab">法务批注</div>' +
          '<input type="text" id="nt-' + r.id + '" style="width:100%" ' +
          'placeholder="可补充处理意见" onclick="event.stopPropagation()" value="' +
          CR.esc(r.lawyer_note || '') + '"></div>' +
        '<div class="row" style="margin-top:9px">' +
          '<button class="btn sm primary" data-cap="edit_suggestion" data-cap-label="采纳/调整修改建议" onclick="event.stopPropagation();adopt(' + r.id + ',true)">采纳建议</button>' +
          (r.adopted ? '<button class="btn sm" data-cap="edit_suggestion" data-cap-label="采纳/调整修改建议" onclick="event.stopPropagation();adopt(' + r.id + ',false)">取消采纳</button>' : '') +
          '<button class="btn sm ghost" onclick="event.stopPropagation();copySugg(' + r.id + ')">复制条款</button>' +
        '</div>' +
      '</div></div>';
  }).join('');
}

function foldCard(rid) {
  const c = CR.$('#card-' + rid);
  if (c) c.classList.toggle('collapsed');
}

let ALL_FOLDED = false;
function toggleCollapse() {
  ALL_FOLDED = !ALL_FOLDED;
  CR.$$('.risk-card').forEach(c => c.classList.toggle('collapsed', ALL_FOLDED));
  CR.$('#btnCollapse').textContent = ALL_FOLDED ? '全部展开' : '全部折叠';
}

function renderClauses() {
  const cov = D.coverage || {};
  const cls = D.clauses || [];
  const rate = Math.round((cov.coverage_rate || 0) * 100);
  return '<div class="row" style="justify-content:space-between;margin-bottom:8px">' +
    '<span style="font-size:12.5px">条款覆盖度 <b class="num">' + rate + '%</b>（' +
    (cov.present || []).length + ' / ' + (cov.total_types || 0) + ' 类）</span>' +
    '<span class="faint" style="font-size:11.5px">共切分 ' + cls.length + ' 个条款</span></div>' +
    '<div class="bar" style="margin-bottom:12px"><i style="width:' + rate + '%"></i></div>' +
    '<div class="kv-list" style="margin-bottom:12px">' +
    '<div class="r"><div class="k">已识别</div><div class="v">' +
      (cov.present || []).map(x => '<span class="tag t-ok" style="margin:1px 3px 1px 0">' +
        CR.esc(x) + '</span>').join('') + '</div></div>' +
    '<div class="r"><div class="k">未识别</div><div class="v">' +
      ((cov.missing || []).length
        ? (cov.missing || []).map(x => '<span class="tag t-none" style="margin:1px 3px 1px 0">' +
          CR.esc(x) + '</span>').join('')
        : '无') + '</div></div></div>' +
    '<div class="table-wrap"><table class="grid"><thead><tr><th style="width:36px">#</th><th>条款</th>' +
    '<th style="width:104px">类型</th><th style="width:52px">页码</th>' +
    '<th style="width:82px">段落</th><th style="width:56px" class="right">字数</th>' +
    '</tr></thead><tbody>' + cls.map(c =>
      '<tr class="clickable" onclick="goPara(' + c.page + ',' + c.para_start + ')">' +
      '<td class="mono">' + c.seq + '</td>' +
      '<td style="font-size:12.5px">' + CR.esc((c.title || '').slice(0, 46)) + '</td>' +
      '<td style="font-size:12px">' + CR.esc(c.clause_label) + '</td>' +
      '<td class="num">' + c.page + '</td>' +
      '<td class="num faint">' + c.para_start + '-' + c.para_end + '</td>' +
      '<td class="num right">' + c.char_count + '</td></tr>').join('') +
    '</tbody></table></div>';
}

function renderMeta() {
  const c = D.case, m = D.meta || {}, d = D.document || {};
  const rows = [
    ['合同标题', c.title], ['合同编号', c.case_no], ['送审部门', c.send_dept],
    ['申请人', c.applicant], ['相对方主体', c.counterparty || m.party_b],
    ['统一社会信用代码', c.credit_code || m.credit_code || '未载明'],
    ['合同金额', c.amount_text], ['币种', c.currency],
    ['履约周期', c.perform_period || m.perform_period],
    ['生效条件', c.effective_condition || m.effective_condition],
    ['合同类别', c.business_type_label], ['来源', c.source_label],
    ['审批单号', c.approval_instance || '-'], ['解析引擎', d.parse_engine],
    ['解析状态', d.parse_status], ['OCR 置信度', d.ocr_confidence || '-'],
    ['页数 / 段落 / 字符', (d.page_count || 0) + ' / ' + (d.para_count || 0) +
      ' / ' + (d.char_count || 0)],
    ['文件指纹 SHA256', d.sha256], ['规则版本', D.rules_version || '-'],
    ['正文段落总数', D.paragraphs_total || (D.paragraphs || []).length],
    ['创建时间', c.created_at]
  ];
  return '<div class="kv-list">' + rows.map(r =>
    '<div class="r"><div class="k">' + CR.esc(r[0]) + '</div><div class="v">' +
    CR.esc(r[1]) + '</div></div>').join('') + '</div>';
}

function renderAudit() {
  const rows = D.audit || [];
  if (!rows.length) return '<div class="empty">暂无审计记录</div>';
  return '<div class="log">' + rows.map(r =>
    '<div class="r"><span class="t">' + CR.esc(r.created_at) + '</span>' +
    '<span class="s ' + CR.esc(r.status) + '">' + CR.esc(r.status) + '</span>' +
    '<span>' + CR.esc(r.step) + ' · ' + CR.esc(r.tool) +
    (r.detail ? (' — ' + CR.esc(r.detail)) : '') + '</span></div>').join('') + '</div>';
}

/* ------------------------------------------------------------------ 回写区 */
function renderSide() {
  const s = D.summary || {}, c = D.case;
  const st = s.stats || { high: 0, medium: 0, low: 0 };
  const all = D.results || [];
  const adopted = all.filter(r => r.adopted).length;
  const prog = all.length ? Math.round(adopted / all.length * 100) : 0;

  CR.$('#summaryBox').innerHTML =
    '<div class="row" style="margin-bottom:9px">' + CR.riskTag(c.risk_level, c.risk_level_label) +
    '<b style="font-size:13.5px">' + CR.esc(c.conclusion_label) + '</b>' +
    '<div class="spacer" style="flex:1"></div>' +
    '<span class="faint num" style="font-size:12px">高 ' + st.high + ' · 中 ' + st.medium +
    ' · 低 ' + st.low + '</span></div>' +
    CR.distBar({ high: st.high, medium: st.medium, low: st.low }) +
    '<div class="summary-line" style="margin-top:10px">' + CR.esc(s.headline || '') + '</div>' +
    renderModeCompare(s) +
    ((s.attention || []).length ? '<ul class="attn">' +
      s.attention.map(a => '<li>' + CR.esc(a) + '</li>').join('') + '</ul>' : '') +
    // 建议采纳进度（真实数据：已采纳 / 风险项总数）
    (all.length ?
      '<div class="row" style="margin-top:12px"><span class="faint" style="font-size:11.5px">' +
      '建议采纳进度</span><div class="spacer" style="flex:1"></div>' +
      '<span class="num" style="font-size:12px;color:var(--cyan)">' + adopted + ' / ' + all.length +
      '</span></div><div class="bar" style="margin-top:5px"><i style="width:' +
      Math.max(2, prog) + '%"></i></div>' : '') +
    '<div class="faint" style="font-size:11.5px;margin-top:9px">摘要生成方式：' +
      (s.summary_source === 'llm' ? '大模型润色' : '确定性规则模板') +
      '　·　风险判定：规则引擎（大模型不参与判定）</div>';
  const ta = CR.$('#opinion');
  if (ta && !ta.value) ta.value = c.lawyer_opinion || '';
}

/* -------------------------------------------------- v2.0 规则/模型模式对照（P1-05）
   同一份命中证据链，两种表达并排。未配 Key 时明示当前模式与未启用原因，
   保证「对照视图一定可演示」—— 拿不到真实润色也看得到两种模式的差别。 */
function renderModeCompare(s) {
  const mc = D.mode_compare || {};
  const mi = D.mode_info || {};
  const cur = mc.current || s.mode || 'rule';
  const rule = mc.rule || {};
  const model = mc.model || {};
  const on = !!model.available;

  return '<div style="margin-top:14px;border-top:1px dashed var(--line-2);padding-top:12px">' +
    '<div class="row" style="margin-bottom:9px">' +
    '<span style="font-size:12.4px;font-weight:600">审查表达模式对照</span>' +
    '<div class="spacer" style="flex:1"></div>' +
    '<span class="mode-badge ' + (on ? 'live' : 'off') + '">当前：' +
    CR.esc(cur === 'rule+llm' ? '规则 + 模型润色' : '规则引擎') + '</span></div>' +

    '<div class="mode-grid">' +
    '<div class="mode-col' + (cur === 'rule' ? ' cur' : '') + '">' +
    '<h4>' + CR.icon('shield', '', 13) + '规则引擎模式' +
    '<span class="mode-badge ' + (cur === 'rule' ? 'live' : 'off') + '">' +
    (cur === 'rule' ? '当前生效' : '可切换') + '</span></h4>' +
    '<div class="body">' + CR.esc(rule.headline || s.headline_rule || s.headline || '') + '</div>' +
    '<div class="mode-note">' + CR.esc(rule.note ||
      '确定性模板生成，可复现、可审计，零依赖') + '</div></div>' +

    '<div class="mode-col' + (cur === 'rule+llm' ? ' cur' : '') + '">' +
    '<h4>' + CR.icon('cpu', '', 13) + '规则 + 模型润色模式' +
    '<span class="mode-badge ' + (on ? 'live' : 'off') + '">' +
    (on ? (cur === 'rule+llm' ? '当前生效' : '可切换') : '未启用') + '</span></h4>' +
    '<div class="body">' + (on && model.headline
      ? CR.esc(model.headline)
      : '<span class="faint">未启用模型润色 —— 当前沿用规则模板输出，风险结论与等级完全一致，'
        + '差别仅在中文字面表达。</span>') + '</div>' +
    '<div class="mode-note">' + CR.esc(model.reason
      ? '未启用原因：' + model.reason
      : (model.note || '仅在规则结论之上改写措辞，不参与风险判定')) +
    (model.model ? '　·　模型 ' + CR.esc(model.model) : '') + '</div></div>' +
    '</div>' +

    '<div class="mode-note">' + CR.esc(mi.note ||
      '风险判定 100% 由确定性规则引擎产出，大模型只改写措辞、不参与判定。') + '</div>' +

    // 页眉页脚剔除留痕：剔除行可查，锚点仍可定位
    ((D.stripped || []).length ?
      '<div class="striplist" style="margin-top:12px">' +
      '<div style="font-size:11.5px;color:var(--sub);margin-bottom:5px">' +
      '已剔除页眉/页脚 ' + D.stripped.length + ' 行（剔除后重算坐标，锚点仍可精确定位）</div>' +
      D.stripped.slice(0, 6).map(x =>
        '<div><code>' + CR.esc(x.text) + '</code> ' +
        '<span class="faint">第 ' + x.page + ' 页 · ' +
        (x.channel === 'repeat' ? '跨页重复' : '页码/密级形态') + '</span></div>').join('') +
      '</div>'
      : '<div class="mode-note" style="margin-top:10px">本文档未检出跨页重复的页眉/页脚短行，' +
        '未做剔除。条款标题与含实体词的行受保护名单保护，永不被剔除。</div>') +
    '</div>';
}

function renderWriteback() {
  const wb = D.writeback_logs || [];
  const st = D.case.writeback_status;
  CR.$('#wbState').innerHTML = CR.wbTag(st, D.case.writeback_status_label);
  if (!wb.length) {
    CR.$('#wbResult').innerHTML = '<div class="faint" style="font-size:12px">' +
      '尚未回写。系统只会写入审查意见评论，不提供通过 / 拒绝 / 转交等改变审批结论的能力。</div>';
    return;
  }
  const last = wb[0];
  CR.$('#wbResult').innerHTML =
    '<div class="kv-list"><div class="r"><div class="k">最近回写</div><div class="v">' +
      CR.esc(last.created_at) + '　模式 ' + CR.esc(last.mode) + '　通道 ' +
      CR.esc(last.channel) + '　状态 ' + CR.esc(last.status_label) +
      (last.comment_id ? ('　评论 <span class="mono">' + CR.esc(last.comment_id) + '</span>') : '') +
      '</div></div>' +
    (last.error ? '<div class="r"><div class="k">错误信息</div>' +
      '<div class="v" style="color:var(--high)">' + CR.esc(last.error) + '</div></div>' : '') +
    '</div>' +
    ((D.approval_comments || []).length
      ? '<div class="field"><div class="lab">审批单评论区（模拟飞书审批）</div>' +
        D.approval_comments.map(c => '<div class="quote"><b>' + CR.esc(c.author) +
          '</b> <span class="faint">' + CR.esc(c.created_at) + '</span><br>' +
          CR.esc2(c.text.slice(0, 320)) + (c.text.length > 320 ? ' …' : '') + '</div>').join('') +
        '</div>'
      : '<div class="faint" style="font-size:12px;margin-top:8px">审批单评论区暂无评论。</div>') +
    (last.status === 'failed'
      ? '<button class="btn sm danger" data-cap="writeback" data-cap-label="回写审批意见" style="margin-top:8px" onclick="doWriteback(\'live\')">重试回写</button>'
      : '');
}

async function loadNotifyStatus() {
  try {
    const s = await CR.api('GET', '/api/notify/status');
    const el = CR.$('#notifyState');
    el.innerHTML = (!s.enabled || !s.configured)
      ? '<button type="button" class="as-link" onclick="openNotify()">通知未启用</button>'
      : '<button type="button" class="as-link" onclick="openNotify()">通知已启用 · 成功 ' +
        s.stats.sent + ' / 失败 ' + s.stats.failed + '</button>';
  } catch (e) { }
}

async function openNotify() {
  CR.$('#notifyBody').innerHTML = CR.skeleton(4);
  CR.openModal('mNotify');
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
          CR.esc(k) + '</span>').join('') + '</div></div>' +
      '</div>' +
      (s.configured ? '' : '<div class="notice warn" style="margin-top:10px">要启用钉钉通知：编辑 ' +
        '<span class="mono">config/app.json</span>，把 <span class="mono">notify.enabled</span> ' +
        '设为 true，填写 <span class="mono">notify.webhook</span>（钉钉自定义机器人地址）；' +
        '若机器人安全设置选了「加签」，再把 <span class="mono">notify.secret</span> 填上 SEC 开头的密钥。</div>') +
      (recent.length ? '<div class="field" style="margin-top:10px"><div class="lab">最近发送记录</div>' +
        '<div class="log">' + recent.map(r => '<div class="r"><span class="t">' + CR.esc(r.at) +
          '</span><span class="s ' + CR.esc(r.status === 'sent' ? 'ok' :
            (r.status === 'failed' ? 'failed' : 'skipped')) + '">' + CR.esc(r.status) +
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
  }
}

/* ------------------------------------------------------------------ 交互 */
function pick(rid) {
  rid = Number(rid);
  const r = (D.results || []).find(x => x.id === rid);
  if (!r) return;
  if (TAB !== 'risk') setTab('risk');
  CR.$$('.risk-card').forEach(c => c.classList.toggle('on', Number(c.dataset.rid) === rid));
  CURSOR = CR.$$('.risk-card').findIndex(c => Number(c.dataset.rid) === rid);
  const card = CR.$('#card-' + rid);
  if (card) {
    if (card.classList.contains('collapsed')) card.classList.remove('collapsed');
    // 目标卡片若没有完整进入视口就**居中**滚过去；否则只贴边 ——
    // 用 nearest 时卡片会紧贴视口底/被吸顶的顶栏压住，用户仍看不到要点的那条。
    const rc = card.getBoundingClientRect();
    const stick = 76;                              // 顶栏 + 留白
    const fullyVisible = rc.top >= stick && rc.bottom <= window.innerHeight - 8;
    card.scrollIntoView({ block: fullyVisible ? 'nearest' : 'center', behavior: 'smooth' });
  }
  if (r.page_no !== null && r.page_no !== undefined) {
    if (PAGE !== r.page_no) { PAGE = r.page_no; renderDoc(); }
    const mk = CR.$('mark[data-rid="' + rid + '"]');
    if (mk) {
      mk.classList.add('flash');
      mk.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(() => mk.classList.remove('flash'), 1100);
    }
  }
}

function goPara(page, paraIndex) {
  PAGE = page;
  renderDoc();
  const el = CR.$('#para-' + paraIndex);
  if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function useAlt(rid, ai) {
  const ta = CR.$('#sg-' + rid);
  const card = CR.$('#card-' + rid);
  const nodes = card ? card.querySelectorAll('[data-alt]') : [];
  if (!ta || !nodes[ai]) return;
  ta.value = nodes[ai].dataset.alt;
  CR.toast('已用备选方案替换编辑区内容，点「采纳建议」保存', 'ok');
}

async function copySugg(rid) {
  const ta = CR.$('#sg-' + rid);
  if (!ta) return;
  const ok = await CR.copyText(ta.value);
  CR.toast(ok ? '推荐条款已复制到剪贴板' : '复制失败，请手动选择文本', ok ? 'ok' : 'err');
}

async function adopt(rid, on) {
  const ta = CR.$('#sg-' + rid);
  const nt = CR.$('#nt-' + rid);
  if (!ta) return;
  try {
    await CR.api('PATCH', '/api/results/' + rid, {
      suggestion_final: ta.value, adopted: on, lawyer_note: nt ? nt.value : ''
    });
    CR.toast(on ? '已采纳该修改建议' : '已取消采纳', 'ok');
    D = await CR.api('GET', '/api/cases/' + CASE_ID);
    renderTabs();
    renderSide();
    renderWriteback();
  } catch (e) { CR.toast('保存失败：' + CR.esc(e.message), 'err'); }
}

async function saveOpinion() {
  try {
    await CR.api('POST', '/api/cases/' + CASE_ID + '/opinion',
                 { opinion: CR.$('#opinion').value });
    CR.toast('法务批注已保存', 'ok');
  } catch (e) { CR.toast('保存失败：' + CR.esc(e.message), 'err'); }
}

async function doWriteback(mode) {
  const b = CR.$('#btnWb');
  if (mode === 'live') CR.busy(b, true, '回写中');
  try {
    await CR.api('POST', '/api/cases/' + CASE_ID + '/opinion',
                 { opinion: CR.$('#opinion').value });
    const d = await CR.api('POST', '/api/cases/' + CASE_ID + '/writeback', { mode: mode });
    PF = d.comment_text || '';
    CR.$('#cmTitle').textContent = (mode === 'dry_run'
      ? '预览：待写入审批评论区的审查意见'
      : '回写结果：' + (d.message || ''));
    CR.$('#cmBody').textContent = PF;
    CR.$('#cmOk').style.display = (mode === 'dry_run') ? 'inline-flex' : 'none';
    CR.openModal('mComment');
    if (mode === 'live') {
      CR.toast(d.ok
        ? ('回写成功，评论标识 <span class="mono">' + CR.esc(d.comment_id) + '</span>')
        : ('回写失败：' + CR.esc(d.error || '')), d.ok ? 'ok' : 'err');
    }
    D = await CR.api('GET', '/api/cases/' + CASE_ID);
    renderHead(); renderSide(); renderWriteback(); renderTabs();
  } catch (e) {
    CR.toast('回写失败：' + CR.esc(e.message), 'err');
  } finally { CR.busy(b, false); }
}

async function copyComment() {
  const ok = await CR.copyText(PF || '');
  CR.toast(ok ? '审查意见已复制' : '复制失败', ok ? 'ok' : 'err');
}

async function confirmWb() {
  CR.closeModal('mComment');
  await doWriteback('live');
}

function exportReport(fmt) {
  CR.download('/api/cases/' + CASE_ID + '/report?format=' + fmt + '&download=1');
  CR.toast('已开始下载 Markdown 审查意见报告', 'ok');
}

function exportPdf() {
  const w = window.open('/api/cases/' + CASE_ID + '/report?format=pdf&print=1', '_blank');
  if (!w) CR.toast('浏览器拦截了新窗口，请允许弹窗后重试', 'err');
  else CR.toast('PDF 通道已触发；若打开的是打印预览，选择「另存为 PDF」即可', 'ok');
}

async function doRetry() {
  CR.overlay(true, '正在重新解析与审查…', '修正文件后重新进入流转流水线。');
  try {
    const r = await CR.api('POST', '/api/cases/' + CASE_ID + '/retry');
    if (r && r.blocked) CR.toast('重试后仍受阻：' + CR.esc(r.reason || ''), 'err');
    else CR.toast('重试成功，已完成审查', 'ok');
    await load();
  } catch (e) { CR.toast('重试失败：' + CR.esc(e.message), 'err'); }
  finally { CR.overlay(false); }
}

function openComments() {
  const inst = D.case.approval_instance;
  if (!inst) return CR.toast('该合同尚未绑定审批单，请先执行一次回写', 'err');
  const w = window.open('/mock/feishu/api/instances/' + inst + '/comments', '_blank');
  if (!w) CR.toast('浏览器拦截了新窗口', 'err');
}

/* ------------------------------------------------------------------ 快捷键 */
document.addEventListener('keydown', function (e) {
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;
  if (e.key === 'Escape') {
    ['mComment', 'mNotify'].forEach(id => CR.closeModal(id));
    return;
  }
  if (TAB !== 'risk') return;
  const cards = CR.$$('.risk-card');
  if (!cards.length) return;
  if (e.key === 'j' || e.key === 'J') {
    CURSOR = Math.min(cards.length - 1, (CURSOR < 0 ? -1 : CURSOR) + 1);
    pick(cards[CURSOR].dataset.rid);
    e.preventDefault();
  } else if (e.key === 'k' || e.key === 'K') {
    CURSOR = Math.max(0, (CURSOR < 0 ? cards.length : CURSOR) - 1);
    pick(cards[CURSOR].dataset.rid);
    e.preventDefault();
  } else if (e.key === 'a' || e.key === 'A') {
    if (CURSOR >= 0 && cards[CURSOR]) {
      adopt(cards[CURSOR].dataset.rid, true);
      e.preventDefault();
    }
  }
});

load();
