
// 演示辅助：受限页上也给一键切换到有权限的角色，避免现场退回登录页重新输口令
(function () {
  var allow = '{{ALLOW_ROLES}}'.split(',').filter(Boolean);
  if (!allow.length) return;
  if (!(new URLSearchParams(location.search).get('next'))) {
    var a = document.querySelector('a[href="/login?next=/rules"]');
    if (a) a.href = '/login?next=' + encodeURIComponent(location.pathname);
  }
  addEventListener('load', function () {
    var box = document.querySelector('.deny-actions');
    var path = location.pathname + location.search;
    allow.forEach(function (role) {
      var b = document.createElement('button');
      b.className = 'btn ghost';
      b.textContent = '切换为 ' + ({ legal: '法务审查人', applicant: '业务经办人', admin: '系统管理员' }[role] || role);
      b.onclick = function () {
        b.disabled = true;
        fetch('/api/auth/switch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: role })
        }).then(function (r) { return r.json(); }).then(function (j) {
          if (j.code === 0) location.href = path;
          else { b.disabled = false; alert(j.msg || '切换失败'); }
        });
      };
      box.appendChild(b);
    });
  });
})();
