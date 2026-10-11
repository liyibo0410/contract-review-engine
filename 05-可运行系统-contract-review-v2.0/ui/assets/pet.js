/* ==========================================================================
   衡鉴 V2.1.7 · AI 助手（页面宠物）
   --------------------------------------------------------------------------
   三件事：
     1) 悬浮宠物 —— 内联 SVG、零依赖、可拖动并记忆位置、移动端自适应；
     2) 点击开聊天 —— 消息流 / 快捷提问 / 正在输入态 / Esc 关闭 / 键盘可达；
     3) 新手步骤引导 —— 聚光灯高亮真实元素，可以「点到哪一步走哪一步」，
        跨页面续接，遇到当前角色没权限的步骤会提示并支持一键切角色。

   设计口径：
     * 助手只读不写 —— 它没有任何改库/回写路径，只能回答与指路；
     * 回答全部来自服务端（/api/assistant/*），前端不硬编码业务事实；
     * 登录页没有接口权限，使用内置的游客小知识库，并明确告知「登录后有完整引导」。
   ========================================================================== */
(function (global) {
  'use strict';

  var CR = global.CR;
  if (!CR) { return; }

  var $ = CR.$, $$ = CR.$$;
  var PAGE = (function () {
    var p = location.pathname.replace(/\/+$/, '') || '/';
    if (p === '/index.html' || p === '') { p = '/'; }
    return p.indexOf('/workbench') === 0 ? '/workbench' : p;
  })();
  var GUEST = PAGE === '/login';
  var LS = 'cr-pet-';
  var SS_TOUR = 'cr-pet-tour';

  /* ---------------------------------------------------------------- 状态 */
  var S = {
    open: false,
    busy: false,
    msgs: [],
    chips: [],
    tour: null,          // {page, title, steps, i}
    petDragged: false
  };

  /* 登录页的游客知识库：只有登录/账号相关，不涉及任何业务数据 */
  var GUEST_KB = [
    ["怎么登录？", "输入账号与口令登录。也可以直接用右侧三个演示身份的「一键进入」——\n\n" +
      "演示账号：`legal` / `Legal@2026`、`applicant` / `Apply@2026`、`admin` / `Admin@2026`。\n\n" +
      "口令以 pbkdf2-sha256 哈希存储，明文不落库；会话 Cookie 是 HttpOnly，脚本读不到。"],
    ["三个身份有什么区别？", "**业务经办人**负责送审、看风险点、下载报告；\n" +
      "**法务审查人**负责核对原文、采纳或调整示范条款、写回审批意见；\n" +
      "**系统管理员**负责维护规则与标准条款库、处理解析受阻、查看运行指标。\n\n" +
      "登录后我可以在每个页面带你走一遍。"],
    ["登录之后能做什么？", "能看到审查大盘、智能审查工作台、动态孪生看板；\n" +
      "管理员还能进合规规则与模板页。\n\n" +
      "登录后点我，我会按你的角色给出**一步一步的引导**。"],
    ["这个系统是干什么的？", "衡鉴 V2.1.7：把待审合同自动解析、切条款、比对公司合规规则，" +
      "生成中文审查意见，并**写回审批单评论区**。\n\n审批权始终在人手里。"]
  ];
  var GUEST_CHIPS = ["怎么登录？", "三个身份有什么区别？", "登录之后能做什么？"];

  /* ================================================================ 样式挂载 */
  function injectStyle() {
    if ($('#petStyle')) { return; }
    var st = document.createElement('style');
    st.id = 'petStyle';
    st.textContent = [
      /* ---- 宠物球 ---- */
      '.pet-root{position:fixed;right:20px;bottom:20px;z-index:9000;display:flex;',
      'flex-direction:column;align-items:flex-end;gap:10px;pointer-events:none}',
      '.pet-root>*{pointer-events:auto}',
      '.pet-btn{position:relative;width:62px;height:62px;border-radius:50%;cursor:grab;',
      'border:1px solid var(--line);background:var(--panel-solid,#121a28);',
      'box-shadow:0 12px 30px -10px rgba(0,0,0,.55),0 0 0 1px var(--brand-soft);',
      'display:flex;align-items:center;justify-content:center;padding:0;',
      'transition:transform .22s cubic-bezier(.22,.68,.36,1),box-shadow .22s}',
      '.pet-btn:hover{transform:translateY(-3px) scale(1.04);box-shadow:0 16px 36px -10px rgba(0,0,0,.6),0 0 0 3px var(--brand-soft)}',
      '.pet-btn:focus-visible{outline:2px solid var(--brand);outline-offset:3px}',
      '.pet-btn svg{width:40px;height:40px;overflow:visible}',
      '.pet-btn .pet-a1{animation:petBreathe 3.2s ease-in-out infinite;transform-origin:50% 50%}',
      '.pet-btn .pet-a2{animation:petBreathe 3.2s ease-in-out infinite .35s;transform-origin:50% 50%}',
      '.pet-btn .pet-eye{animation:petBlink 4.4s infinite}',
      '.pet-btn.think .pet-a1,.pet-btn.think .pet-a2{animation-duration:.9s}',
      '@keyframes petBreathe{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-2.5px) scale(1.035)}}',
      '@keyframes petBlink{0%,92%,100%{transform:scaleY(1)}96%{transform:scaleY(.12)}}',
      // 徽标底色的对比度是量过的：--high 在深色主题下配 #fff 只有 2.51:1（低于 AA 4.5:1），
      // 所以这里用一个固定的无障碍红（#fff 在其上为 6.5:1），深浅主题都达标。
      '.pet-badge{position:absolute;top:-2px;right:-2px;min-width:18px;height:18px;',
      'border-radius:999px;background:#b3261e;color:#fff;font-size:11px;line-height:18px;',
      'text-align:center;padding:0 5px;font-weight:700;display:none}',
      '.pet-badge.on{display:block}',
      '.pet-tip{position:absolute;right:74px;bottom:12px;white-space:nowrap;font-size:12.5px;',
      'background:var(--panel-solid,#121a28);border:1px solid var(--line);color:var(--ink);',
      'padding:6px 11px;border-radius:999px;opacity:0;transform:translateX(6px);',
      'transition:opacity .2s,transform .2s;pointer-events:none;box-shadow:var(--shadow-sm)}',
      '.pet-btn:hover .pet-tip{opacity:1;transform:translateX(0)}',
      '.pet-tip.show{opacity:1;transform:translateX(0)}',
      '.pet-btn.attn{animation:petAttn 1.6s ease-in-out 3}',
      '@keyframes petAttn{0%,100%{transform:none}50%{transform:translateY(-6px) scale(1.05)}}',

      /* ---- 聊天面板 ---- */
      '.pet-panel{width:min(392px,calc(100vw - 32px));max-height:min(620px,calc(100vh - 110px));',
      'display:none;flex-direction:column;background:var(--panel-solid,#121a28);',
      'border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:var(--shadow)}',
      '.pet-panel.on{display:flex;animation:petIn .24s cubic-bezier(.22,.68,.36,1)}',
      '@keyframes petIn{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:none}}',
      '.pet-ph{display:flex;align-items:center;gap:9px;padding:11px 13px;',
      'border-bottom:1px solid var(--line);background:var(--panel-3)}',
      '.pet-ph b{font-size:13.6px}',
      '.pet-ph .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex:none}',
      '.pet-ph .mode{font-size:11.3px;color:var(--sub)}',
      '.pet-ph .spacer{flex:1}',
      '.pet-x{width:26px;height:26px;border-radius:8px;border:1px solid var(--line);',
      'background:transparent;color:var(--sub);cursor:pointer;line-height:1;font-size:15px}',
      '.pet-x:hover{color:var(--ink);border-color:var(--brand)}',
      '.pet-body{flex:1;overflow-y:auto;padding:13px;display:flex;flex-direction:column;gap:11px}',
      '.pet-msg{display:flex;gap:8px;align-items:flex-start}',
      '.pet-msg .av{width:24px;height:24px;border-radius:8px;flex:none;display:flex;',
      'align-items:center;justify-content:center;background:var(--brand-soft);color:var(--brand);font-size:12px}',
      '.pet-msg .bd{background:var(--panel-3);border:1px solid var(--line-2);',
      'border-radius:2px 12px 12px 12px;padding:9px 12px;font-size:13px;line-height:1.78;',
      'max-width:100%;word-break:break-word}',
      '.pet-msg.me{flex-direction:row-reverse}',
      '.pet-msg.me .av{background:var(--panel-3);color:var(--sub)}',
      '.pet-msg.me .bd{background:var(--brand-soft);border-color:transparent;',
      'border-radius:12px 2px 12px 12px}',
      '.pet-msg .bd p{margin:0 0 7px}.pet-msg .bd p:last-child{margin-bottom:0}',
      '.pet-msg .bd ul,.pet-msg .bd ol{margin:5px 0;padding-left:19px}',
      '.pet-msg .bd li{margin:3px 0}',
      '.pet-msg .bd code{font-family:var(--mono);font-size:11.9px;background:var(--panel-3);',
      'border:1px solid var(--line-2);border-radius:5px;padding:1px 5px}',
      '.pet-msg .bd table{border-collapse:collapse;font-size:12.3px;margin:6px 0;width:100%}',
      '.pet-msg .bd th,.pet-msg .bd td{border:1px solid var(--line-2);padding:4px 8px;text-align:left}',
      '.pet-msg .bd th{color:var(--sub);font-weight:600}',
      '.pet-msg .bd hr{border:0;border-top:1px dashed var(--line-2);margin:8px 0}',
      '.pet-refs{font-size:11.2px;color:var(--faint);margin-top:6px}',
      '.pet-acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}',
      '.pet-act{font-size:12.2px;padding:4px 10px;border-radius:999px;cursor:pointer;',
      'border:1px solid var(--brand);background:transparent;color:var(--brand)}',
      '.pet-act:hover{background:var(--brand-soft)}',
      '.pet-typing span{display:inline-block;width:5px;height:5px;margin:0 1.5px;border-radius:50%;',
      'background:var(--sub);animation:petT 1.1s infinite}',
      '.pet-typing span:nth-child(2){animation-delay:.16s}',
      '.pet-typing span:nth-child(3){animation-delay:.32s}',
      '@keyframes petT{0%,60%,100%{opacity:.25;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}',
      '.pet-chips{display:flex;gap:6px;flex-wrap:wrap;padding:0 13px 10px}',
      '.pet-chip{font-size:12.1px;padding:4px 10px;border-radius:999px;cursor:pointer;',
      'background:var(--panel-3);border:1px solid var(--line);color:var(--sub)}',
      '.pet-chip:hover{color:var(--ink);border-color:var(--brand)}',
      '.pet-pf{display:flex;gap:7px;padding:11px 13px;border-top:1px solid var(--line);',
      'background:var(--panel-3)}',
      '.pet-pf input{flex:1;min-width:0;font-size:13px;padding:8px 11px;border-radius:9px;',
      'background:var(--bg);color:var(--ink);border:1px solid var(--line);outline:none}',
      '.pet-pf input:focus{border-color:var(--brand)}',
      '.pet-pf button{flex:none;padding:8px 14px;border-radius:9px;border:1px solid var(--brand);',
      'background:var(--brand);color:#fff;font-size:13px;cursor:pointer}',
      '.pet-pf button:disabled{opacity:.5;cursor:not-allowed}',

      /* ---- 引导 ---- */
      '.pet-spot{position:fixed;z-index:9500;border-radius:10px;pointer-events:none;',
      'box-shadow:0 0 0 9999px rgba(4,8,16,.66),0 0 0 2px var(--brand),0 0 22px 2px var(--brand-soft);',
      'transition:top .26s cubic-bezier(.22,.68,.36,1),left .26s cubic-bezier(.22,.68,.36,1),',
      'width .26s,height .26s;animation:petPulse 2.2s ease-in-out infinite}',
      '@keyframes petPulse{0%,100%{box-shadow:0 0 0 9999px rgba(4,8,16,.66),0 0 0 2px var(--brand),0 0 18px 2px var(--brand-soft)}',
      '50%{box-shadow:0 0 0 9999px rgba(4,8,16,.66),0 0 0 3px var(--brand),0 0 30px 6px var(--brand-soft)}}',
      '.pet-coach{position:fixed;z-index:9600;width:min(352px,calc(100vw - 28px));',
      'background:var(--panel-solid,#121a28);border:1px solid var(--brand);border-radius:14px;',
      'box-shadow:0 18px 48px -14px rgba(0,0,0,.7);overflow:hidden}',
      '.pet-coach .ch{display:flex;align-items:center;gap:8px;padding:9px 12px;',
      'border-bottom:1px solid var(--line-2);font-size:11.8px;color:var(--sub)}',
      '.pet-coach .ch b{color:var(--ink);font-size:12.6px}',
      '.pet-coach .cb{padding:11px 13px;font-size:12.8px;line-height:1.8;max-height:44vh;overflow-y:auto}',
      '.pet-coach .cb .hint{margin-top:8px;font-size:11.8px;color:var(--mid)}',
      '.pet-coach .cf{display:flex;gap:7px;align-items:center;padding:10px 12px;',
      'border-top:1px solid var(--line-2);background:var(--panel-3)}',
      '.pet-coach .cf .spacer{flex:1}',
      '.pet-coach button{font-size:12.4px;padding:5px 12px;border-radius:8px;cursor:pointer;',
      'border:1px solid var(--line);background:transparent;color:var(--ink)}',
      '.pet-coach button.primary{background:var(--brand);border-color:var(--brand);color:#fff}',
      '.pet-coach button:disabled{opacity:.45;cursor:not-allowed}',
      '.pet-prog{display:flex;gap:3px}',
      '.pet-prog i{width:16px;height:3px;border-radius:999px;background:var(--line);display:block}',
      '.pet-prog i.on{background:var(--brand)}',
      '.pet-wait{font-size:11.6px;color:var(--mid)}',

      /* ---- 移动端 ---- */
      '@media (max-width:640px){',
      '.pet-root{right:12px;bottom:12px}',
      '.pet-btn{width:54px;height:54px}.pet-btn svg{width:34px;height:34px}',
      '.pet-panel{width:calc(100vw - 24px);max-height:calc(100vh - 96px)}',
      '.pet-tip{display:none}',
      '.pet-coach{left:12px !important;right:12px;width:auto}',
      '}',
      '@media (prefers-reduced-motion:reduce){',
      '.pet-btn .pet-a1,.pet-btn .pet-a2,.pet-btn .pet-eye,.pet-spot{animation:none}',
      '}'
    ].join('');
    document.head.appendChild(st);
  }

  /* ================================================================ 宠物 */
  var PET_SVG = [
    '<svg viewBox="0 0 48 48" fill="none" aria-hidden="true">',
    '<defs><linearGradient id="petG" x1="0" y1="0" x2="1" y2="1">',
    '<stop offset="0" stop-color="#5aa8ff"/><stop offset="1" stop-color="#2fbfa8"/>',
    '</linearGradient></defs>',
    '<ellipse class="pet-a1" cx="24" cy="30" rx="14" ry="12" fill="url(#petG)" opacity=".22"/>',
    '<path class="pet-a2" d="M11 20c0-8 5.8-13 13-13s13 5 13 13v9c0 6-5.8 10-13 10s-13-4-13-10z"',
    ' fill="url(#petG)" opacity=".9"/>',
    '<path d="M13 15l3.5-8 4.5 6.2M35 15l-3.5-8-4.5 6.2" stroke="url(#petG)"',
    ' stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    '<g class="pet-eye"><circle cx="19.5" cy="24" r="2.6" fill="#0b1220"/>',
    '<circle cx="28.8" cy="24" r="2.6" fill="#0b1220"/>',
    '<circle cx="20.4" cy="23.1" r=".9" fill="#fff"/>',
    '<circle cx="29.7" cy="23.1" r=".9" fill="#fff"/></g>',
    '<path d="M21 31c1.8 1.6 4.2 1.6 6 0" stroke="#0b1220" stroke-width="1.9"',
    ' stroke-linecap="round" fill="none"/>',
    '</svg>'
  ].join('');

  function buildPet() {
    if ($('#petRoot')) { return; }
    var root = document.createElement('div');
    root.className = 'pet-root';
    root.id = 'petRoot';
    root.innerHTML =
      '<div class="pet-panel" id="petPanel" role="dialog" aria-label="AI 助手对话框"></div>' +
      '<button class="pet-btn" id="petBtn" type="button" aria-expanded="false" ' +
      'aria-controls="petPanel" title="点我提问，或让我带你走一遍">' +
      PET_SVG + '<span class="pet-tip">有问题点我，或让我带你走一遍</span>' +
      '<span class="pet-badge" id="petBadge">1</span></button>';
    document.body.appendChild(root);

    var btn = $('#petBtn');
    btn.addEventListener('click', function () {
      if (S.petDragged) { S.petDragged = false; return; }
      toggle();
    });
    makeDraggable(btn);
    restorePetPos();

    var panel = $('#petPanel');
    panel.innerHTML = chatShell();
    bindChat();
  }

  function chatShell() {
    return '<div class="pet-ph">' +
      '<span class="dot" id="petDot"></span><b>小亦 · 使用助手</b>' +
      '<span class="mode" id="petMode"></span><div class="spacer"></div>' +
      '<button class="pet-x" id="petTour" type="button" title="开始新手引导">引导</button>' +
      '<button class="pet-x" id="petClose" type="button" aria-label="关闭">×</button>' +
      '</div>' +
      '<div class="pet-body" id="petBody" aria-live="polite"></div>' +
      '<div class="pet-chips" id="petChips"></div>' +
      '<div class="pet-pf"><input id="petInput" type="text" autocomplete="off" ' +
      'placeholder="问点关于审查、权限、操作的问题…" aria-label="输入问题">' +
      '<button id="petSend" type="button">发送</button></div>';
  }

  function bindChat() {
    $('#petClose').addEventListener('click', close);
    $('#petTour').addEventListener('click', function () { startTour(PAGE); });
    $('#petSend').addEventListener('click', function () { send($('#petInput').value); });
    $('#petInput').addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send(this.value); }
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') {
        if (S.tour) { stopTour('已退出引导'); return; }
        if (S.open) { close(); }
      }
    });
  }

  /* 拖动 + 位置记忆（只允许左右两个停靠位，避免挡住页面内容） */
  function makeDraggable(btn) {
    var dragging = false, sx = 0, sy = 0, moved = false;
    btn.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) { return; }
      dragging = true; moved = false;
      sx = ev.clientX; sy = ev.clientY;
      btn.style.cursor = 'grabbing';
      try { btn.setPointerCapture(ev.pointerId); } catch (e) { }
    });
    btn.addEventListener('pointermove', function (ev) {
      if (!dragging) { return; }
      if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 6) { moved = true; }
      if (!moved) { return; }
      var root = $('#petRoot');
      root.style.left = Math.max(8, Math.min(global.innerWidth - 78, ev.clientX - 31)) + 'px';
      root.style.top = Math.max(8, Math.min(global.innerHeight - 78, ev.clientY - 31)) + 'px';
      root.style.right = 'auto'; root.style.bottom = 'auto';
    });
    btn.addEventListener('pointerup', function (ev) {
      if (!dragging) { return; }
      dragging = false;
      btn.style.cursor = 'grab';
      S.petDragged = moved;
      var root = $('#petRoot');
      if (moved) {
        var r = root.getBoundingClientRect();
        var right = r.left + r.width / 2 > global.innerWidth / 2;
        root.style.left = 'auto'; root.style.right = right ? '20px' : 'auto';
        if (!right) { root.style.left = '20px'; }
        try {
          localStorage.setItem(LS + 'pos', JSON.stringify({
            top: r.top, right: right
          }));
        } catch (e) { }
        setTimeout(function () { S.petDragged = false; }, 60);
      }
    });
  }

  function restorePetPos() {
    var pos = CR.load(LS + 'pos', null);
    if (!pos || typeof pos.top !== 'number') { return; }
    var root = $('#petRoot');
    root.style.top = Math.max(8, Math.min(global.innerHeight - 78, pos.top)) + 'px';
    root.style.bottom = 'auto';
    root.style.left = pos.right ? 'auto' : '20px';
    root.style.right = pos.right ? '20px' : 'auto';
  }

  /* ================================================================ 极简 Markdown */
  function md(text) {
    if (!text) { return ''; }
    var lines = String(text).split('\n');
    var out = [], list = null, table = null;

    function closeList() { if (list) { out.push('<' + list + '>' + listBuf.join('') + '</' + list + '>'); list = null; listBuf = []; } }
    function closeTable() {
      if (!table) { return; }
      var head = table[0], body = table.slice(1);
      out.push('<table><thead><tr>' + head.map(function (c) {
        return '<th>' + inline(c) + '</th>';
      }).join('') + '</tr></thead><tbody>' + body.map(function (r) {
        return '<tr>' + r.map(function (c) { return '<td>' + inline(c) + '</td>'; }).join('') + '</tr>';
      }).join('') + '</tbody></table>');
      table = null;
    }
    var listBuf = [];

    function inline(s) {
      return CR.esc(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    }

    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (/^\s*\|.*\|\s*$/.test(ln)) {
        var cells = ln.trim().replace(/^\||\|$/g, '').split('|').map(function (c) { return c.trim(); });
        if (cells.every(function (c) { return /^:?-{2,}:?$/.test(c); })) { continue; }
        closeList();
        if (!table) { table = []; }
        table.push(cells);
        continue;
      }
      closeTable();
      var mUl = ln.match(/^\s*[*-]\s+(.*)$/);
      var mOl = ln.match(/^\s*\d+\.\s+(.*)$/);
      if (mUl) {
        if (list !== 'ul') { closeList(); list = 'ul'; }
        listBuf.push('<li>' + inline(mUl[1]) + '</li>');
        continue;
      }
      if (mOl) {
        if (list !== 'ol') { closeList(); list = 'ol'; }
        listBuf.push('<li>' + inline(mOl[1]) + '</li>');
        continue;
      }
      closeList();
      if (/^\s*(-{3,})\s*$/.test(ln)) { out.push('<hr>'); continue; }
      if (ln.trim() === '') { continue; }
      out.push('<p>' + inline(ln) + '</p>');
    }
    closeList();
    closeTable();
    return out.join('');
  }

  /* ================================================================ 聊天 */
  function push(role, html, extra) {
    S.msgs.push({ role: role, html: html, extra: extra || null });
    render();
  }

  function render() {
    var box = $('#petBody');
    if (!box) { return; }
    box.innerHTML = S.msgs.map(function (m) {
      return '<div class="pet-msg ' + (m.role === 'me' ? 'me' : '') + '">' +
        '<span class="av">' + (m.role === 'me' ? '我' : '亦') + '</span>' +
        '<div class="bd">' + m.html +
        (m.extra && m.extra.refs && m.extra.refs.length
          ? '<div class="pet-refs">依据：' + CR.esc(m.extra.refs.join('；')) + '</div>' : '') +
        (m.extra && m.extra.actions && m.extra.actions.length
          ? '<div class="pet-acts">' + m.extra.actions.map(function (a, i) {
            return '<button class="pet-act" data-ai="' + i + '" type="button">' +
              CR.esc(a.label) + '</button>';
          }).join('') + '</div>' : '') +
        '</div></div>';
    }).join('');
    // 动作绑定
    $$('#petBody .pet-act').forEach(function (b) {
      b.addEventListener('click', function () {
        var last = null;
        for (var i = S.msgs.length - 1; i >= 0; i--) {
          if (S.msgs[i].extra && S.msgs[i].extra.actions) { last = S.msgs[i].extra.actions; break; }
        }
        var a = last && last[Number(this.dataset.ai)];
        if (!a) { return; }
        if (a.kind === 'goto') { location.href = a.target; }
        else if (a.kind === 'tour') { startTour(a.target || PAGE); }
      });
    });
    box.scrollTop = box.scrollHeight;
  }

  function renderChips() {
    var el = $('#petChips');
    if (!el) { return; }
    el.innerHTML = S.chips.map(function (c) {
      return '<span class="pet-chip" data-q="' + CR.esc(c) + '">' + CR.esc(c) + '</span>';
    }).join('');
    $$('#petChips .pet-chip').forEach(function (c) {
      c.addEventListener('click', function () { send(this.dataset.q); });
    });
  }

  function typingOn() {
    var box = $('#petBody');
    if (!box) { return; }
    var d = document.createElement('div');
    d.className = 'pet-msg'; d.id = 'petTyping';
    d.innerHTML = '<span class="av">亦</span><div class="bd">' +
      '<span class="pet-typing"><span></span><span></span><span></span></span></div>';
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }

  function typingOff() {
    var d = $('#petTyping');
    if (d) { d.remove(); }
  }

  /* 游客模式：不做接口调用，只用内置小知识库（登录页没有会话） */
  function guestAnswer(q) {
    var best = null, bestN = 0;
    GUEST_KB.forEach(function (row) {
      var n = 0;
      row[0].replace(/[？?]/g, '').split('').forEach(function (ch) {
        if (q.indexOf(ch) >= 0) { n++; }
      });
      if (n > bestN) { best = row; bestN = n; }
    });
    if (best && bestN >= 2) { return best[1]; }
    return '我还没登录，只能回答登录相关的问题。\n\n' +
      '登录后我就能带你熟悉整套流程了 —— 包括**每一步该点哪里**。\n\n' +
      '可以问我：怎么登录？三个身份有什么区别？这个系统是干什么的？';
  }

  async function send(text) {
    var q = (text || '').trim();
    if (!q || S.busy) { return; }
    var input = $('#petInput');
    if (input) { input.value = ''; }
    push('me', CR.esc(q));
    S.busy = true;
    var btn = $('#petSend');
    if (btn) { btn.disabled = true; }
    $('#petBtn').classList.add('think');
    typingOn();
    try {
      if (GUEST) {
        await new Promise(function (r) { setTimeout(r, 260); });
        typingOff();
        push('ai', md(guestAnswer(q)), { refs: ['登录页内置问答（不含业务数据）'] });
        S.chips = GUEST_CHIPS; renderChips();
      } else {
        var out = await CR.api('POST', '/api/assistant/ask', { q: q, page: PAGE });
        typingOff();
        push('ai', md(out.answer), { refs: out.refs, actions: out.actions });
        if (out.chips && out.chips.length) { S.chips = out.chips; renderChips(); }
        if (out.used_llm) { setMode('已接入模型润色'); }
      }
    } catch (e) {
      typingOff();
      push('ai', '<p>暂时问不到服务端：' + CR.esc(e.message) + '</p>' +
        '<p>你可以稍后重试，或者直接点上面的「引导」让我带你走一遍。</p>');
    } finally {
      S.busy = false;
      if (btn) { btn.disabled = false; }
      $('#petBtn').classList.remove('think');
      var i2 = $('#petInput');
      if (i2 && S.open) { i2.focus(); }
    }
  }

  function setMode(t) {
    var el = $('#petMode');
    if (el) { el.textContent = t || ''; }
  }

  async function greet() {
    if (S.msgs.length) { return; }
    if (GUEST) {
      push('ai', md('你好，我是**小亦**。\n\n还没登录，所以先只能聊登录的事。' +
        '登录后我可以在**每个页面**带你一步一步走，\n需要哪一步点哪里，我直接指给你。'), null);
      S.chips = GUEST_CHIPS; renderChips();
      setMode('未登录 · 基础问答');
      return;
    }
    var me = CR.me();
    var role = me && me.role_label ? me.role_label : '当前角色';
    push('ai', md('你好，我是**小亦**，这个系统的使用助手。\n\n' +
      '你现在是 **' + role + '**。我可以：\n' +
      '* 回答**流程 / 操作 / 权限 / 安全**类问题\n' +
      '* 查**实时数据**（现在有多少任务、某份合同有哪些风险、某条规则怎么配）\n' +
      '* 点右上角 **「引导」**，带你**一步一步**走完这个页面的主要操作\n\n' +
      '我不会编 —— 查不到就直说查不到。'), null);
    S.chips = [];
    setMode('只读 · 不参与任何判定');
    try {
      var kb = await CR.api('GET', '/api/assistant/kb');
      S.chips = kb.chips || [];
      renderChips();
      setMode('知识库 ' + kb.entries + ' 条 · 只读');
    } catch (e) { }
  }

  /* ================================================================ 引导引擎 */
  function clearTourDom() {
    ['petSpot', 'petCoach'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) { el.remove(); }
    });
    if (S.tour && S.tour._clickHandler) {
      var t = S.tour._el;
      if (t) { t.removeEventListener('click', S.tour._clickHandler, true); }
    }
  }

  function findTarget(sel) {
    var parts = String(sel).split(',');
    for (var i = 0; i < parts.length; i++) {
      var el = document.querySelector(parts[i].trim());
      if (el && el.offsetParent !== null) { return el; }
      if (el) { return el; }
    }
    return null;
  }

  async function fetchTour(page) {
    try {
      return await CR.api('GET', '/api/assistant/tour?page=' + encodeURIComponent(page));
    } catch (e) {
      return null;
    }
  }

  async function startTour(page, resumeIndex) {
    page = page || PAGE;
    if (!S.open) { open(); }
    if (page === '/login') {
      push('ai', '<p>登录页的引导很简单：右侧三个「一键进入」按钮就是三个演示身份，' +
        '点一个即可。登录后我可以在每个页面带你走。</p>');
      return;
    }
    var data = await fetchTour(page);
    if (!data || !data.available || !data.steps.length) {
      push('ai', '<p>这个页面暂时没有引导步骤。我可以带你去：' +
        CR.esc((data && data.pages ? data.pages : []).join('、')) + '</p>');
      return;
    }
    S.tour = { page: page, title: data.title, steps: data.steps, i: resumeIndex || 0 };
    close();
    renderTour();
  }

  function renderTour() {
    clearTourDom();
    var t = S.tour;
    if (!t) { return; }
    var s = t.steps[t.i];
    if (!s) { stopTour('引导完成，辛苦啦'); return; }

    var el = findTarget(s.target);
    var spot = document.createElement('div');
    spot.className = 'pet-spot';
    spot.id = 'petSpot';
    document.body.appendChild(spot);

    var coach = document.createElement('div');
    coach.className = 'pet-coach';
    coach.id = 'petCoach';
    document.body.appendChild(coach);

    if (!el) {
      spot.style.display = 'none';
      coach.innerHTML = coachHtml(t, s, '这一步的目标元素当前不在页面上 ' +
        '（可能要先完成前一步，或者需要更高的角色权限）。');
      placeCoach(coach, null);
      bindCoach(coach, s, null);
      return;
    }

    el.scrollIntoView({
      block: el.offsetHeight > global.innerHeight * 0.7 ? 'start' : 'center',
      behavior: 'smooth'
    });
    setTimeout(function () {
      spot.style.display = '';
      paintSpot(spot, el);
      placeCoach(coach, spotRect(el));
    }, 180);

    paintSpot(spot, el);
    placeCoach(coach, spotRect(el));
    coach.innerHTML = coachHtml(t, s, '');
    bindCoach(coach, s, el);

    // 需要真实点击才前进的步骤：监听一次点击
    if (s.advance === 'click' && s.allowed) {
      var h = function (ev) {
        if (ev.target.closest && ev.target.closest('#petCoach')) { return; }
        el.removeEventListener('click', h, true);
        setTimeout(function () { tourNext(); }, 420);
      };
      el.addEventListener('click', h, true);
      t._clickHandler = h; t._el = el;
    }
    // 页面滚动 / 改变窗口时跟着走
    if (!t._onScroll) {
      t._onScroll = function () {
        if (!S.tour) { return; }
        var cur = S.tour.steps[S.tour.i];
        var e2 = cur && findTarget(cur.target);
        var sp = document.getElementById('petSpot');
        var co = document.getElementById('petCoach');
        if (!e2 || !sp) { return; }
        paintSpot(sp, e2);
        if (co) { placeCoach(co, spotRect(e2)); }
      };
      global.addEventListener('resize', t._onScroll);
      document.addEventListener('scroll', t._onScroll, true);
    }
  }

  function coachHtml(t, s, warn) {
    var dots = t.steps.map(function (_x, i) {
      return '<i class="' + (i <= t.i ? 'on' : '') + '"></i>';
    }).join('');
    return '<div class="ch"><div class="pet-prog">' + dots + '</div>' +
      '<b>第 ' + s.index + ' / ' + t.steps.length + ' 步</b>' +
      '<span>' + CR.esc(t.title) + '</span></div>' +
      '<div class="cb"><b style="font-size:13.2px">' + CR.esc(s.title) + '</b><br>' +
      md(s.desc).replace(/^<p>|<\/p>$/g, '') +
      (s.advance === 'click' && s.allowed
        ? '<div class="hint">请直接点上方的按钮，我来判断你点对了没有 ↑</div>' : '') +
      (!s.allowed
        ? '<div class="hint">这一步需要「' + CR.esc(s.need_label) + '」权限，归属：' +
          CR.esc((s.need_roles || []).join('、')) + '。可以先切换身份再继续。</div>' : '') +
      (warn ? '<div class="hint">' + CR.esc(warn) + '</div>' : '') +
      '</div>' +
      '<div class="cf">' +
      '<button data-t="prev"' + (t.i === 0 ? ' disabled' : '') + '>上一步</button>' +
      '<button class="primary" data-t="next">' +
      (t.i === t.steps.length - 1 ? '完成' : '下一步') + '</button>' +
      '<div class="spacer"></div>' +
      (!s.allowed && (s.need_roles || []).length
        ? '<button data-t="switch">切换为' + CR.esc(s.need_roles[0]) + '</button>' : '') +
      '<button data-t="stop">退出</button></div>';
  }

  /* 聚光灯定位。
     关键：**目标可能比视口还高**（窄屏下统计卡片区有 900 多 px）。
     那种情况下不能整块罩住 —— 既看不见边界，气泡也没地方放。
     这里把高亮高度封顶到视口的 45%，从目标顶部开始罩，
     既指得清位置，又给气泡留出空间。 */
  function spotRect(el) {
    var r = el.getBoundingClientRect();
    var maxH = Math.max(120, global.innerHeight * 0.45);
    var top = Math.max(8, r.top);
    var h = Math.min(r.height, maxH, global.innerHeight - top - 12);
    return { top: top, left: Math.max(6, r.left), width: Math.min(r.width,
             global.innerWidth - 12), height: Math.max(48, h),
             targetTop: r.top, targetBottom: r.bottom, targetLeft: r.left,
             targetRight: r.right };
  }

  function paintSpot(spot, el) {
    var s = spotRect(el);
    spot.style.top = (s.top - 7) + 'px';
    spot.style.left = (s.left - 7) + 'px';
    spot.style.width = (s.width + 14) + 'px';
    spot.style.height = (s.height + 14) + 'px';
  }

  function placeCoach(coach, rect) {
    var vw = global.innerWidth, vh = global.innerHeight;
    var W = coach.offsetWidth || 352;
    var H = coach.offsetHeight || 240;
    var left = 16, top = 16;
    if (rect) {
      var below = rect.top + rect.height + 12 + H < vh - 10;
      top = below ? rect.top + rect.height + 12
                  : Math.max(10, rect.top - H - 12);
      if (top + H > vh - 8) { top = Math.max(8, vh - H - 10); }
      left = Math.min(vw - W - 12, Math.max(12, rect.left));
      if (vw < 700) { left = 12; }
    }
    coach.style.left = left + 'px';
    coach.style.top = top + 'px';
  }

  function bindCoach(coach, s, el) {
    $$('button', coach).forEach(function (b) {
      b.addEventListener('click', async function () {
        var t = this.dataset.t;
        if (t === 'prev') { tourPrev(); }
        else if (t === 'next') { tourNext(); }
        else if (t === 'stop') { stopTour('已退出引导'); }
        else if (t === 'switch') {
          var roleMap = { '法务审查人': 'legal', '业务经办人': 'applicant', '系统管理员': 'admin' };
          var role = roleMap[(s.need_roles || [])[0]];
          if (!role) { return; }
          clearTourDom();
          var idx = S.tour.i;
          sessionStorage.setItem(SS_TOUR, JSON.stringify({ page: S.tour.page, i: idx }));
          CR.roleSwitch(role, location.pathname + location.search);
        }
      });
    });
  }

  function tourNext() {
    if (!S.tour) { return; }
    clearTourDom();
    S.tour.i++;
    if (S.tour.i >= S.tour.steps.length) {
      var title = S.tour.title;
      var lastPage = S.tour.page;
      S.tour = null;
      sessionStorage.removeItem(SS_TOUR);
      open();
      push('ai', md('这个页面的引导走完了 —— **' + title + '**。\n\n' +
        '刚才每一步指向的按钮都是页面上真实存在的；如果哪一步没看清，' +
        '可以再点一次「引导」重来。\n\n' +
        '接下来可以去别的页面继续，比如用顶栏导航切换。'), null);
      return;
    }
    renderTour();
  }

  function tourPrev() {
    if (!S.tour || S.tour.i === 0) { return; }
    clearTourDom();
    S.tour.i--;
    renderTour();
  }

  function stopTour(msg) {
    clearTourDom();
    S.tour = null;
    sessionStorage.removeItem(SS_TOUR);
    if (msg) { push('ai', '<p>' + CR.esc(msg) + '</p>'); }
  }

  /* 跨页面续接：切换角色后回到原页，从原来的步骤继续 */
  function resumeTour() {
    var raw = sessionStorage.getItem(SS_TOUR);
    if (!raw) { return false; }
    var st;
    try { st = JSON.parse(raw); } catch (e) { return false; }
    if (!st || st.page !== PAGE) { return false; }
    sessionStorage.removeItem(SS_TOUR);
    setTimeout(async function () {
      if (!S.open) { open(); }
      await greet();
      push('ai', '<p>身份已切换，我们接着上一步继续。</p>');
      await startTour(st.page, st.i);
    }, 420);
    return true;
  }

  /* ================================================================ 开关 */
  function open() {
    if (S.open) { return; }
    S.open = true;
    $('#petPanel').classList.add('on');
    $('#petBtn').setAttribute('aria-expanded', 'true');
    var b = $('#petBadge');
    if (b) { b.classList.remove('on'); }
    try { sessionStorage.setItem(LS + 'greeted', '1'); } catch (e) { }
    greet();
    setTimeout(function () { var i = $('#petInput'); if (i) { i.focus(); } }, 60);
  }

  function close() {
    if (!S.open) { return; }
    S.open = false;
    $('#petPanel').classList.remove('on');
    $('#petBtn').setAttribute('aria-expanded', 'false');
  }

  function toggle() { if (S.open) { close(); } else { open(); } }

  /* ================================================================ 错误哨兵
     前端脚本报错最容易的表现是「页面某块空着」，肉眼很难判断是没数据还是报错了。
     这里把 window.onerror 与未处理的 Promise 异常收集到一个隐藏节点，
     元信息落进 DOM —— 既方便人工排查，也让自动化能断言「没有前端异常」。 */
  function installSentinel() {
    var box = document.createElement('span');
    box.id = 'petErrLog';
    box.hidden = true;
    box.setAttribute('aria-hidden', 'true');
    box.textContent = '';
    document.body.appendChild(box);
    var errs = [];
    function note(kind, msg, where) {
      errs.push(kind + ': ' + msg + (where ? ' @' + where : ''));
      box.textContent = errs.slice(-5).join(' || ');
      var b = document.getElementById('petBtn');
      if (b) {
        b.setAttribute('data-err', '1');
        b.title = '页面出现脚本异常，点我看看：' + msg;
        var root = document.getElementById('petRoot');
        if (root) { root.setAttribute('data-err', '1'); }
      }
    }
    global.addEventListener('error', function (ev) {
      var t = ev.target;
      if (t && t !== global && (t.tagName === 'IMG' || t.tagName === 'SCRIPT')) {
        note('资源', t.tagName + ' ' + (t.src || ''), '');
        return;
      }
      note('脚本', ev.message || 'unknown', (ev.lineno || '') + ':' + (ev.colno || ''));
    }, true);
    global.addEventListener('unhandledrejection', function (ev) {
      note('请求', String((ev.reason && ev.reason.message) || ev.reason || 'unknown'), '');
    });
  }

  /* ================================================================ 启动 */
  function boot() {
    if (!document.body) { return; }
    injectStyle();
    installSentinel();
    buildPet();
    if (resumeTour()) { return; }
    // 深链：?pet=open 直接打开对话；?pet=tour 直接开始本页引导。
    // 既方便演示时一键进引导，也让无头浏览器能验证引导真的跑起来了。
    var want = (new URLSearchParams(location.search).get('pet') || '').toLowerCase();
    if (want.indexOf('open') >= 0 || want.indexOf('tour') >= 0) {
      open();
      if (want.indexOf('tour') >= 0) {
        setTimeout(function () { startTour(PAGE); }, 600);
      }
      return;
    }
    // 首次进入**不主动弹窗**（弹窗会盖住页面内容，打扰操作）：
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
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  CR.pet = {
    open: open, close: close, toggle: toggle,
    startTour: startTour, stopTour: stopTour,
    ask: send, page: PAGE
  };
})(window);
