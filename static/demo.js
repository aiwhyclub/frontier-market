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
  var ENTITY_LABEL = { sources: '자료', experts: '전문가', catalogs: '과제 카드', works: '작업', projects: '프로젝트', companies: '가상회사' };
  var ENTITY_STAGE = { sources: 's1', experts: 's3', catalogs: 's4', works: 's5', projects: 's6', companies: 's7' };
  var STATE_LABEL = { pending_review: '검토 대기', inspected: '검사 완료', held: '보류', released: '해제' };
  var CHECK_LABEL = { answer_present: '답안 존재', provenance_linked: '출처 연결', rights_ok: '권리 확인', reviewer_ok: '검토자 승인' };

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
      box.innerHTML = '<span class="fm-demo-tag">운영자 모드</span>' +
        '<button type="button" class="btn btn-secondary" id="fm-op-logout">운영자 로그아웃</button>';
    } else {
      box.innerHTML = '<span class="fm-demo-tag">' + (mode === 'closed' ? '시연 종료' : '읽기 전용 공개 시연') + '</span>' +
        '<button type="button" class="btn btn-secondary" id="fm-op-login">운영자 로그인</button>';
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
        '<h2 id="fm-login-title">운영자 로그인</h2>' +
        '<p class="fm-demo-muted">운영자 토큰을 입력하면 등록·수정·권리 확인·승인 등 전체 기능을 사용할 수 있습니다.</p>' +
        '<form id="fm-login-form" novalidate>' +
          '<label for="fm-login-token">운영자 토큰</label>' +
          '<input id="fm-login-token" class="fm-demo-input" type="password" autocomplete="off" required>' +
          '<p class="fm-demo-error" id="fm-login-error" role="alert" hidden></p>' +
          '<div class="fm-demo-actions">' +
            '<button type="submit" class="btn btn-primary">로그인</button>' +
            '<button type="button" class="btn btn-secondary" id="fm-login-cancel">취소</button>' +
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
      if (!input.value) { error.textContent = '토큰을 입력하세요.'; error.hidden = false; input.focus(); return; }
      postJSON('/api/auth/login', { token: input.value }).then(function (r) {
        if (r.status === 200) { window.location.reload(); return; }
        error.textContent = r.status === 401 ? '인증에 실패했습니다. 토큰을 확인하세요.' : '로그인하지 못했습니다. 잠시 후 다시 시도하세요.';
        error.hidden = false;
        input.value = '';
        input.focus();
      }, function () {
        error.textContent = '서버에 연결하지 못했습니다.'; error.hidden = false;
      });
    });
    input.focus();
  }

  /* ---- visitor screens (read-only) ---- */
  function notice() {
    return '<section class="fm-demo-notice" role="note"><strong>읽기 전용 공개 시연</strong>' +
      '<p>모든 자료는 합성 가공 자료입니다. 조회와 가상회사 과제 실행·초기화만 할 수 있고, 등록·수정·권리 확인·승인·내보내기는 운영자 전용입니다. ' +
      '이 시연은 ' + esc(formatEnd()) + '에 종료됩니다.</p></section>';
  }

  function closedHtml() {
    return '<section class="fm-demo-notice fm-demo-closed" role="alert"><strong>공개 시연이 종료되었습니다</strong>' +
      '<p>' + esc(formatEnd()) + ' 이후에는 공개 화면과 자료를 열 수 없습니다. 관람해 주셔서 감사합니다. 운영자는 위의 로그인으로 계속 사용할 수 있습니다.</p></section>';
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
        '<span class="stage-desc">합성 ' + esc(counts[name] == null ? 0 : counts[name]) + '건 조회</span></a></li>';
    }).join('');
    container.innerHTML = notice() +
      '<section aria-labelledby="demo-stage-heading"><h2 class="section-title" id="demo-stage-heading">둘러보기</h2>' +
      '<ul class="stage-grid">' + cards + '</ul></section>';
  }

  function listScreen(container, entity) {
    container.innerHTML = notice() + '<p class="loading">불러오는 중…</p>';
    getJSON('/api/demo/' + entity).then(function (res) {
      if (handleGone(container, res)) { return; }
      if (res.status !== 200) { container.innerHTML = notice() + errorHtml('목록을 불러오지 못했습니다.'); return; }
      var items = res.body.items || [];
      if (!items.length) {
        container.innerHTML = notice() + '<section class="empty-state"><h2>공개된 합성 항목이 없습니다</h2><p>이 단계에는 아직 시연용 합성 자료가 없습니다.</p></section>';
        return;
      }
      container.innerHTML = notice() + '<ul class="fm-demo-list">' + items.map(function (it) {
        return '<li class="fm-demo-card"><div class="fm-demo-card-head"><strong>' + esc(it.title || it.id) + '</strong>' +
          '<span class="fm-demo-tag">' + esc(it.status || '') + '</span></div>' +
          '<button type="button" class="btn btn-secondary" data-demo-open="' + esc(it.id) + '" aria-expanded="false">상세 보기</button>' +
          '<div class="fm-demo-detail" hidden></div></li>';
      }).join('') + '</ul>';
      container.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-demo-open]') : null;
        if (!btn) { return; }
        var box = btn.parentNode.querySelector('.fm-demo-detail');
        if (!box.hidden) { box.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.textContent = '상세 보기'; return; }
        box.hidden = false; box.textContent = '불러오는 중…';
        btn.setAttribute('aria-expanded', 'true'); btn.textContent = '접기';
        getJSON('/api/demo/' + entity + '/' + encodeURIComponent(btn.getAttribute('data-demo-open'))).then(function (r) {
          if (r.status === 410) { handleGone(container, r); return; }
          if (r.status !== 200) { box.innerHTML = errorHtml('상세를 불러오지 못했습니다.'); return; }
          var it = r.body.item || {};
          var body = it.body;
          var text = typeof body === 'string' ? body : (body ? JSON.stringify(body, null, 2) : '');
          box.innerHTML = '<pre class="fm-demo-body">' + esc(text || '본문이 없습니다.') + '</pre>' +
            '<p class="fm-demo-muted">버전 ' + esc(it.version == null ? '-' : it.version) + ' · 합성 자료</p>';
        });
      });
    }, function () { container.innerHTML = notice() + errorHtml('서버에 연결하지 못했습니다.'); });
  }

  function dbScreen(container) {
    var counts = (bootstrapData && bootstrapData.counts) || {};
    var rows = Object.keys(ENTITY_STAGE).map(function (name) {
      return '<li class="fm-demo-card"><div class="fm-demo-card-head"><strong>' + esc(ENTITY_LABEL[name]) + '</strong>' +
        '<span class="fm-demo-tag">합성 ' + esc(counts[name] == null ? 0 : counts[name]) + '건</span></div>' +
        '<a class="btn btn-secondary" href="#' + ENTITY_STAGE[name] + '">' + esc(ENTITY_LABEL[name]) + ' 보기</a></li>';
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
      return '<li>시도 ' + esc(h.attempt) + ' · ' + esc(h.event) + ' → ' + esc(STATE_LABEL[h.to_state] || h.to_state) +
        (h.score == null ? '' : ' · 점수 ' + esc(h.score) + '/4') + '</li>';
    }).join('');
    var crit = (t.criteria || []).map(function (c) { return '<li>' + esc(c.label || c.id || c) + '</li>'; }).join('');
    return '<li class="fm-demo-card" data-task="' + esc(t.task_id) + '" data-version="' + esc(run.run_version) + '">' +
      '<div class="fm-demo-card-head"><strong>' + esc(t.title) + '</strong><span class="fm-demo-tag">' + esc(STATE_LABEL[run.state] || run.state) + '</span></div>' +
      '<p class="fm-demo-muted">시도 ' + esc(run.attempt) + ' · 실행 버전 ' + esc(run.run_version) +
        (run.score == null ? '' : ' · 검사 점수 ' + esc(run.score) + '/4') + ' · 이 기록은 내 브라우저 세션에만 저장됩니다.</p>' +
      (crit ? '<ul class="fm-demo-crit">' + crit + '</ul>' : '') +
      '<div class="fm-demo-actions">' +
        '<button type="button" class="btn btn-primary" data-act="request_inspection"' + dis + '>검사 요청</button>' +
        '<button type="button" class="btn btn-secondary" data-act="hold"' + dis + '>보류</button>' +
        '<button type="button" class="btn btn-secondary" data-act="release"' + releaseDis + '>해제</button>' +
        '<button type="button" class="btn btn-secondary" data-act="reset"' + dis + '>초기화</button>' +
      '</div>' +
      '<p class="fm-demo-status" role="status" aria-live="polite"></p>' +
      (hist ? '<details><summary>내 시도 기록</summary><ul>' + hist + '</ul></details>' : '') + '</li>';
  }

  function companyDetail(container, cid) {
    container.innerHTML = notice() + '<p class="loading">불러오는 중…</p>';
    getJSON('/api/demo/companies/' + encodeURIComponent(cid)).then(function (res) {
      if (handleGone(container, res)) { return; }
      if (res.status !== 200) { container.innerHTML = notice() + errorHtml('가상회사를 불러오지 못했습니다.'); return; }
      var v = res.body.item;
      var holdHtml = v.hold ? '<p class="fm-demo-error" role="alert">권리 또는 검토 조건이 바뀌어 이 시연은 보류되었습니다. 실행과 초기화를 할 수 없습니다.</p>' : '';
      container.innerHTML = notice() +
        '<p><a href="#s7" class="btn btn-secondary" id="demo-back">목록으로</a></p>' +
        '<h2 class="section-title">' + esc(v.title) + '</h2>' +
        (v.purpose ? '<p class="fm-demo-muted">목적: ' + esc(v.purpose) + '</p>' : '') + holdHtml +
        (v.tasks.length ? '<ul class="fm-demo-list">' + v.tasks.map(function (t) { return taskHtml(t, v.hold); }).join('') + '</ul>'
          : '<section class="empty-state"><h2>과제가 아직 없습니다</h2><p>이 회사에는 실행할 과제가 없습니다.</p></section>');
      container.querySelector('#demo-back').addEventListener('click', function (e) { e.preventDefault(); companiesScreen(container); });
      container.onclick = function (e) {
        var btn = e.target.closest ? e.target.closest('[data-act]') : null;
        if (!btn || btn.disabled) { return; }
        var card = btn.closest('[data-task]');
        runAction(container, cid, card, btn.getAttribute('data-act'));
      };
    }, function () { container.innerHTML = notice() + errorHtml('서버에 연결하지 못했습니다.'); });
  }

  function runAction(container, cid, card, act, retried) {
    var status = card.querySelector('.fm-demo-status');
    var buttons = card.querySelectorAll('[data-act]');
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    status.textContent = '처리 중…';
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
      var msg = (r.body && r.body.error && r.body.error.message) || '처리하지 못했습니다.';
      if (r.status === 409 && r.body.error && r.body.error.code === 'version_conflict') {
        msg += ' 화면을 새로 불러옵니다.';
        window.setTimeout(function () { companyDetail(container, cid); }, 1200);
      }
      status.textContent = msg;
      Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
    }, function () {
      status.textContent = '서버에 연결하지 못했습니다.';
      Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
    });
  }

  function companiesScreen(container) {
    container.onclick = null;
    var list = (bootstrapData && bootstrapData.companies) || [];
    if (!list.length) {
      container.innerHTML = notice() + '<section class="empty-state"><h2>공개된 가상회사가 없습니다</h2><p>시연용 합성 가상회사가 아직 만들어지지 않았습니다.</p></section>';
      return;
    }
    container.innerHTML = notice() + '<ul class="fm-demo-list">' + list.map(function (c) {
      return '<li class="fm-demo-card"><div class="fm-demo-card-head"><strong>' + esc(c.title) + '</strong><span class="fm-demo-tag">합성</span></div>' +
        (c.purpose ? '<p class="fm-demo-muted">목적: ' + esc(c.purpose) + '</p>' : '') +
        '<button type="button" class="btn btn-primary" data-company="' + esc(c.id) + '">과제 보기·실행</button></li>';
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
