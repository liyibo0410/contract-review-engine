
let D = null;                 // 最近一次 /api/twin 结果
let SEEN = {};                // 已渲染过的审计条目标识（用于判断「新条目」）
let FIRST = true;

CR.initAuth('/twin', '孪生看板 · 实时镜像');

/* 把脚本异常/请求异常直接写到页面上：否则页面只会「空着」，
   排查时只能靠猜（这类静默失败最难查）。 */
window.addEventListener('error', function (ev) {
  var n = CR.$('#srcNote');
  if (n) n.innerHTML = '<b style="color:var(--high)">页面脚本异常：' +
    CR.esc((ev.message || '') + ' @ 行 ' + (ev.lineno || '')) + '</b>';
});
window.addEventListener('unhandledrejection', function (ev) {
  var n = CR.$('#srcNote');
  if (n) n.innerHTML = '<b style="color:var(--high)">请求异常：' +
    CR.esc(String((ev.reason && ev.reason.message) || ev.reason)) + '</b>';
});

/* ------------------------------------------------------------------ 工具 */
function kv(k, v, sub) {
  return '<div class="kv-cell"><div class="k">' + CR.esc(k) + '</div>' +
    '<div class="v">' + v + (sub ? '<div class="faint" style="font-size:11.7px">' +
      CR.esc(sub) + '</div>' : '') + '</div></div>';
}
function gauge(k, v, max, unit, sub, cls) {
  const pct = max > 0 ? Math.min(100, v / max * 100) : 0;
  return '<div class="meter ' + (cls || '') + '">' +
    '<div class="k"><span>' + CR.esc(k) + '</span><b>' + v + (unit || '') + '</b></div>' +
    '<div class="bar"><i style="width:' + Math.max(1.5, pct) + '%"></i></div>' +
    '<div class="sub">' + CR.esc(sub || '') + '</div></div>';
}
function sec(ms) {
  const h = Math.floor(ms / 3600), m = Math.floor(ms % 3600 / 60), s = ms % 60;
  return (h ? h + ' 时 ' : '') + (h || m ? m + ' 分 ' : '') + s + ' 秒';
}

/* ------------------------------------------------------------------ 渲染 */
/* 聚合异常必须看得见 —— 否则「取不到」会被当成「没有」，
   面板显示的就是错误信息（本次踩过：通知状态读失败 → 显示"未配置/脱敏关闭"）。 */
