
(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var ROLES = [
    { role: 'legal', label: '法务审查人', user: 'legal',
      desc: '查看审查详情、核对原文高亮、采纳或调整修改建议、补充法务意见并触发回写',
      act: '可回写 · 可采纳建议 · 不可改规则' },
    { role: 'applicant', label: '业务经办人', user: 'applicant',
      desc: '提交合同审查申请、查看风险点与整改建议、下载审查报告',
      act: '可送审 · 可下载报告 · 不可回写' },
    { role: 'admin', label: '系统管理员', user: 'admin',
      desc: '配置对接参数、维护规则与标准条款库、监控任务状态并处理阻塞重试',
      act: '可改规则 · 可重试阻塞 · 可查看指标' }
  ];

  // 主题联动 logo（浅色主题用浅色版 logo）
  try {
    if (localStorage.getItem('cr-theme') === 'light') {
      $('#loginLogo').src = '/assets/logo-sm-light.png';
    }
  } catch (e) { }

  var ICONS = {
    legal: '<path d="M12 4v16"/><path d="M6 8h12"/><path d="M6 8l-3 5.5h6z"/><path d="M18 8l-3 5.5h6z"/><path d="M8.5 20h7"/>',
    applicant: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
    admin: '<rect x="4.5" y="10.5" width="15" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14.5v2"/>'
  };

  var html = ROLES.map(function (r, i) {
    return '<button class="role-item" data-role="' + r.role + '" data-user="' + r.user + '">' +
      '<span class="role-ic"><svg viewBox="0 0 24 24" width="19" height="19" fill="none" ' +
      'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
      ICONS[r.role] + '</svg></span>' +
      '<span><b>' + r.label + '（' + r.user + '）</b><span>' + r.act + '</span></span>' +
      '<span class="go">进入 →</span></button>';
  }).join('');
  $('#roleList').innerHTML = html;

  function next() {
    var n = new URLSearchParams(location.search).get('next');
    if (!n || n.indexOf('/') !== 0 || n.indexOf('//') === 0) return '/';
    return n;
  }

  function fail(msg) { $('#loginErr').textContent = msg || '登录失败'; }

  $('#loginForm').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var u = $('#u').value.trim(), p = $('#p').value;
    if (!u || !p) { fail('请输入账号与口令'); return; }
    $('#loginBtn').disabled = true;
    $('#loginErr').textContent = '';
    fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    }).then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (o) {
        $('#loginBtn').disabled = false;
        if (o.j.code !== 0) { fail(o.j.msg || '账号或口令不正确'); return; }
        location.href = next();
      }).catch(function (e) {
        $('#loginBtn').disabled = false;
        fail('网络错误：' + e.message);
      });
  });

  $('#roleList').addEventListener('click', function (ev) {
    var el = ev.target.closest ? ev.target.closest('.role-item') : null;
    if (!el) return;
    var role = el.getAttribute('data-role');
    el.style.opacity = '.6';
    fetch('/api/auth/switch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: role })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.code !== 0) { el.style.opacity = ''; fail(j.msg || '切换失败'); return; }
      location.href = next();
    }).catch(function (e) {
      el.style.opacity = '';
      fail('网络错误：' + e.message);
    });
  });

  $('#p').focus();
})();
