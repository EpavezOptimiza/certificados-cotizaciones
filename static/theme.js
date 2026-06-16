(function () {
  function getDeviceId() {
    var id = localStorage.getItem('optimiza_device_id');
    if (!id) {
      id = 'dev_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem('optimiza_device_id', id);
      document.cookie = 'device_id=' + id + ';max-age=' + (86400 * 365) + ';path=/';
    }
    return id;
  }

  function darken(hex, amt) {
    try {
      var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
      return 'rgb(' + Math.max(0, r - amt) + ',' + Math.max(0, g - amt) + ',' + Math.max(0, b - amt) + ')';
    } catch (e) { return hex; }
  }

  function hexToRgbStr(hex) {
    try {
      var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
      return r + ',' + g + ',' + b;
    } catch (e) { return '37,99,235'; }
  }

  function apply(p) {
    var accent = p.color_btn || '#2563eb';
    var accentDark = p.color_icon || darken(accent, 35);
    var bg = p.color_bg || '#0d1b2e';
    var orb1 = p.color_orb1 || accent;
    var orb2 = p.color_orb2 || '#6366f1';
    var root = document.documentElement.style;
    root.setProperty('--accent', accent);
    root.setProperty('--accent-dark', accentDark);
    root.setProperty('--accent-rgb', hexToRgbStr(accent));
    root.setProperty('--accent-grad', 'linear-gradient(135deg,' + accentDark + ',' + accent + ')');
    root.setProperty('--sidebar-bg', bg);
    root.setProperty('--sidebar-grad', 'linear-gradient(180deg,' + bg + ' 0%,' + accentDark + ' 60%,' + bg + ' 100%)');
    root.setProperty('--orb1', orb1);
    root.setProperty('--orb2', orb2);
  }

  try {
    var cached = JSON.parse(localStorage.getItem('optimiza_login_prefs') || 'null');
    if (cached) apply(cached);
  } catch (e) {}

  fetch('/api/device_prefs?device_id=' + encodeURIComponent(getDeviceId()))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.ok && d.prefs) {
        apply(d.prefs);
        localStorage.setItem('optimiza_login_prefs', JSON.stringify(d.prefs));
      }
    })
    .catch(function () {});
})();
