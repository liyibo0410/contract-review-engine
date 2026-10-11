
(function () {
  try {
    var t = localStorage.getItem('cr-theme');
    if (t !== 'light' && t !== 'dark') t = 'dark';
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) { }
})();
