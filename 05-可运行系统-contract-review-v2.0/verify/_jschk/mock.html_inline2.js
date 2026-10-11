
let DATA = null;
let CUR = null;

CR.initAuth('/mock', '模拟审批平台 · 内置实现');

window.addEventListener('error', function (ev) {
  CR.toast('页面脚本异常：' + (ev.message || ''), 'err');
});
window.addEventListener('unhandledrejection', function (ev) {
  CR.toast('请求异常：' + String((ev.reason && ev.reason.message) || ev.reason), 'err');
});

/* ------------------------------------------------------------------ 列表 */
function renderList() {
  const kw = (CR.$('#kw').value || '').trim().toLowerCase();
  const items = (DATA.instances || []).filter(function (x) {
    if (!kw) return true;
    return (x.instance_id + x.title + x.applicant).toLowerCase().indexOf(kw) >= 0;
  });
  CR.$('#list').innerHTML = items.length ? items.map(function (x) {
    const st = x.review_status
      ? (x.review_status === 'completed' ? '已审查' :
        x.review_status === 'blocked' ? '解析受阻' : '处理中')
      : '未接入审查';
    const cls = x.review_status === 'completed' ? 'ok' :
      x.review_status === 'blocked' ? 'blocked' : '';
    return '<div class="mock-item' + (CUR === x.instance_id ? ' on' : '') +
      '" onclick="pick(\'' + CR.esc(x.instance_id) + '\')">' +
      '<div class="t1">' + CR.esc(x.title || '(无标题)') + '</div>' +
      '<div class="t2">' +
      '<span class="mono">' + CR.esc(x.instance_id) + '</span>' +
      '<span>' + CR.esc(x.applicant || '-') + ' · ' + CR.esc(x.send_dept || '-') + '</span>' +
      '<span>' + CR.esc(x.business_type || '-') + '</span>' +
      '<span>' + CR.money(x.amount).replace(/<[^>]+>/g, '').trim() + '</span>' +
      '<span class="tag ' + cls + '">' + st + '</span>' +
      '<span>' + x.comments + ' 条评论</span>' +
      '</div></div>';
  }).join('') : '<div class="empty">没有匹配的审批单</div>';
}

/* ------------------------------------------------------------------ 详情 */
async function pick(iid) {
  CUR = iid;
  renderList();
  CR.$('#detailNo').textContent = iid;
  CR.$('#detail').innerHTML = CR.skeleton(4);
  try {
    const d = await CR.api('GET', '/api/mock/overview');
    DATA = d;
  } catch (e) { /* 用已有数据 */ }
  const x = (DATA.instances || []).filter(v => v.instance_id === iid)[0];
  if (!x) { CR.$('#detail').innerHTML = '<div class="empty">未找到该审批单</div>'; return; }

  const stMap = { completed: ['已审查', 'ok'], blocked: ['解析受阻', 'blocked'] };
  const st = x.review_status ? (stMap[x.review_status] ||
    [x.review_status, '']) : ['未接入审查', ''];

  let html = '<div class="mock-kv">' +
    cell('审批单号', '<span class="mono">' + CR.esc(x.instance_id) + '</span>') +
    cell('合同名称', CR.esc(x.title || '-')) +
    cell('申请人 / 部门', CR.esc((x.applicant || '-') + ' · ' + (x.send_dept || '-'))) +
    cell('合同类型 / 金额', CR.esc(x.business_type || '-') + ' · ' +
         CR.money(x.amount).replace(/<[^>]+>/g, '').trim()) +
    cell('平台侧状态', CR.esc(x.status || '-')) +
    cell('提交时间', CR.esc(x.submitted_at || '-')) +
    cell('附件', '<span class="mono" style="font-size:12.4px">' +
         CR.esc(x.attachment || '-') + '</span>') +
    cell('本系统处理结果', x.linked
      ? '<span class="tag ' + st[1] + '">' + st[0] + '</span> ' +
        CR.riskTag(x.risk_level, ({high: '高风险', medium: '中风险', low: '低风险'}[x.risk_level] || ''))
      : '<span class="faint">尚未建立审查任务</span>') +
    '</div>';

  html += '<div class="sep"></div><div style="font-size:12.3px;color:var(--sub);' +
    'margin-bottom:9px">审批单评论区（本系统写回的审查意见就落在这里）</div>';
  const acts = (DATA.instances || []).filter(v => v.instance_id === iid)[0].comment_items || [];
  if (!acts.length) {
    html += '<div class="faint" style="font-size:12.5px">' +
      '评论区还是空的。到工作台点「写回审批单」，这条意见就会出现在这里。</div>';
  } else {
    html += acts.slice().reverse().map(c =>
      '<div class="mock-comment"><div class="ch">' +
      '<b>' + CR.esc(c.author || '—') + '</b>' +
      '<span>' + CR.esc(c.created_at || '') + '</span>' +
      '<span class="mono">' + CR.esc(c.comment_id || '') + '</span></div>' +
      '<div class="cb">' + CR.esc((c.text || '').slice(0, 700)) +
      ((c.text || '').length > 700 ? '…' : '') + '</div></div>').join('');
  }
  if (x.case_id) {
    html += '<div style="margin-top:12px"><a class="btn sm primary" href="/workbench?id=' +
      x.case_id + '">打开该合同的审查工作台 →</a></div>';
  }
  CR.$('#detail').innerHTML = html;
  CR.motion.enter(CR.$('#detail'));
}

