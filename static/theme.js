/* Theme toggle (STORY 14.2): system -> light -> dark. Stored in localStorage key "fm-theme".
 * The tiny inline script in index.html <head> applies the saved value before first paint. */
(function () {
  'use strict';
  var KEY = 'fm-theme';
  var ORDER = ['system', 'light', 'dark'];
  var ICONS = {
    light: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dark: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
    system: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>'
  };

  function read() {
    try {
      var v = window.localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : 'system';
    } catch (e) { return 'system'; }
  }
  function apply(mode) {
    var root = document.documentElement;
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
  }
  function save(mode) {
    try {
      if (mode === 'system') window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, mode);
    } catch (e) { /* storage blocked: theme still applies for this page view */ }
  }
  function render(btn, mode) {
    var icon = btn.querySelector('.theme-icon');
    if (icon) icon.innerHTML = ICONS[mode];
    btn.setAttribute('data-mode', mode);
    btn.setAttribute('title', mode);
  }

  var mode = read();
  apply(mode);
  function init() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    render(btn, mode);
    btn.addEventListener('click', function () {
      mode = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
      apply(mode);
      save(mode);
      render(btn, mode);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
