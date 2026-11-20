(function () {
  try {
    var t = localStorage.getItem('happyranch.theme');
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
  } catch (e) { /* storage unavailable */ }
})();