function cell(k, v) {
  return '<div class="cell"><div class="k">' + CR.esc(k) + '</div><div class="v">' + v + '</div></div>';
}

/* ------------------------------------------------------------------ 接口清单 */
function renderEps() {
  CR.$('#eps').innerHTML = (DATA.endpoints || []).map(function (e) {
    return '<div class="ep"><span class="m">' + CR.esc(e.method) + '</span>' +
      '<span class="p">' + CR.esc(e.path) + '</span>' +
      '<span class="d">' + CR.esc(e.desc) + '</span></div>';
  }).join('');
}

function scrollToEp() {
  CR.$('#eps').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ------------------------------------------------------------------ 原始 JSON */
function toggleRaw() {
  const p = CR.$('#rawPanel');
  const on = p.style.display === 'none';
  p.style.display = on ? '' : 'none';
  CR.$('#btnRaw').textContent = on ? '收起原始 JSON' : '查看原始 JSON';
  if (on) loadRaw();
}

function syncRawOptions() {
  if (!CUR) return;
  CR.$('#optDetail').value = '/mock/feishu/api/instances/' + CUR;
  CR.$('#optDetail').textContent = 'GET /mock/feishu/api/instances/' + CUR;
  CR.$('#optComments').value = '/mock/feishu/api/instances/' + CUR + '/comments';
  CR.$('#optComments').textContent = 'GET /mock/feishu/api/instances/' + CUR + '/comments';
}

async function loadRaw() {
  syncRawOptions();
  const pick = CR.$('#rawPick');
  const path = pick.value || '/mock/feishu/api/todo';
  CR.$('#rawBox').textContent = '加载中…';
  try {
    const r = await fetch(path, { headers: { 'Accept': 'application/json' } });
    const j = await r.json();
    CR.$('#rawBox').textContent = JSON.stringify(j, null, 2);
  } catch (e) {
    CR.$('#rawBox').textContent = '读取失败：' + e.message;
  }
}

function copyRaw() {
  CR.copyText(CR.$('#rawBox').textContent).then(function () {
    CR.toast('已复制原始 JSON', 'ok');
  });
}

/* ------------------------------------------------------------------ 拉取 */
async function refreshAll() {
  try {
    DATA = await CR.api('GET', '/api/mock/overview');
  } catch (e) {
    CR.$('#list').innerHTML = '<div class="empty">读取模拟平台数据失败：' +
      CR.esc(e.message) + '</div>';
    return;
  }
  CR.$('#todoTag').textContent = '待办 ' + DATA.todo_count + ' 张';
  CR.$('#genTag').textContent = '数据生成于 ' + DATA.generated_at +
    (DATA.fault_rate ? ' · 已注入故障率 ' + DATA.fault_rate : '');
  renderList();
  renderEps();
  CR.motion.enter();
  if (CUR) pick(CUR);
}

CR.$('#kw').addEventListener('input', renderList);
(async function () {
  await refreshAll();
  // 默认选中「有评论的那张单」—— 打开就能看到本系统写回的审查意见，
  // 而不是停在一张空评论区上。
  const all = DATA.instances || [];
  const first = all.filter(x => x.comments > 0)[0] ||
    all.filter(x => x.linked)[0] || all[0];
  if (first) pick(first.instance_id);
  CR.initReveal();
})();
