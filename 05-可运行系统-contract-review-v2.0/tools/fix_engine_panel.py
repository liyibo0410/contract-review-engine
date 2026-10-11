# -*- coding: utf-8 -*-
"""重构「引擎负载与资源占用」面板（由专项探针发现的 4 个真实问题）

探针结果：
  ① 四个仪表里三个**永远是空的** —— 队列 0/50、待处理 0/40、附件存储 0.89/2048MB
     占比 0.00% / 0.00% / 0.04%，条看不见。分母是"上限"，健康状态下必然显示为空，
     看起来像坏了。
  ② 标题叫「引擎负载」，内容却是队列与存储水位 —— 名不副实。
  ③ OCR 置信度 100% 只基于 1 份样本，却给了满格绿条。
  ④ 下半 4 个 kv（解析引擎 / PDF 通道 / 规则库 / 条款词典）是**能力清单**不是负载。

修法：
  * 面板拆成两段，各司其职：
      「引擎运行指标」—— 真实绝对值（解析吞吐、平均/峰值接口耗时、命中密度、
                         OCR 置信度含样本数、运行时长、累计请求与错误率）
      「资源水位」—— 保留占比条（水位天然是占比），但
                        值为 0 时标为 idle（灰条 + "空闲"文案），条有最小可见宽度，
                        并在标题旁说明「空闲时接近 0 属正常」
  * 吞吐在运行时长不足 2 分钟时**不计算**（显示"—"），避免刚重启就显示 359 份/分钟这种假峰值
  * OCR 样本 < 3 时明确标注「样本少，仅供参考」；无扫描件样本时不显示 100%
  * 能力清单单独成组，标题改「引擎能力」
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
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:56]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:56]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ================================================================ twin.html 面板骨架
patch("ui/twin.html", [
    ("""      <div class="panel-head">
        <span class="ttl"><svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3"/></svg>引擎负载与资源占用</span>
        <div class="spacer"></div>
        <span class="faint" style="font-size:12.2px" id="qWorkers"></span>
      </div>
      <div class="panel-body">
        <div class="gauge-row" id="gauges"></div>
        <div class="sep"></div>
        <div id="engineKv"></div>
      </div>""",
     """      <div class="panel-head">
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
      </div>"""),
])

# ================================================================ twin.html renderEngine
patch("ui/twin.html", [
    ("""function renderEngine() {
  const q = D.queue, e = D.engines, s = D.storage;
  CR.$('#qWorkers').textContent = '并发度 ' + q.workers + ' · 队列上限 ' + q.maxsize;
  CR.$('#gauges').innerHTML =
    gauge('队列水位', q.queued, q.maxsize, ' 个',
      '在飞 ' + (q.inflight || []).length + ' 个 · 上限 ' + q.maxsize + ' 个',
      q.queued / q.maxsize > .8 ? 'warn' : 'ok') +
    gauge('待处理堆积', q.pending_cases, q.max_pending, ' 个',
      '上限 ' + q.max_pending + ' 个 · 累计提交 ' + q.submitted + ' 个',
      q.pending_cases / q.max_pending > .8 ? 'warn' : 'ok') +
    gauge('附件存储', s.uploads_mb, q.max_total_mb, ' MB',
      '上限 ' + q.max_total_mb + ' MB · 数据文件 ' + s.data_files + ' 个',
      s.uploads_mb / q.max_total_mb > .8 ? 'warn' : 'ok') +
    gauge('OCR 平均置信度', Math.round((e.ocr.avg_confidence || 0) * 100), 100, '%',
      '已识别 ' + e.ocr.docs + ' 份扫描件 · 阈值 50%',
      (e.ocr.avg_confidence || 0) >= .5 ? 'ok' : 'hl');
  CR.$('#engineKv').innerHTML = '<div class="kv-grid">' +
    kv('解析引擎', 'DOCX / PDF / OCR 三路', 'DOCX 与 PDF 走标准库自研解析器') +
    kv('PDF 报告通道', e.pdf.effective === 'browser' ? '浏览器无头打印' : '标准库直出',
       e.pdf.browser ? '本机有浏览器，排版最佳' : '无 chromium，走零依赖通道') +
    kv('规则库', e.rules.total + ' 条 / ' + e.rules.types.length + ' 类',
       e.rules.types.map(t => t.label).join('、')) +
    kv('条款词典', e.clause_types + ' 类', '示范条款库 ' + e.clause_variants + ' 套备选') +
    '</div>';
}""",
     """/* 资源水位用的是「上限」做分母 —— 健康状态下必然接近 0。
   所以：值为 0 时标 idle（灰条 + 空闲文案），并给条一个最小可见宽度，
   避免"看着像坏了"；同时在分组标题旁说明这一点。 */
function renderEngine() {
  const q = D.queue, e = D.engines, s = D.storage, h = D.http, r = D.rollup;
  const upSec = h.uptime_sec || 0;
  const upMin = upSec / 60;
  const parsed = r.docs || 0;
  const ocrN = e.ocr.docs || 0;
  const ocrPct = ocrN ? Math.round((e.ocr.avg_confidence || 0) * 100) : 0;
  const peek = (h.top_paths || []).reduce((m, t) => Math.max(m, t.max_ms || 0), 0);

  CR.$('#qWorkers').textContent = '并发度 ' + q.workers + ' · 队列上限 ' + q.maxsize;

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
}"""),
])

# ================================================================ CSS：idle 条 + 最小可见宽度
p = os.path.join(ROOT, "ui/assets/app.css")
with io.open(p, "r", encoding="utf-8") as f:
    css = f.read()
FIX = """
/* v2.1.2 · 资源水位条的可读性修复
   仪表以「上限」为分母，健康时占比接近 0 —— 条会完全看不见，像坏了。
   这里给条一个最小可见宽度，并为「值为 0」定义中性色（idle），
   让"空闲"看起来是"空闲"，而不是"空白/故障"。 */
.gauge .bar i { min-width: 2px; }
.gauge.idle .bar i { background: var(--faint); }
.gauge.idle .bar { background: var(--panel-3); }
.gauge.idle .k b { color: var(--sub); }
"""
if "v2.1.2 · 资源水位条的可读性修复" not in css:
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(css + FIX)
    log.append("  ok   ui/assets/app.css :: 追加 v2.1.2 仪表可读性修复")
else:
    log.append("  skip ui/assets/app.css")

with io.open(os.path.join(ROOT, "verify", "engine_fix.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("engine fix done")
