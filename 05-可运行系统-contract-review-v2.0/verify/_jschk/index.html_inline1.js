
/* 主题防闪 + 入场动效安全网（不依赖 app.js，必须内联在 head 最前） */
(function () {
  try {
    var t = localStorage.getItem('cr-theme');
    if (t !== 'light' && t !== 'dark') t = 'dark';
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) { }
  document.documentElement.className += ' js';
  setTimeout(function () {
    var v = document.querySelectorAll('.reveal:not(.in)');
    for (var i = 0; i < v.length; i++) {
      var r = v[i].getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) v[i].classList.add('in');
    }
  }, 2600);
})();
