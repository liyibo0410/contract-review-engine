# -*- coding: utf-8 -*-
"""孪生看板信息架构重排（针对「引擎负载与资源占用还是乱的」）

上一轮只解决了「数据对不对」，没解决「乱」。「乱」的真实来源有三条：

  1) **一个面板塞了三类内容**：运行指标（kv 卡片）+ 资源水位（仪表）+ 能力清单（kv 卡片），
     三种视觉形态 + 两个小标题 + 两条分隔线，读数时眼睛要来回跳；
  2) **同一信息在多个面板重复**：队列水位、数据库大小同时出现在
     「引擎负载与资源占用」和「队列·调度·存储」两个面板里；
  3) **kv 卡片高度不齐**：value 里嵌了第二行说明，导致卡片 2~3 行不等，栅格看着参差。

这次的修法是**信息架构调整**，不是再修饰样式：

  每个指标只归属一个面板
    引擎运行指标   ← 解析吞吐 / 接口耗时 / 命中密度 / OCR 置信度 / 错误率 / 运行时长
    资源水位与配额 ← 队列水位 / 待处理堆积 / 附件存储 / 数据库（4 个仪表，各带配额分母）
    流转与调度     ← 定时拉取 / 队列累计吞吐 / 通知渠道 / 越权拦截 / 受阻明细
    合规机制与规则库 ← 规则库构成 + 合规口径

  统一视觉形态
    运行指标用**一行一项的对齐列表**（标签 / 数值 / 说明 三列对齐），不再用卡片栅格；
    水位用仪表条（它本来就是占比）；
    能力清单压成**一行**说明文字，不占栅格。

  布局
    中排改成三栏：引擎运行指标 ｜ 资源水位与配额 ｜ 接口响应
    底排维持三栏：实时审查日志 ｜ 流转与调度 ｜ 合规机制与规则库
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:54]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:54]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ================================================================ twin.html 面板骨架
patch("ui/twin.html", [
    # 中排：拆成三栏（引擎运行指标 / 资源水位与配额 / 接口响应）
    ("""  <!-- ============================ 引擎与接口 ============================ -->
  <div class="twin-mid">
    <div class="panel reveal">
      <div class="panel-head">
        <span class="ttl"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3"/></svg>引擎运行指标与资源水位</span>
        <div class="spacer"></div>
        <span class="faint" style="font-size:12.2px" id="qWorkers"></span>
      </div>
      <div class="panel-body">
        <div style="font-size:12.2px;color:var(--sub);margin-bottom:8px">引擎运行指标 · 真实读数</div>
        <div class="kv-grid" id="engineKv"></div>
        <div class="sep"></div>
        <div class="row" style="margin-bottom:9px">
          <span style="font-size:12.2px;color:var(--sub)">资源水位</span>
          <div class="spacer"></div>
          <span class="faint" style="font-size:11.7px" id="idleHint"></span>
        </div>
        <div class="gauge-row" id="gauges"></div>
        <div class="sep"></div>
        <div style="font-size:12.2px;color:var(--sub);margin-bottom:8px">引擎能力 · 配置实测</div>
        <div class="kv-grid" id="engineCap"></div>
      </div>
    </div>

    <div class="panel reveal" data-d="1">""",
     """  <!-- ============================ 引擎 / 水位 / 接口 ============================ -->
  <div class="twin-mid">

    <div class="panel reveal">
      <div class="panel-head">
        <span class="ttl"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3"/></svg>引擎运行指标</span>
        <div class="spacer"></div>
        <span class="faint" style="font-size:12.2px">真实读数</span>
      </div>
      <div class="panel-body">
        <div class="metric-list" id="engineList"></div>
        <div class="mode-note" id="capLine" style="margin-top:10px"></div>
      </div>
    </div>

    <div class="panel reveal" data-d="1">
      <div class="panel-head">
        <span class="ttl"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l8.5 4.6-8.5 4.6L3.5 8.1z"/><path d="M3.5 12.5L12 17l8.5-4.5"/><path d="M3.5 16.5L12 21l8.5-4.5"/></svg>资源水位与配额</span>
        <div class="spacer"></div>
        <span class="faint" style="font-size:12.2px" id="qWorkers"></span>
      </div>
      <div class="panel-body">
        <div class="gauge-row" id="gauges"></div>
        <div class="mode-note" id="idleHint" style="margin-top:11px"></div>
      </div>
    </div>

    <div class="panel reveal" data-d="2">"""),

    # 底排第二个面板：去掉与「资源水位」重复的队列/数据库项，聚焦流转与调度
    ("""      <div class="panel-head">
        <span class="ttl"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l8.5 4.6-8.5 4.6L3.5 8.1z"/><path d="M3.5 12.5L12 17l8.5-4.5"/><path d="M3.5 16.5L12 21l8.5-4.5"/></svg>队列 · 调度 · 存储</span>
        <div class="spacer"></div>
        <span class="faint" style="font-size:12.2px">真实水位</span>
      </div>
      <div class="panel-body"><div id="opsKv"></div></div>""",
     """      <div class="panel-head">
        <span class="ttl"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v4h-4"/></svg>流转与调度</span>
        <div class="spacer"></div>
        <span class="faint" style="font-size:12.2px">队列水位见左侧面板</span>
      </div>
      <div class="panel-body"><div id="opsKv"></div><div id="blockedBox2" style="margin-top:10px"></div></div>"""),
])

# ================================================================ twin.html 渲染逻辑
patch("ui/twin.html", [
    ("""/* 资源水位用的是「上限」做分母 —— 健康状态下必然接近 0。
   所以：值为 0 时标 idle（灰条 + 空闲文案），并给条一个最小可见宽度，
   避免"看着像坏了"；同时在分组标题旁说明这一点。 */
