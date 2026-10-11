# -*- coding: utf-8 -*-
"""前端细节修复（由 CDP 几何体检发现的真实问题）

体检结论（1600px 视口）：
  * 顶栏高 94px = **两行** —— 环境标签宽 612px（把 base_url/OCR/PDF/版本全塞进去），
    把后面的按钮挤到第二行；这是 v2.0.1 改 `min-height + wrap` 时引入的回归；
  * 981~1280px 区间按钮跨行跳位（hide-sm 只在 ≤980 生效），布局凌乱；
  * 统计卡 176px 高，底边到副标题之间空出 64px，
    「查看明细 →」浮在底部空白里，视觉上像没排好；
  * 390px 下顶栏 132px = 三行（品牌 146 + 角色区 230 = 376 > 可用 366，差 10px 就掉行）。

修法：
  1) 环境标签只留「演示环境 · vX.Y.Z」，详细信息进 title（悬停可见）；
  2) 顶栏做**确定性阶梯**：≥1180px 不换行；宽度不足时按 隐藏模拟审批链接 → 隐藏环境标签 → 允许换行
     的顺序收，不再出现「按钮跳到第二行又留着大片空白」；
  3) 「查看明细 →」移到卡片右上角（与标题同行），不再浮在底部空白里；
  4) ≤640 隐藏角色文字 chip（下拉框本身就显示角色名），让品牌与角色区同排。
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


# ================================================================ app.js
patch("ui/assets/app.js", [
    # 1) renderTopbar 支持第三个参数（环境详情走 title）；按钮用 hide-md 收放
    ("""  function renderTopbar(active, envText) {""",
     """  function renderTopbar(active, envText, envDetail) {"""),
    ("""      '<span class="env-tag" id="envTag">' + esc(envText || '本地演示环境') + '</span>',
      '<a class="btn sm ghost hide-sm" href="/mock" title="模拟审批系统（美化查看页）">' +
        icon('external', '', 13) + '模拟审批系统</a>',
      '<button class="btn sm ghost hide-sm" id="themeBtn" onclick="CR.toggleTheme()"></button>'""",
     """      '<span class="env-tag" id="envTag" title="' +
        esc(envDetail || envText || '') + '">' + esc(envText || '本地演示环境') + '</span>',
      '<a class="btn sm ghost hide-md" href="/mock" title="模拟审批系统（美化查看页）">' +
        icon('external', '', 13) + '模拟审批系统</a>',
      '<button class="btn sm ghost hide-xs" id="themeBtn" onclick="CR.toggleTheme()"></button>'"""),
    # 2) initAuth 透传详情
    ("""  function initAuth(active, envText) {
    return authMe(true).then(function (m) {
      renderTopbar(active, envText);""",
     """  function initAuth(active, envText, envDetail) {
    return authMe(true).then(function (m) {
      renderTopbar(active, envText, envDetail);"""),
])

# ================================================================ index.html
patch("ui/index.html", [
    ("""    envText = '演示环境 · ' +
      (h.approval_channel === 'feishu_mock' ? '内置模拟审批' : String(h.approval_base)) +
      ' · OCR ' + h.ocr_backend +
      ' · PDF ' + (h.pdf_channels && h.pdf_channels.effective === 'stdlib'
        ? '标准库直出' : '浏览器通道') +
      ' · v' + h.version;
  } catch (e) { }
  // 先取当前身份与能力，再渲染顶栏并执行权限显隐（隐藏只是体验，拦截在服务端）
  try { await CR.initAuth('/', envText); } catch (e) { CR.renderTopbar('/', envText); }""",
     """    // 顶栏只放最必要的信息（太长会把按钮挤到第二行）；
    // 完整信息放 title，鼠标悬停可看。
    envText = '演示环境 · v' + h.version;
    envDetail = '审批通道：' +
      (h.approval_channel === 'feishu_mock' ? '内置模拟审批' : String(h.approval_base)) +
      ' ｜ OCR：' + h.ocr_backend +
      ' ｜ PDF：' + (h.pdf_channels && h.pdf_channels.effective === 'stdlib'
        ? '服务端标准库直出' : '浏览器无头打印') +
      ' ｜ 版本：v' + h.version;
  } catch (e) { }
  // 先取当前身份与能力，再渲染顶栏并执行权限显隐（隐藏只是体验，拦截在服务端）
  try { await CR.initAuth('/', envText, envDetail); }
  catch (e) { CR.renderTopbar('/', envText, envDetail); }"""),
    ("  let envText = '演示环境 · 模拟审批系统';",
     "  let envText = '演示环境';\n  let envDetail = '';"),
])

# ================================================================ app.css
patch("ui/assets/app.css", [
    # 环境标签：定宽 + 省略号，绝不撑宽顶栏
    ("""    .env-tag { display: none; }              /* 收起环境标签 */""",
     """    .env-tag { display: none; }              /* 收起环境标签 */"""),
])

p = os.path.join(ROOT, "ui/assets/app.css")
with io.open(p, "r", encoding="utf-8") as f:
    css = f.read()

FIX = """

/* ==========================================================================
   v2.1.1 · 顶栏与统计卡细节修复（由 CDP 几何体检发现的真实问题）
   --------------------------------------------------------------------------
   体检发现：1600px 视口下顶栏高 94px = 两行，元凶是宽 612px 的环境标签，
   它把「模拟审批系统 / 主题」两个按钮挤到第二行，顶栏下方还留着大片空白。
   这里做三件事：收窄环境标签、给顶栏定一条确定性的收放阶梯、把统计卡的
   下钻提示移到右上角（原来浮在底部 64px 空白里，像没排好）。
   ========================================================================== */

/* 1) 环境标签定宽 + 省略号（详细信息看 title） */
.env-tag {
  max-width: 176px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 0 0 auto;
}

/* 2) 顶栏收放阶梯（确定性，不再出现「按钮跳第二行」）
   ≥1440  全部显示（一行）
   1180+  收起「模拟审批系统」链接与环境标签（一行）
   <1180  允许换行（品牌+角色一行，导航第二行）
   ≤640   再收起角色文字 chip，保证品牌与角色区同排 */
@media (min-width: 1180px) {
  .topbar { flex-wrap: nowrap; }
}
@media (max-width: 1439px) {
  .env-tag { display: none; }
  .hide-md { display: none !important; }
}
@media (max-width: 560px) {
  .hide-xs { display: none !important; }     /* 主题切换（系统暗色可用） */
}
@media (max-width: 640px) {
  .role-chip { display: none; }              /* 下拉框本身就显示角色名 */
  .role-bar { gap: 6px; }
}

/* 3) 统计卡：下钻提示移到右上角，与标题同行 */
.stat.clickable::after {
  top: 12px;
  right: 14px;
  bottom: auto;
}
.stat .k { padding-right: 62px; }
.stat.clickable:hover::after,
.stat.clickable:focus-visible::after { transform: translateX(0); }
.stat.clickable::after { transform: none; }

/* 4) 统计卡内部节奏：收紧一点，避免「高卡片底部大片空」 */
.stat { padding: 13px 16px 13px; }
.stat .v { margin-top: 4px; }
.stat .x { margin-top: 2px; }
.stat .bar { margin-top: 9px !important; }
"""
if "v2.1.1 · 顶栏与统计卡细节修复" not in css:
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(css + FIX)
    log.append("  ok   ui/assets/app.css :: 追加 v2.1.1 修复块")
else:
    log.append("  skip ui/assets/app.css（已存在）")

# ================================================================ pet.js：不再主动弹窗
patch("ui/assets/pet.js", [
    ("""    // 首次进入邀请一次（每会话一次，不打扰）
    var greeted = null;
    try { greeted = sessionStorage.getItem(LS + 'greeted'); } catch (e) { }
    if (!greeted) {
      var b = $('#petBadge');
      if (b) { b.classList.add('on'); }
      setTimeout(function () {
        if (S.open) { return; }
        open();
      }, GUEST ? 900 : 1400);
    }""",
     """    // 首次进入**不主动弹窗**（弹窗会盖住页面内容，打扰操作）：
    // 只在宠物上方飘一句提示、加一个小红点，用户点了才展开。
    var greeted = null;
    try { greeted = sessionStorage.getItem(LS + 'greeted'); } catch (e) { }
    if (!greeted) {
      var b = $('#petBadge');
      if (b) { b.classList.add('on'); }
      var tip = $('.pet-tip');
      var btn = $('#petBtn');
      if (tip) { tip.classList.add('show'); }
      if (btn) { btn.classList.add('attn'); }
      setTimeout(function () {
        if (tip) { tip.classList.remove('show'); }
        if (btn) { btn.classList.remove('attn'); }
        try { sessionStorage.setItem(LS + 'hinted', '1'); } catch (e) { }
      }, 7000);
    }"""),
    # 提示气泡与注意动效样式
    ("""      '.pet-btn:hover .pet-tip{opacity:1;transform:translateX(0)}',""",
     """      '.pet-btn:hover .pet-tip{opacity:1;transform:translateX(0)}',
      '.pet-tip.show{opacity:1;transform:translateX(0)}',
      '.pet-btn.attn{animation:petAttn 1.6s ease-in-out 3}',
      '@keyframes petAttn{0%,100%{transform:none}50%{transform:translateY(-6px) scale(1.05)}}',"""),
])

with io.open(os.path.join(ROOT, "verify", "front_fix1.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("front fix1 done")