function renderErrors() {
  var box = CR.$('#errBox');
  if (!box) { return; }
  var es = (D && D.errors) || [];
  if (!es.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
  box.style.display = '';
  box.innerHTML = '<b style="color:var(--high)">服务端聚合出现 ' + es.length +
    ' 处异常，下列数字可能不完整：</b>' +
    es.map(function (e) { return '<div>· ' + CR.esc(e) + '</div>'; }).join('');
}

function renderHead() {
  CR.$('#verTag').textContent = 'v' + (D.version || '') + ' · 数据生成于 ' + D.generated_at;
  CR.$('#liveText').textContent = '服务已运行 ' + sec(D.http.uptime_sec || 0) +
    ' · 每 4 秒回读';
}

function renderKpi() {
  const c = D.counts, r = D.rollup;
  const cells = [
    ['审查任务总数', c.total, '个', '已完成 ' + c.completed + ' · 待处理 ' + c.pending,
     'var(--brand)'],
    ['审查完成', c.completed, '个', '已生成审查意见', 'var(--ok)'],
    ['解析受阻', c.blocked, '个', '待管理员重试', 'var(--mid)'],
    ['规则命中', r.hits, '项', '高风险 ' + ((r.hits_by_risk || {}).high || 0) +
      ' · 中风险 ' + ((r.hits_by_risk || {}).medium || 0), 'var(--high)'],
    ['建议采纳', r.adopted, '项', '法务已确认 ' + r.adopted + ' / ' + r.hits, 'var(--cyan)'],
    ['意见回写', c.writeback_success, '条', '共尝试 ' + r.writeback_total + ' 次', 'var(--teal)']
  ];
  CR.$('#kpi').innerHTML = cells.map(x =>
    '<div class="tk" style="--c:' + x[4] + '">' +
    '<div class="k">' + CR.esc(x[0]) + '</div>' +
    '<div class="v' + (CR.motion.changed('kpi|' + x[0], x[1]) ? ' cfx-tick' : '') +
    '">' + x[1] + '<small>' + x[2] + '</small></div>' +
    '<div class="x">' + CR.esc(x[3]) + '</div></div>').join('');
}

function renderPipe() {
  CR.$('#pipe').innerHTML = D.pipeline.map(n =>
    '<div class="pipe-node ' + n.state + '">' +
    '<div class="nm">' + CR.esc(n.name) + '</div>' +
    '<div class="vl">' + n.value + '<small>' + CR.esc(n.unit) + '</small></div>' +
    '<div class="nt">' + CR.esc(n.note) + '</div>' +
    '<div class="src">来源 ' + CR.esc(n.src) + '</div></div>').join('');
}

/* 一行一项的对齐列表：标签 / 数值 / 说明 三列对齐。
   比卡片栅格更适合「读数」——每行的三个信息固定在同一列上，眼睛不用来回找。 */
function mline(k, v, note) {
  return '<div class="ml"><span class="mll">' + CR.esc(k) + '</span>' +
    '<span class="mlv">' + v + '</span>' +
    '<span class="mln">' + CR.esc(note || '') + '</span></div>';
}

function renderEngine() {
  const q = D.queue, e = D.engines, s = D.storage, h = D.http, r = D.rollup;
  const upSec = h.uptime_sec || 0;
  const upMin = upSec / 60;
  const parsed = r.docs || 0;
  const ocrN = e.ocr.docs || 0;
  const ocrPct = ocrN ? Math.round((e.ocr.avg_confidence || 0) * 100) : 0;
  const peek = (h.top_paths || []).reduce((m, t) => Math.max(m, t.max_ms || 0), 0);

  CR.$('#qWorkers').textContent = '并发 ' + q.workers + ' · 队列上限 ' + q.maxsize;

  /* ① 引擎运行指标：一行一项，三列对齐 */
  CR.$('#engineList').innerHTML =
    mline('解析吞吐',
          upSec < 120 ? '—' : (parsed / Math.max(upMin, 0.001)).toFixed(1) + ' 份/分钟',
          upSec < 120 ? '运行不足 2 分钟，暂不计算（避免刚重启的假峰值）'
                      : '累计已解析 ' + parsed + ' 份') +
    mline('接口平均耗时', CR.fmtMs(h.avg_ms),
          '峰值 ' + (peek ? CR.fmtMs(peek) : '—') + ' · 累计 ' + h.requests + ' 次请求') +
    mline('规则命中密度', parsed ? (r.hits / parsed).toFixed(1) + ' 项/份' : '—',
          '累计 ' + r.hits + ' 项（高 ' + ((r.hits_by_risk || {}).high || 0) +
          ' · 中 ' + ((r.hits_by_risk || {}).medium || 0) + '）') +
    mline('OCR 置信度', ocrN ? ocrPct + '%' : '无样本',
          ocrN === 0 ? '尚未处理过扫描件'
            : '样本 ' + ocrN + ' 份' + (ocrN < 3 ? '，偏少仅供参考' : '') + ' · 阈值 50%') +
    mline('错误率', (h.error_rate * 100).toFixed(2) + '%',
          h.errors ? '累计 ' + h.errors + ' 次错误' : '无错误记录') +
    mline('运行时长', sec(upSec), '自 ' + (h.started_at || '—') + ' 起');

  /* 能力清单压成一行（它不是"读数"，占栅格只会让面板更乱） */
  CR.$('#capLine').innerHTML =
    '引擎能力：解析 ' + CR.esc('DOCX / PDF / OCR') +
    ' ｜ PDF 报告 ' + CR.esc(e.pdf.effective === 'browser' ? '浏览器无头打印' : '标准库直出') +
    ' ｜ 规则库 ' + e.rules.total + ' 条 / ' + e.rules.types.length + ' 类' +
    ' ｜ 条款词典 ' + e.clause_types + ' 类 / 示范条款 ' + e.clause_variants + ' 套';

  /* ② 资源水位：以各自配额为分母 */
  const g = (k, v, max, unit, sub) => {
    const ratio = max > 0 ? v / max : 0;
    const cls = v === 0 ? 'idle' : (ratio > .95 ? 'hl' : ratio > .8 ? 'warn' : 'ok');
    return gauge(k, v, max, unit, sub, cls);
  };
  CR.$('#gauges').innerHTML =
    g('队列水位', q.queued, q.maxsize, ' 个',
      q.queued === 0 ? '空闲 · 上限 ' + q.maxsize + ' 个'
                     : '在飞 ' + (q.inflight || []).length + ' 个 · 上限 ' + q.maxsize + ' 个') +
    g('待处理堆积', q.pending_cases, q.max_pending, ' 个',
      q.pending_cases === 0 ? '无积压 · 上限 ' + q.max_pending + ' 个'
                            : '累计提交 ' + q.submitted + ' 个') +
    g('附件存储', s.uploads_mb, q.max_total_mb, ' MB',
      '占配额 ' + (s.uploads_mb / q.max_total_mb * 100).toFixed(2) + '% · 配额 ' +
      q.max_total_mb + ' MB') +
    g('数据库', Math.round(s.db_kb * 10) / 10, Math.max(1024, q.max_total_mb * 1024), ' KB',
      '含 ' + s.data_files + ' 个数据文件 · 报告 ' + s.reports + ' 份');

  const idle = q.queued === 0 && q.pending_cases === 0;
  CR.$('#idleHint').innerHTML = '水位以各自的<b>配额</b>为分母。' + (idle
    ? '当前<b>空闲</b> —— 接近 0 是正常的，表示没有积压。'
    : '当前有在跑的活，注意别接近上限。');
}

function renderHttp() {
  const h = D.http;
  CR.$('#httpMeta').textContent = '自 ' + (h.started_at || '-') + ' 起';
  CR.$('#httpStats').innerHTML = '<div class="kv-grid">' +
    kv('累计请求', h.requests, '错误 ' + h.errors + ' 次') +
    kv('平均响应', CR.fmtMs(h.avg_ms), '全部请求历史均值') +
    kv('错误率', (h.error_rate * 100).toFixed(2) + '%', h.errors ? '有错误记录' : '无错误') +
    kv('响应总量', (h.bytes_out / 1048576).toFixed(2) + ' MB', '压缩后实际发送') +
    '</div>';
  const top = h.top_paths || [];
  const maxMs = Math.max.apply(null, top.map(t => t.avg_ms).concat([1]));
  CR.$('#topPaths').innerHTML = top.length ? top.map(t =>
    '<div style="margin-bottom:8px">' +
    '<div style="display:flex;justify-content:space-between;font-size:12.3px">' +
    '<span class="mono" style="color:var(--ink-2)">' + CR.esc(t.path) + '</span>' +
    '<span class="faint">' + t.count + ' 次 · 均 ' + CR.fmtMs(t.avg_ms) +
    ' · 峰 ' + CR.fmtMs(t.max_ms) + '</span></div>' +
    '<div style="height:5px;border-radius:999px;background:var(--panel-3);' +
    'overflow:hidden;margin-top:4px"><i style="display:block;height:100%;background:' +
    'linear-gradient(90deg,var(--cyan),var(--brand));width:' +
    Math.max(3, t.avg_ms / maxMs * 100) + '%"></i></div></div>').join('')
    : '<div class="faint" style="font-size:12.2px">暂无请求记录</div>';
  const slow = h.slow_requests || [];
  CR.$('#slowBox').innerHTML = slow.length
    ? '<div style="font-size:12.2px;color:var(--sub);margin:12px 0 6px">慢请求（≥ 1s）</div>' +
      slow.map(s => '<div class="row" style="font-size:12.1px;gap:10px;padding:3px 0">' +
        '<span class="faint mono">' + CR.esc(s.at) + '</span>' +
        '<span style="color:var(--mid)">' + s.ms + 'ms</span>' +
        '<span class="mono" style="color:var(--ink-2)">' + CR.esc(s.path) + '</span></div>').join('')
    : '<div class="faint" style="font-size:12.1px;margin-top:12px">' +
      '暂无慢请求。审查与 OCR 属耗时操作，正常会出现在这里。</div>';
}

function renderLog() {
  const act = D.activity || [];
  CR.$('#logMeta').textContent = '审计表共 ' + D.rollup.audit + ' 条 · 显示最近 ' + act.length;
  const box = CR.$('#logStream');
  let fresh = 0;
  box.innerHTML = act.map(function (a) {
    const k = a.created_at + '|' + a.id;
    const isNew = !FIRST && !SEEN[k];
    if (isNew) fresh++;
    SEEN[k] = 1;
    return '<div class="li' + (isNew ? ' fresh' : '') + '">' +
      '<span class="ts">' + CR.esc((a.created_at || '').slice(11, 19)) + '</span>' +
      '<span class="st ' + CR.esc(a.status) + '">' + CR.esc(a.status) + '</span>' +
      '<span class="tl" title="' + CR.esc(a.tool) + '">' + CR.esc(a.tool) + '</span>' +
      '<span class="dt" title="' + CR.esc(a.detail || '') + '">' +
      CR.esc((a.detail || '').replace(/\s+/g, ' ')) + '</span></div>';
  }).join('') || '<div class="faint">暂无审计记录</div>';
  if (fresh && D) {
    CR.$('#liveText').textContent = '新增 ' + fresh + ' 条日志';
    setTimeout(function () {
      if (D) CR.$('#liveText').textContent =
        '服务已运行 ' + sec(D.http.uptime_sec || 0) + ' · 每 4 秒回读';
    }, 2800);
  }
}

/* 流转与调度：只放「流程流转」相关的内容。
   队列水位、数据库大小已经在「资源水位与配额」面板里，这里不再重复 ——
   同一信息出现两处是上一版"乱"的主因之一。 */
function renderOps() {
  const q = D.queue, s = D.scheduler, n = D.notify;
  const blocked = D.blocked_detail || [];
  CR.$('#opsKv').innerHTML = '<div class="metric-list">' +
    mline('定时拉取', s.enabled ? '运行中' : '已关闭',
          s.enabled
            ? '间隔 ' + (s.current_interval || s.interval_sec || 0) + ' 秒 · 累计 ' +
              (s.runs || 0) + ' 轮'
            : '默认关闭（避免演示数据自行变化）') +
    mline('累计提交', q.submitted + ' 个',
          '完成 ' + q.completed + ' · 失败 ' + q.failed +
          (q.rejected_queue_full ? ' · 队列满被拒 ' + q.rejected_queue_full : '')) +
    mline('解析快照', D.rollup.docs + ' 份',
          D.rollup.pages + ' 页 / ' + D.rollup.paras + ' 段 / ' + D.rollup.chars + ' 字符') +
    mline('通知渠道', n.configured ? '已配置' : '未配置',
          '脱敏 ' + ((n.mask || {}).party ? '已开启' : '关闭') +
          ' · 已发 ' + ((n.stats || {}).sent || 0) +
          ' / 失败 ' + ((n.stats || {}).failed || 0)) +
    mline('越权拦截', (D.auth.forbidden || 0) + ' 次',
          '未登录拦截 ' + (D.auth.unauthorized || 0) + ' 次') +
    '</div>';

  CR.$('#blockedBox2').innerHTML = blocked.length
    ? '<div class="sep"></div><div style="font-size:12.2px;color:var(--sub);' +
      'margin-bottom:6px">解析受阻明细（共 ' + D.counts.blocked + ' 个）</div>' +
      blocked.map(b => '<div class="blocked-item">' +
        '<span class="no">#' + b.id + '</span>' +
        '<span class="rs"><b style="font-size:12.3px">' + CR.esc(b.title) + '</b><br>' +
        '<span class="faint">' + CR.esc(b.reason) + '</span></span></div>').join('')
    : '<div class="mode-note">当前没有解析受阻的任务。</div>';
}

function renderRules() {
  const t = D.engines.rules.types || [];
  const max = Math.max.apply(null, t.map(x => x.rules).concat([1]));
  CR.$('#ruleBars').innerHTML = t.map(x =>
    '<div class="rb"><div class="hh"><span>' + CR.esc(x.label) +
    '</span><span class="faint">' + x.rules + ' 条</span></div>' +
    '<div class="bar"><i style="width:' + Math.max(4, x.rules / max * 100) + '%"></i></div></div>'
  ).join('') || '<div class="faint" style="font-size:12.2px">未加载到规则库</div>';
  const m = D.notify.mask || {}, ev = D.notify.events || {};
  const evOn = Object.keys(ev).filter(k => ev[k]).length;
  CR.$('#complianceKv').innerHTML = '<div class="kv-grid">' +
    kv('规则引擎', D.engines.rules.total + ' 条',
       '风险判定 100% 由确定性规则产出，模型只改写措辞') +
    kv('外发脱敏', (m.party && m.amount && m.credit_code) ? '已开启' : '部分关闭',
       '相对方 / 金额 / 信用代码一律掩码，正文片段不外发') +
    kv('事件收敛', evOn + ' / ' + Object.keys(ev).length + ' 开启',
       'batch_summary 与 review_completed 默认关闭') +
    kv('三角色权限', '已启用', '越权 ' + (D.auth.forbidden || 0) + ' 次被拦并写审计') +
    '</div>';
}

/* ------------------------------------------------------------------ 拉取 */
async function refresh() {
  try {
    D = await CR.api('GET', '/api/twin');
  } catch (e) {
    CR.$('#srcNote').innerHTML = '<b style="color:var(--high)">读取 /api/twin 失败：' +
      CR.esc(e.message) + '</b>　页面保留上一次成功的数据。';
    return;
  }
  renderHead();
  renderErrors();
  renderKpi();
  renderPipe();
  renderEngine();
  renderHttp();
  renderLog();
  renderOps();
  renderRules();
  FIRST = false;
  CR.motion.enter();            // 首屏播一次；后续 4 秒刷新不再重播入场
  CR.initReveal();
}

/* ------------------------------------------------------------------ 时钟 */
function tick() {
  const d = new Date();
  CR.$('#clock').textContent = [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(n => String(n).padStart(2, '0')).join(':');
  CR.$('#date').textContent = d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0') + ' ' +
    ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
}

(async function () {
  tick();
  setInterval(tick, 1000);
  await refresh();
  setInterval(refresh, 4000);      // 真实数据 4 秒回读，不做本地造数插值
})();