function renderEngine() {""",
     """/* 一行一项的对齐列表：标签 / 数值 / 说明 三列对齐。
   比卡片栅格更适合「读数」——每行的三个信息固定在同一列上，眼睛不用来回找。 */
function mline(k, v, note) {
  return '<div class="ml"><span class="mll">' + CR.esc(k) + '</span>' +
    '<span class="mlv">' + v + '</span>' +
    '<span class="mln">' + CR.esc(note || '') + '</span></div>';
}

function renderEngine() {"""),

    ("""  CR.$('#qWorkers').textContent = '并发度 ' + q.workers + ' · 队列上限 ' + q.maxsize;

  CR.$('#engineKv').innerHTML =
    kv('解析吞吐',
       upSec < 120 ? '—' : (parsed / Math.max(upMin, 0.001)).toFixed(1) + ' 份/分钟',
       upSec < 120 ? '运行时长不足 2 分钟，暂不计算（避免刚重启的假峰值）'
                   : '累计已解析 ' + parsed + ' 份 · 运行 ' + sec(upSec)) +
    kv('接口平均耗时', CR.fmtMs(h.avg_ms),
       '峰值 ' + (peek ? CR.fmtMs(peek) : '—') + ' · 累计 ' + h.requests + ' 次请求') +
    kv('规则命中密度', parsed ? (r.hits / parsed).toFixed(1) + ' 项/份' : '—',
       '累计命中 ' + r.hits + ' 项（高 ' + ((r.hits_by_risk || {}).high || 0) +
       ' · 中 ' + ((r.hits_by_risk || {}).medium || 0) + '）') +
    kv('OCR 置信度', ocrN ? ocrPct + '%' : '无样本',
       ocrN === 0 ? '尚未处理过扫描件'
         : ('样本 ' + ocrN + ' 份' + (ocrN < 3 ? '，样本少仅供参考' : '') +
            ' · 阈值 50%')) +
    kv('错误率', (h.error_rate * 100).toFixed(2) + '%',
       h.errors ? '累计 ' + h.errors + ' 次错误' : '无错误记录') +
    kv('数据库', s.db_kb.toFixed(0) + ' KB',
       '数据文件 ' + s.data_files + ' 个 · 报告 ' + s.reports + ' 份');

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
      '占上限 ' + (s.uploads_mb / q.max_total_mb * 100).toFixed(2) + '% · 上限 ' +
      q.max_total_mb + ' MB') +
    gauge('OCR 置信度', ocrPct, 100, '%',
      ocrN ? '样本 ' + ocrN + ' 份' + (ocrN < 3 ? '（偏少）' : '') + ' · 阈值 50%'
           : '无扫描件样本',
      ocrN === 0 ? 'idle' : (ocrPct >= 50 ? 'ok' : 'hl'));

  const idle = q.queued === 0 && q.pending_cases === 0;
  CR.$('#idleHint').textContent = idle
    ? '空闲状态 —— 水位接近 0 是正常的，表示没有积压'
    : '有在跑的活，注意别接近上限';

  CR.$('#engineCap').innerHTML =
    kv('解析引擎', 'DOCX / PDF / OCR 三路', 'DOCX 与 PDF 走标准库自研解析器') +
    kv('PDF 报告通道', e.pdf.effective === 'browser' ? '浏览器无头打印' : '标准库直出',
       e.pdf.browser ? '本机有浏览器，排版最佳' : '无 chromium，走零依赖通道') +
    kv('规则库', e.rules.total + ' 条 / ' + e.rules.types.length + ' 类',
       e.rules.types.map(t => t.label).join('、')) +
    kv('条款词典', e.clause_types + ' 类', '示范条款库 ' + e.clause_variants + ' 套备选');
}""",
     """  CR.$('#qWorkers').textContent = '并发 ' + q.workers + ' · 队列上限 ' + q.maxsize;

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
    g('数据库', s.db_kb / 1024, Math.max(1, q.max_total_mb / 1024), ' MB',
      '含 ' + s.data_files + ' 个数据文件 · 报告 ' + s.reports + ' 份');

  const idle = q.queued === 0 && q.pending_cases === 0;
  CR.$('#idleHint').innerHTML = '水位以各自的<b>配额</b>为分母。' + (idle
    ? '当前<b>空闲</b> —— 接近 0 是正常的，表示没有积压。'
    : '当前有在跑的活，注意别接近上限。');
}"""),

    # 流转与调度：去掉与资源水位重复的队列/数据库项
    ("""function renderOps() {
  const q = D.queue, s = D.scheduler, st = D.storage, n = D.notify;
  const blocked = D.blocked_detail || [];
  CR.$('#opsKv').innerHTML = '<div class="kv-grid">' +
    kv('审查队列', q.queued + ' / ' + q.maxsize + ' 排队',
       '并发 ' + q.workers + ' · 提交 ' + q.submitted + ' · 完成 ' + q.completed +
       (q.failed ? ' · 失败 ' + q.failed : '')) +
    kv('定时拉取', s.enabled ? '运行中' : '已关闭',
       '间隔 ' + (s.current_interval || s.interval_sec || 0) + ' 秒 · 累计 ' +
       (s.runs || 0) + ' 轮 · 连续失败 ' + (s.consecutive_failures || 0) + ' 次') +
    kv('解析快照', D.rollup.docs + ' 份',
       '共 ' + D.rollup.pages + ' 页 / ' + D.rollup.paras + ' 段 / ' +
       D.rollup.chars + ' 字符') +
    kv('数据库', st.db_kb.toFixed(0) + ' KB', '报告文件 ' + st.reports + ' 份') +
    kv('通知渠道', n.configured ? '已配置' : '未配置',
       '脱敏 ' + ((n.mask || {}).party ? '已开启' : '关闭') +
       ' · 已发 ' + ((n.stats || {}).sent || 0) +
       ' / 失败 ' + ((n.stats || {}).failed || 0)) +
    kv('越权拦截', (D.auth.forbidden || 0) + ' 次',
       '未登录拦截 ' + (D.auth.unauthorized || 0) + ' 次') +
    '</div>' + (blocked.length
      ? '<div class="sep"></div><div style="font-size:12.2px;color:var(--sub);' +
        'margin-bottom:6px">解析受阻明细（共 ' + D.counts.blocked + ' 个）</div>' +
        blocked.map(b => '<div class="blocked-item">' +
          '<span class="no">#' + b.id + '</span>' +
          '<span class="rs"><b style="font-size:12.3px">' + CR.esc(b.title) + '</b><br>' +
          '<span class="faint">' + CR.esc(b.reason) + '</span></span></div>').join('')
      : '');
}""",
     """/* 流转与调度：只放「流程流转」相关的内容。
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
}"""),
])

# ================================================================ CSS
p = os.path.join(ROOT, "ui/assets/app.css")
with io.open(p, "r", encoding="utf-8") as f:
    css = f.read()
FIX = """
/* v2.1.3 · 孪生看板信息架构重排
   「一行一项」的对齐列表：标签 / 数值 / 说明 三列固定对齐。
   比卡片栅格更适合读数 —— 卡片里 value 嵌了第二行说明会导致高度参差，
   三列列表则每行都在同一列上，扫读成本最低。 */
