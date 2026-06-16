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

  // Capa de fondo (background-image) por estilo, usando los colores de orbe/acento.
  function designLayer(style, o1, o2) {
    switch (style) {
      case 'minimal':
        return 'none';
      case 'cristal':
        return 'repeating-linear-gradient(135deg, rgba(255,255,255,.05) 0 2px, transparent 2px 36px), ' +
               'radial-gradient(circle at 85% 15%, rgba(' + o1 + ',.18), transparent 55%)';
      case 'geometrico':
        return 'linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px), ' +
               'linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px), ' +
               'radial-gradient(circle at 90% 90%, rgba(' + o1 + ',.2), transparent 55%)';
      case 'neon':
        return 'linear-gradient(180deg, rgba(' + o1 + ',.22), transparent 35%), ' +
               'linear-gradient(0deg, rgba(' + o2 + ',.18), transparent 35%)';
      case 'aurora':
        return 'radial-gradient(ellipse at 15% 20%, rgba(' + o1 + ',.35), transparent 55%), ' +
               'radial-gradient(ellipse at 85% 80%, rgba(' + o2 + ',.3), transparent 55%)';
      case 'ondas':
        return 'repeating-radial-gradient(circle at 50% 120%, rgba(' + o1 + ',.16) 0, transparent 60px, rgba(' + o2 + ',.1) 90px, transparent 140px)';
      case 'cosmos':
        return 'radial-gradient(1.5px 1.5px at 20% 25%, rgba(255,255,255,.55) 1.5px, transparent 0), ' +
               'radial-gradient(1.5px 1.5px at 65% 60%, rgba(255,255,255,.4) 1.5px, transparent 0), ' +
               'radial-gradient(1.5px 1.5px at 85% 15%, rgba(255,255,255,.35) 1.5px, transparent 0), ' +
               'radial-gradient(1.5px 1.5px at 40% 85%, rgba(255,255,255,.3) 1.5px, transparent 0), ' +
               'radial-gradient(circle at 70% 90%, rgba(' + o1 + ',.2), transparent 55%)';
      case 'hexagonal':
        return 'radial-gradient(circle at 15% 80%, rgba(' + o1 + ',.22), transparent 50%), ' +
               'radial-gradient(circle at 85% 20%, rgba(' + o2 + ',.18), transparent 50%), ' +
               'linear-gradient(120deg, transparent 48%, rgba(255,255,255,.05) 50%, transparent 52%)';
      case 'lluvia':
        return 'repeating-linear-gradient(100deg, rgba(' + o1 + ',.14) 0 2px, transparent 2px 18px), ' +
               'repeating-linear-gradient(100deg, rgba(' + o2 + ',.1) 0 1px, transparent 1px 28px)';
      case 'orbos':
      default:
        return 'radial-gradient(circle at 0% 0%, rgba(' + o1 + ',.35), transparent 45%), ' +
               'radial-gradient(circle at 100% 100%, rgba(' + o2 + ',.3), transparent 45%), ' +
               'radial-gradient(circle at 1px 1px, rgba(255,255,255,.06) 1px, transparent 0)';
    }
  }

  function apply(p) {
    var accent = p.color_btn || '#2563eb';
    var accentDark = p.color_icon || darken(accent, 35);
    var bg = p.color_bg || '#0d1b2e';
    var orb1 = p.color_orb1 || accent;
    var orb2 = p.color_orb2 || '#6366f1';
    var style = p.login_style || 'orbos';
    var orb1Rgb = hexToRgbStr(orb1), orb2Rgb = hexToRgbStr(orb2);
    var root = document.documentElement.style;
    root.setProperty('--accent', accent);
    root.setProperty('--accent-dark', accentDark);
    root.setProperty('--accent-rgb', hexToRgbStr(accent));
    root.setProperty('--accent-grad', 'linear-gradient(135deg,' + accentDark + ',' + accent + ')');
    root.setProperty('--sidebar-bg', bg);
    root.setProperty('--sidebar-grad', 'linear-gradient(180deg,' + bg + ' 0%,' + accentDark + ' 60%,' + bg + ' 100%)');
    root.setProperty('--orb1', orb1);
    root.setProperty('--orb2', orb2);
    root.setProperty('--themed-bg-image', designLayer(style, orb1Rgb, orb2Rgb));
    root.setProperty('--themed-bg-size', style === 'geometrico' ? '22px 22px, 22px 22px, 100% 100%' : 'cover');
    document.documentElement.setAttribute('data-theme-style', style);
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
