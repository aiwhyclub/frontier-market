/* Public demo layer (EPIC 13). Loaded before app.js.
 * Asks GET /api/demo/status which mode the server is in:
 *   404 (route absent)  -> local mode: this file does nothing else, the app behaves as before.
 *   200                 -> public mode: anonymous visitors get a read-only view of the synthetic data
 *                          (only /api/demo/*) plus per-visitor task run/reset; operator-only actions
 *                          are not rendered. An operator can log in with the token (/api/auth/login) and
 *                          then uses the full UI; mutating calls carry the session-bound X-CSRF-Token.
 *   410 `closed`        -> end notice for visitors (operators may still log in).
 * app.js waits for window.FMBoot before the first render. */
(function () {
  'use strict';

  var KEYS = ['dashboard', 's1', 's2', 's3', 's4', 's5', 's6', 's7'];
  var ENTITY = { s1: 'sources', s3: 'experts', s4: 'catalogs', s5: 'works', s6: 'projects' };
  var ENTITY_LABEL = FMI18n.lazy(function () { return { sources: t('collect.materials'), experts: t('app.experts'), catalogs: t('catalog.task_card'), works: t('demo.work'), projects: t('demo.project'), companies: t('app.virtual_company') }; });
  var ENTITY_STAGE = { sources: 's1', experts: 's3', catalogs: 's4', works: 's5', projects: 's6', companies: 's7' };
  var STATE_LABEL = FMI18n.lazy(function () { return { pending_review: t('demo.awaiting_review'), inspected: t('company.inspected'), held: t('catalog.on_hold'), released: t('company.release') }; });
  var CHECK_LABEL = FMI18n.lazy(function () { return { answer_present: t('demo.answer_present'), provenance_linked: t('demo.provenance_linked'), rights_ok: t('demo.rights_confirmed'), reviewer_ok: t('demo.reviewer_approved') }; });

  var mode = 'local'; // local | operator | visitor | closed
  var visitorCsrf = null;
  var operatorCsrf = null;
  var publicEnd = null;
  var bootstrapData = null;

  window.FMScreens = window.FMScreens || {};

  function esc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function domReady() {
    return new Promise(function (resolve) {
      if (document.readyState === 'complete') { resolve(); return; }
      document.addEventListener('DOMContentLoaded', function () { resolve(); });
    });
  }

  function getJSON(path) {
    return fetch(path, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) { return { status: res.status, body: body }; });
      });
  }

  function postJSON(path, body, headers) {
    var h = { 'Content-Type': 'application/json', Accept: 'application/json' };
    Object.keys(headers || {}).forEach(function (k) { h[k] = headers[k]; });
    return fetch(path, { method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: h, body: JSON.stringify(body) })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (b) { return { status: res.status, body: b }; });
      });
  }

  function formatEnd() {
    if (!publicEnd) { return '2026-10-24 21:00 (KST)'; }
    var d = new Date(publicEnd);
    if (isNaN(d.getTime())) { return '2026-10-24 21:00 (KST)'; }
    var k = new Date(d.getTime() + 9 * 3600 * 1000);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return k.getUTCFullYear() + '-' + p(k.getUTCMonth() + 1) + '-' + p(k.getUTCDate()) + ' ' +
      p(k.getUTCHours()) + ':' + p(k.getUTCMinutes()) + ' (KST)';
  }

  /* ---- operator: CSRF header on every same-origin mutating operator API call ---- */
  function installOperatorFetch() {
    var original = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = String(init.method || (input && input.method) || 'GET').toUpperCase();
      var path = url.replace(/^https?:\/\/[^/]+/, '');
      if (operatorCsrf && method !== 'GET' && method !== 'HEAD' && path.indexOf('/api/') === 0 &&
          path.indexOf('/api/demo/') !== 0 && path.indexOf('/api/auth/login') !== 0) {
        var headers = new Headers(init.headers || (typeof input !== 'string' && input && input.headers) || {});
        headers.set('X-CSRF-Token', operatorCsrf);
        init = Object.assign({}, init, { headers: headers, credentials: init.credentials || 'same-origin' });
      }
      return original.call(window, input, init);
    };
  }

  /* ---- topbar: login / logout entry ---- */
  function topbarActions() {
    var bar = document.querySelector('.topbar');
    if (!bar || bar.querySelector('.fm-demo-bar')) { return; }
    var box = document.createElement('div');
    box.className = 'fm-demo-bar';
    if (mode === 'operator') {
      box.innerHTML = '<span class="fm-demo-tag">' + t('demo.operator_mode') + '</span>' +
        '<button type="button" class="btn btn-secondary" id="fm-op-logout">' + t('demo.operator_log_out') + '</button>';
    } else {
      box.innerHTML = '<span class="fm-demo-tag">' + (mode === 'closed' ? t('demo.demo_ended') : t('demo.read_only_public_demo')) + '</span>' +
        '<button type="button" class="btn btn-secondary" id="fm-op-login">' + t('demo.operator_login') + '</button>';
    }
    bar.appendChild(box);
    var login = box.querySelector('#fm-op-login');
    if (login) { login.addEventListener('click', openLogin); }
    var logout = box.querySelector('#fm-op-logout');
    if (logout) {
      logout.addEventListener('click', function () {
        postJSON('/api/auth/logout', {}, { 'X-CSRF-Token': operatorCsrf || '' }).then(function () { window.location.reload(); });
      });
    }
  }

  function openLogin() {
    var root = document.getElementById('dialog-root') || document.body;
    var opener = document.activeElement;
    var wrap = document.createElement('div');
    wrap.className = 'fm-demo-overlay';
    wrap.innerHTML =
      '<div class="fm-demo-dialog" role="dialog" aria-modal="true" aria-labelledby="fm-login-title">' +
        '<h2 id="fm-login-title">' + t('demo.operator_login') + '</h2>' +
        '<p class="fm-demo-muted">' + t('demo.enter_the_operator_token_to') + '</p>' +
        '<form id="fm-login-form" novalidate>' +
          '<label for="fm-login-token">' + t('demo.operator_token') + '</label>' +
          '<input id="fm-login-token" class="fm-demo-input" type="password" autocomplete="off" required>' +
          '<p class="fm-demo-error" id="fm-login-error" role="alert" hidden></p>' +
          '<div class="fm-demo-actions">' +
            '<button type="submit" class="btn btn-primary">' + t('demo.log_in') + '</button>' +
            '<button type="button" class="btn btn-secondary" id="fm-login-cancel">' + t('demo.cancel') + '</button>' +
          '</div>' +
        '</form>' +
      '</div>';
    root.appendChild(wrap);
    var input = wrap.querySelector('#fm-login-token');
    var error = wrap.querySelector('#fm-login-error');
    function close() {
      if (wrap.parentNode) { wrap.parentNode.removeChild(wrap); }
      if (opener && opener.focus) { opener.focus(); }
    }
    wrap.querySelector('#fm-login-cancel').addEventListener('click', close);
    wrap.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); } });
    wrap.querySelector('#fm-login-form').addEventListener('submit', function (e) {
      e.preventDefault();
      if (!input.value) { error.textContent = t('demo.enter_the_token'); error.hidden = false; input.focus(); return; }
      postJSON('/api/auth/login', { token: input.value }).then(function (r) {
        if (r.status === 200) { window.location.reload(); return; }
        error.textContent = r.status === 401 ? t('demo.authentication_failed_check_the_token') : t('demo.could_not_log_in_please');
        error.hidden = false;
        input.value = '';
        input.focus();
      }, function () {
        error.textContent = t('demo.could_not_connect_to_the'); error.hidden = false;
      });
    });
    input.focus();
  }

  /* ---- visitor screens (read-only) ---- */
  function notice() {
    return '<section class="fm-demo-notice" role="note"><strong>' + t('demo.read_only_public_demo') + '</strong>' +
      '<p>' + t('demo.all_materials_are_synthetic_you') + '</p></section>';
  }

  function closedHtml() {
    return '<section class="fm-demo-notice fm-demo-closed" role="alert"><strong>' + t('demo.the_public_demo_has_ended') + '</strong>' +
      '<p>' + t('demo.after_that_time_the_public') + '</p></section>';
  }

  function errorHtml(message) {
    return '<p class="fm-demo-error" role="alert">' + esc(message) + '</p>';
  }

  function handleGone(container, res) {
    if (res.status === 410) {
      mode = 'closed';
      container.innerHTML = closedHtml();
      return true;
    }
    return false;
  }

  function dashboardScreen(container) {
    var counts = (bootstrapData && bootstrapData.counts) || {};
    var cards = Object.keys(ENTITY_STAGE).map(function (name) {
      return '<li><a class="stage-card" href="#' + ENTITY_STAGE[name] + '"><span class="stage-code">' + esc(ENTITY_STAGE[name].toUpperCase()) + '</span>' +
        '<span class="stage-name">' + esc(ENTITY_LABEL[name]) + '</span>' +
        '<span class="stage-desc">' + t('catalog.synthetic') + ' ' + esc(counts[name] == null ? 0 : counts[name]) + t('demo.records_viewable') + '</span></a></li>';
    }).join('');
    container.innerHTML = notice() +
      '<section aria-labelledby="demo-stage-heading"><h2 class="section-title" id="demo-stage-heading">' + t('demo.browse') + '</h2>' +
      '<ul class="stage-grid">' + cards + '</ul></section>';
  }

  function listScreen(container, entity) {
    container.innerHTML = notice() + '<p class="loading">' + t('demo.loading') + '</p>';
    getJSON('/api/demo/' + entity).then(function (res) {
      if (handleGone(container, res)) { return; }
      if (res.status !== 200) { container.innerHTML = notice() + errorHtml(t('catalog.could_not_load_the_list')); return; }
      var items = res.body.items || [];
      if (!items.length) {
        container.innerHTML = notice() + '<section class="empty-state"><h2>' + t('demo.no_public_synthetic_items') + '</h2><p>' + t('demo.there_is_no_demo_synthetic') + '</p></section>';
        return;
      }
      container.innerHTML = notice() + '<ul class="fm-demo-list">' + items.map(function (it) {
        return '<li class="fm-demo-card"><div class="fm-demo-card-head"><strong>' + esc(it.title || it.id) + '</strong>' +
          '<span class="fm-demo-tag">' + esc(it.status || '') + '</span></div>' +
          '<button type="button" class="btn btn-secondary" data-demo-open="' + esc(it.id) + '" aria-expanded="false">' + t('demo.view_details') + '</button>' +
          '<div class="fm-demo-detail" hidden></div></li>';
      }).join('') + '</ul>';
      container.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-demo-open]') : null;
        if (!btn) { return; }
        var box = btn.parentNode.querySelector('.fm-demo-detail');
        if (!box.hidden) { box.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.textContent = t('demo.view_details'); return; }
        box.hidden = false; box.textContent = t('demo.loading');
        btn.setAttribute('aria-expanded', 'true'); btn.textContent = t('demo.collapse');
        getJSON('/api/demo/' + entity + '/' + encodeURIComponent(btn.getAttribute('data-demo-open'))).then(function (r) {
          if (r.status === 410) { handleGone(container, r); return; }
          if (r.status !== 200) { box.innerHTML = errorHtml(t('demo.could_not_load_the_details')); return; }
          var it = r.body.item || {};
          var body = it.body;
          var text = typeof body === 'string' ? body : (body ? JSON.stringify(body, null, 2) : '');
          box.innerHTML = '<pre class="fm-demo-body">' + esc(text || t('demo.no_text')) + '</pre>' +
            '<p class="fm-demo-muted">' + t('collect.version') + ' ' + esc(it.version == null ? '-' : it.version) + ' ' + t('demo.synthetic_material') + '</p>';
        });
      });
    }, function () { container.innerHTML = notice() + errorHtml(t('demo.could_not_connect_to_the')); });
  }

  function dbScreen(container) {
    var counts = (bootstrapData && bootstrapData.counts) || {};
    var rows = Object.keys(ENTITY_STAGE).map(function (name) {
      return '<li class="fm-demo-card"><div class="fm-demo-card-head"><strong>' + esc(ENTITY_LABEL[name]) + '</strong>' +
        '<span class="fm-demo-tag">' + t('catalog.synthetic') + ' ' + esc(counts[name] == null ? 0 : counts[name]) + t('collect.items') + '</span></div>' +
        '<a class="btn btn-secondary" href="#' + ENTITY_STAGE[name] + '">' + esc(t('demo.view_entity', { entity: ENTITY_LABEL[name] })) + '</a></li>';
    }).join('');
    container.innerHTML = notice() + '<ul class="fm-demo-list">' + rows + '</ul>';
  }

  function newKey() {
    if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
    return 'k' + Date.now() + Math.random().toString(16).slice(2);
  }

  function taskHtml(t, hold) {
    var run = t.run || {};
    var dis = hold ? ' disabled' : '';
    var releaseDis = (hold || !run.inspection_complete || run.state === 'released') ? ' disabled' : '';
    var hist = (t.history || []).map(function (h) {
      return '<li>' + t('company.attempt') + ' ' + esc(h.attempt) + ' · ' + esc(h.event) + ' → ' + esc(STATE_LABEL[h.to_state] || h.to_state) +
        (h.score == null ? '' : ' ' + t('company.score') + ' ' + esc(h.score) + '/4') + '</li>';
    }).join('');
    var crit = (t.criteria || []).map(function (c) { return '<li>' + esc(c.label || c.id || c) + '</li>'; }).join('');
    return '<li class="fm-demo-card" data-task="' + esc(t.task_id) + '" data-version="' + esc(run.run_version) + '">' +
      '<div class="fm-demo-card-head"><strong>' + esc(t.title) + '</strong><span class="fm-demo-tag">' + esc(STATE_LABEL[run.state] || run.state) + '</span></div>' +
      '<p class="fm-demo-muted">' + t('company.attempt') + ' ' + esc(run.attempt) + ' ' + t('demo.run_version') + ' ' + esc(run.run_version) +
        (run.score == null ? '' : ' ' + t('demo.inspection_score') + ' ' + esc(run.score) + '/4') + ' ' + t('demo.this_record_is_stored_only') + '</p>' +
      (crit ? '<ul class="fm-demo-crit">' + crit + '</ul>' : '') +
      '<div class="fm-demo-actions">' +
        '<button type="button" class="btn btn-primary" data-act="request_inspection"' + dis + '>' + t('company.request_inspection') + '</button>' +
        '<button type="button" class="btn btn-secondary" data-act="hold"' + dis + '>' + t('catalog.on_hold') + '</button>' +
        '<button type="button" class="btn btn-secondary" data-act="release"' + releaseDis + '>' + t('company.release') + '</button>' +
        '<button type="button" class="btn btn-secondary" data-act="reset"' + dis + '>' + t('demo.reset') + '</button>' +
      '</div>' +
      '<p class="fm-demo-status" role="status" aria-live="polite"></p>' +
      (hist ? '<details><summary>' + t('demo.my_attempt_history') + '</summary><ul>' + hist + '</ul></details>' : '') + '</li>';
  }

  function companyDetail(container, cid) {
    container.innerHTML = notice() + '<p class="loading">' + t('demo.loading') + '</p>';
    getJSON('/api/demo/companies/' + encodeURIComponent(cid)).then(function (res) {
      if (handleGone(container, res)) { return; }
      if (res.status !== 200) { container.innerHTML = notice() + errorHtml(t('demo.could_not_load_the_virtual')); return; }
      var v = res.body.item;
      var holdHtml = v.hold ? '<p class="fm-demo-error" role="alert">' + t('demo.rights_or_review_conditions_changed') + '</p>' : '';
      container.innerHTML = notice() +
        '<p><a href="#s7" class="btn btn-secondary" id="demo-back">' + t('collect.back_to_list') + '</a></p>' +
        '<h2 class="section-title">' + esc(v.title) + '</h2>' +
        (v.purpose ? '<p class="fm-demo-muted">' + t('company.purpose') + ' ' + esc(v.purpose) + '</p>' : '') + holdHtml +
        (v.tasks.length ? '<ul class="fm-demo-list">' + v.tasks.map(function (t) { return taskHtml(t, v.hold); }).join('') + '</ul>'
          : '<section class="empty-state"><h2>' + t('demo.no_tasks_yet') + '</h2><p>' + t('demo.this_company_has_no_tasks') + '</p></section>');
      container.querySelector('#demo-back').addEventListener('click', function (e) { e.preventDefault(); companiesScreen(container); });
      container.onclick = function (e) {
        var btn = e.target.closest ? e.target.closest('[data-act]') : null;
        if (!btn || btn.disabled) { return; }
        var card = btn.closest('[data-task]');
        runAction(container, cid, card, btn.getAttribute('data-act'));
      };
    }, function () { container.innerHTML = notice() + errorHtml(t('demo.could_not_connect_to_the')); });
  }

  function runAction(container, cid, card, act, retried) {
    var status = card.querySelector('.fm-demo-status');
    var buttons = card.querySelectorAll('[data-act]');
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    status.textContent = t('demo.working');
    var body = { task_id: card.getAttribute('data-task'), expected_run_version: parseInt(card.getAttribute('data-version'), 10) || 0 };
    var path = '/api/demo/companies/' + encodeURIComponent(cid) + (act === 'reset' ? '/reset' : '/run');
    if (act !== 'reset') { body.action = act; }
    postJSON(path, body, { 'X-CSRF-Token': visitorCsrf || '', 'Idempotency-Key': newKey() }).then(function (r) {
      if (r.status === 410) { handleGone(container, r); return; }
      if ((r.status === 401 || r.status === 403) && !retried) {
        // Session expired: get a fresh one once and retry.
        return getJSON('/api/demo/bootstrap').then(function (b) {
          if (b.status === 200) { visitorCsrf = b.body.csrf_token; }
          return runAction(container, cid, card, act, true);
        });
      }
      if (r.status === 200) { companyDetail(container, cid); return; }
      var msg = (r.body && r.body.error && r.body.error.message) || t('demo.could_not_process_it');
      if (r.status === 409 && r.body.error && r.body.error.code === 'version_conflict') {
        msg += ' ' + t('demo.reloading_the_screen');
        window.setTimeout(function () { companyDetail(container, cid); }, 1200);
      }
      status.textContent = msg;
      Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
    }, function () {
      status.textContent = t('demo.could_not_connect_to_the');
      Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
    });
  }

  function companiesScreen(container) {
    container.onclick = null;
    var list = (bootstrapData && bootstrapData.companies) || [];
    if (!list.length) {
      container.innerHTML = notice() + '<section class="empty-state"><h2>' + t('demo.no_public_virtual_companies') + '</h2><p>' + t('demo.no_synthetic_demo_virtual_company') + '</p></section>';
      return;
    }
    container.innerHTML = notice() + '<ul class="fm-demo-list">' + list.map(function (c) {
      return '<li class="fm-demo-card"><div class="fm-demo-card-head"><strong>' + esc(c.title) + '</strong><span class="fm-demo-tag">' + t('catalog.synthetic') + '</span></div>' +
        (c.purpose ? '<p class="fm-demo-muted">' + t('company.purpose') + ' ' + esc(c.purpose) + '</p>' : '') +
        '<button type="button" class="btn btn-primary" data-company="' + esc(c.id) + '">' + t('demo.view_and_run_tasks') + '</button></li>';
    }).join('') + '</ul>';
    container.onclick = function (e) {
      var btn = e.target.closest ? e.target.closest('[data-company]') : null;
      if (btn) { companyDetail(container, btn.getAttribute('data-company')); }
    };
  }

  function installScreens() {
    var map = {};
    if (mode === 'closed') {
      KEYS.forEach(function (k) { map[k] = function (c) { c.innerHTML = closedHtml(); }; });
    } else {
      map.dashboard = dashboardScreen;
      map.s2 = dbScreen;
      map.s7 = companiesScreen;
      Object.keys(ENTITY).forEach(function (k) { map[k] = function (c) { listScreen(c, ENTITY[k]); }; });
    }
    KEYS.forEach(function (k) { window.FMScreens[k] = map[k]; });
  }

  /* ---- boot ---- */
  function boot() {
    var ready = domReady();
    var decided = getJSON('/api/demo/status').then(function (res) {
      if (res.status === 200 && res.body && res.body.public) {
        publicEnd = res.body.public_end;
        return 'public';
      }
      if (res.status === 410) { return 'closed'; }
      return 'local';
    }, function () { return 'local'; }).then(function (kind) {
      if (kind === 'local') { return; }
      return getJSON('/api/auth/session').then(function (s) {
        if (s.status === 200 && s.body && s.body.csrf_token) {
          mode = 'operator';
          operatorCsrf = s.body.csrf_token;
          installOperatorFetch();
          return;
        }
        if (kind === 'closed') { mode = 'closed'; return; }
        mode = 'visitor';
        return getJSON('/api/demo/bootstrap').then(function (b) {
          if (b.status === 410) { mode = 'closed'; return; }
          if (b.status === 200) {
            bootstrapData = b.body;
            visitorCsrf = b.body.csrf_token;
            publicEnd = b.body.public_end || publicEnd;
          }
        });
      }, function () { mode = kind === 'closed' ? 'closed' : 'visitor'; });
    });
    return Promise.all([ready, decided]).then(function () {
      if (mode === 'local') { return; }
      document.documentElement.setAttribute('data-fm-mode', mode);
      if (mode === 'visitor' || mode === 'closed') { installScreens(); }
      topbarActions();
    });
  }

  window.FMBoot = boot();
  window.FMDemo = { mode: function () { return mode; } };
})();