.metric-list { display: flex; flex-direction: column; }
.metric-list .ml {
  display: grid;
  grid-template-columns: 92px minmax(84px, 118px) minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line-2);
}
.metric-list .ml:last-child { border-bottom: 0; }
.metric-list .ml .mll { font-size: 12.3px; color: var(--sub); }
.metric-list .ml .mlv {
  font-size: 13.6px; font-weight: 650; color: var(--ink);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.metric-list .ml .mln { font-size: 11.9px; color: var(--faint); line-height: 1.65; }
@media (max-width: 640px) {
  .metric-list .ml { grid-template-columns: 84px minmax(0, 1fr); }
  .metric-list .ml .mln { grid-column: 1 / -1; }
}
/* 中排改三栏：引擎运行指标 ｜ 资源水位与配额 ｜ 接口响应 */
.twin-mid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
@media (max-width: 1280px) { .twin-mid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 820px) { .twin-mid { grid-template-columns: 1fr; } }
"""
if "v2.1.3 · 孪生看板信息架构重排" not in css:
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(css + FIX)
    log.append("  ok   ui/assets/app.css :: 追加 v2.1.3 信息架构重排")
else:
    log.append("  skip ui/assets/app.css")

with io.open(os.path.join(ROOT, "verify", "engine_fix2.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("engine fix2 done")
