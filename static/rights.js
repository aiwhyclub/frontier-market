/*
 * rights.js — 이용 범위(권리) 패널과 목록 (STORY 6.1-6.2, F9.1/F9.3)
 *
 * window.FMScreens.rights(container, {type, id})
 *   - type/id 있음: 해당 기록의 이용 범위 상태·확인 기록·과거 개정/사건 패널 (상세 화면에서 사용)
 *   - 없음: 권리 기록 목록 + 새 기록 등록
 * 서버 텍스트는 textContent로만 넣는다. 합성 기록에는 '합성' 배지를 붙인다.
 */
(function () {
  'use strict';

  window.FMScreens = window.FMScreens || {};

  var TYPE_LABEL = { materials: t('collect.materials'), experts: t('app.experts'), works: t('demo.work') };
  var STATUS_LABEL = {
    confirmed: t('rights.confirmed'), restricted: t('rights.restricted'), unconfirmed: t('catalog.unconfirmed'),
    revoked: t('rights.revoked'), expired: t('rights.expired'), legacy: t('rights.legacy_format')
  };
  var PERM_LABEL = { unknown: t('catalog.unconfirmed'), allowed: t('rights.allowed'), denied: t('rights.denied') };
  var PURPOSE_LABEL = { training: t('rights.training'), evaluation: t('rights.evaluation') };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function badgeSynthetic(item) {
    return item && item.synthetic ? el('span', 'fm-badge', t('catalog.synthetic')) : null;
  }

  function api(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error('request failed');
          err.userMessage = (data && data.error && data.error.message) ||
            t('rights.could_not_load_please_try');
          err.fields = data && data.error && data.error.fields;
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function fail(container, message, retry) {
    container.innerHTML = '';
    var box = el('div', 'rights-error');
    box.setAttribute('role', 'alert');
    box.appendChild(el('p', null, message));
    if (retry) {
      var btn = el('button', 'btn', t('catalog.try_again'));
      btn.type = 'button';
      btn.addEventListener('click', retry);
      box.appendChild(btn);
    }
    container.appendChild(box);
  }

  function loading(container) {
    container.innerHTML = '';
    container.appendChild(el('p', 'rights-loading', t('demo.loading')));
  }

  function formFields(fixed) {
    var fields = [];
    if (!fixed) {
      fields.push({ name: 'subject_type', label: t('rights.subject_type_materials_experts_works'), type: 'text', required: true });
      fields.push({ name: 'subject_id', label: t('rights.subject_id'), type: 'text', required: true });
    }
    fields.push({ name: 'subject_version', label: t('rights.subject_revision_number_integer'), type: 'number', required: true });
    fields.push({ name: 'holder', label: t('rights.rights_holder'), type: 'text', required: true });
    fields.push({ name: 'purposes', label: t('rights.purposes_training_evaluation_comma_separ'), type: 'text', required: true });
    fields.push({ name: 'derivative', label: t('rights.derivatives_unknown_allowed_denied'), type: 'text', required: true });
    fields.push({ name: 'distribution', label: t('rights.distribution_unknown_allowed_denied'), type: 'text', required: true });
    fields.push({ name: 'status', label: t('rights.status_confirmed_restricted_unconfirmed_'), type: 'text', required: true });
    fields.push({ name: 'reason', label: t('rights.reason'), type: 'textarea', required: true });
    fields.push({ name: 'evidence', label: t('rights.evidence_required_for_confirmed_restrict'), type: 'textarea', required: false });
    fields.push({ name: 'restrictions', label: t('rights.restrictions_required_for_restricted'), type: 'textarea', required: false });
    fields.push({ name: 'valid_until', label: t('rights.period_end_date_yyyy_mm'), type: 'text', required: false });
    fields.push({ name: 'actor', label: t('rights.owner_designated_operations_owner'), type: 'text', required: true });
    return fields;
  }

  function grantRow(g, current) {
    var row = el('li', 'rights-grant');
    var head = el('div', 'rights-grant-head');
    head.appendChild(el('strong', null, g.title));
    head.appendChild(el('span', 'fm-badge', STATUS_LABEL[g.effective] || g.effective));
    if (g.synthetic) head.appendChild(badgeSynthetic(g));
    row.appendChild(head);
    var dl = el('dl', 'rights-facts');
    [
      [t('rights.rights_holder'), g.holder],
      [t('rights.revision_number'), g.subject_version + (g.applies_now ? ' ' + t('rights.matches_current_content') : ' ' + t('rights.earlier_content'))],
      [t('company.purpose_2'), (g.purposes || []).map(function (p) { return PURPOSE_LABEL[p] || p; }).join(', ') || t('collect.none')],
      [t('rights.derivatives'), PERM_LABEL[g.derivative] || g.derivative],
      [t('rights.distribution'), PERM_LABEL[g.distribution] || g.distribution],
      [t('rights.period_end'), g.valid_until || t('collect.none')],
      [t('rights.restrictions'), g.restrictions || t('collect.none')],
      [t('catalog.evidence'), g.evidence || t('collect.none')],
      [t('rights.reason'), g.reason || t('collect.none')],
      [t('rights.confirmed_by_at'), g.confirmed_by ? g.confirmed_by + ' · ' + (g.confirmed_at || '') : t('collect.none')],
      [t('rights.recorded_by_at'), g.recorded_by ? g.recorded_by + ' · ' + (g.recorded_at || '') : t('collect.none')]
    ].forEach(function (pair) {
      dl.appendChild(el('dt', null, pair[0]));
      dl.appendChild(el('dd', null, pair[1] === undefined || pair[1] === null ? t('collect.none') : pair[1]));
    });
    row.appendChild(dl);
    return row;
  }

  function renderState(state) {
    var box = el('section', 'rights-state');
    var isHold = state.state !== 'allowed';
    box.appendChild(el('h3', null, t('rights.usage_scope')));
    var verdict = el('p', isHold ? 'rights-hold' : 'rights-allowed',
      isHold ? t('rights.hold_creation_and_further_export') : t('rights.allowed_usable_within_the_confirmed'));
    box.appendChild(verdict);
    if (state.reasons && state.reasons.length) {
      var ul = el('ul', 'rights-reasons');
      state.reasons.forEach(function (r) { ul.appendChild(el('li', null, r)); });
      box.appendChild(ul);
    }
    if (state.past_grant_count) {
      box.appendChild(el('p', 'rights-note', t('rights.rights_records_for_earlier_revisions') + ' ' + state.past_grant_count + t('rights.do_not_apply_to_the')));
    }
    return box;
  }

  function panel(container, opts) {
    var stype = opts.type;
    var sid = opts.id;
    var seq = 0;
    function reload() {
      var my = ++seq;
      loading(container);
      Promise.all([
        api('GET', '/api/rights/state?subject_type=' + encodeURIComponent(stype) + '&subject_id=' + encodeURIComponent(sid)),
        api('GET', '/api/rights?subject_type=' + encodeURIComponent(stype) + '&subject_id=' + encodeURIComponent(sid) + '&limit=100'),
        api('GET', '/api/rights/history/' + encodeURIComponent(stype) + '/' + encodeURIComponent(sid))
      ]).then(function (results) {
        if (my !== seq) return;
        render(results[0], results[1], results[2]);
      }).catch(function (err) {
        if (my !== seq) return;
        fail(container, err.userMessage || t('rights.could_not_load_rights_information'), reload);
      });
    }

    function render(state, list, hist) {
      container.innerHTML = '';
      var wrap = el('div', 'rights-panel');
      var head = el('div', 'rights-head');
      head.appendChild(el('h2', null, t('rights.usage_scope_2') + ' ' + (TYPE_LABEL[stype] || stype)));
      var add = el('button', 'btn', t('rights.add_rights_record'));
      add.type = 'button';
      add.addEventListener('click', function () { openRecord(null); });
      head.appendChild(add);
      wrap.appendChild(head);
      wrap.appendChild(renderState(state));

      var list2 = el('section', 'rights-grants');
      list2.appendChild(el('h3', null, t('rights.rights_records')));
      if (!list.items.length) {
        list2.appendChild(el('p', 'rights-empty', t('rights.no_rights_records_yet_please')));
      } else {
        var ul = el('ul', 'rights-list');
        list.items.forEach(function (g) {
          var row = grantRow(g);
          var edit = el('button', 'btn', t('rights.edit'));
          edit.type = 'button';
          edit.addEventListener('click', function () { openRecord(g); });
          row.appendChild(edit);
          ul.appendChild(row);
        });
        list2.appendChild(ul);
      }
      wrap.appendChild(list2);

      var histBox = el('section', 'rights-history');
      histBox.appendChild(el('h3', null, t('rights.revision_history_and_decision_events')));
      histBox.appendChild(el('p', 'rights-note', t('rights.current_record_revision') + ' ' + hist.current.version + ' (H ' + String(hist.current.material_hash).slice(0, 12) + '…)'));
      var revs = el('ul', 'rights-revs');
      hist.revisions.forEach(function (rv) {
        var li = el('li', null, (rv.is_current ? t('rights.current') + ' ' : t('rights.past') + ' ') + t('rights.revision') + ' ' + rv.version + ' · ' + rv.created_at);
        var btn = el('button', 'btn', t('rights.view_content_at_that_time'));
        btn.type = 'button';
        btn.addEventListener('click', function () { showSnapshot(rv); });
        li.appendChild(btn);
        revs.appendChild(li);
      });
      histBox.appendChild(revs);
      var evs = el('ul', 'rights-events');
      if (!hist.events.length) evs.appendChild(el('li', null, t('rights.no_rights_events_recorded')));
      hist.events.forEach(function (e) {
        evs.appendChild(el('li', null, e.at + ' · ' + e.action + ' · ' + (e.status_from || '-') + ' → ' + (e.status_to || '-') +
          ' ' + t('collect.reason_2') + ' ' + e.reason + ' ' + t('collect.owner_3') + ' ' + e.actor + ' ' + t('rights.revision_2') + ' ' + (e.subject_version || '-')));
      });
      histBox.appendChild(evs);
      wrap.appendChild(histBox);
      container.appendChild(wrap);
    }

    function showSnapshot(rv) {
      var s = rv.snapshot || {};
      var rows = [
        { label: t('rights.revision'), value: String(rv.version) },
        { label: t('rights.saved_at'), value: rv.created_at },
        { label: t('collect.title'), value: s.title },
        { label: t('rights.status'), value: s.status },
        { label: t('collect.body_text'), value: s.body },
        { label: t('rights.content_hash'), value: rv.material_hash }
      ];
      if (window.FMForms && window.FMForms.openDetail) {
        window.FMForms.openDetail({ title: t('rights.past_revision_records'), rows: rows });
      }
    }

    function openRecord(existing) {
      if (!window.FMForms || !window.FMForms.openForm) return;
      window.FMForms.openForm({
        title: existing ? t('rights.edit_rights_record') : t('rights.add_rights_record'),
        fields: formFields(true),
        submitLabel: existing ? t('rights.save_changes') : t('experts.save'),
        onSubmit: function (values) {
          var body = toBody(values, { subject_type: stype, subject_id: sid });
          var req = existing
            ? api('PATCH', '/api/rights/' + encodeURIComponent(existing.id),
                  Object.assign(body, { expected_version: existing.version }))
            : api('POST', '/api/rights', body);
          return req.then(function () {
            if (window.FMForms.showToast) window.FMForms.showToast(t('rights.rights_record_saved'));
            reload();
          });
        }
      });
    }

    reload();
  }

  function toBody(values, fixed) {
    var body = Object.assign({}, fixed);
    Object.keys(values).forEach(function (k) {
      if (values[k] !== '' && values[k] !== undefined) body[k] = values[k];
    });
    body.subject_version = Number(values.subject_version);
    if (typeof body.purposes === 'string') {
      body.purposes = body.purposes.split(/[,\s]+/).filter(Boolean);
    }
    return body;
  }

  function listing(container) {
    function reload() {
      loading(container);
      api('GET', '/api/rights?limit=100').then(function (data) {
        container.innerHTML = '';
        var wrap = el('div', 'rights-panel');
        var head = el('div', 'rights-head');
        head.appendChild(el('h2', null, t('rights.usage_scope_records')));
        var add = el('button', 'btn', t('rights.new_rights_record'));
        add.type = 'button';
        add.addEventListener('click', function () {
          if (!window.FMForms) return;
          window.FMForms.openForm({
            title: t('rights.new_rights_confirmation_record'),
            fields: formFields(false),
            onSubmit: function (values) {
              return api('POST', '/api/rights', toBody(values, {})).then(function () {
                if (window.FMForms.showToast) window.FMForms.showToast(t('rights.rights_record_saved'));
                reload();
              });
            }
          });
        });
        head.appendChild(add);
        wrap.appendChild(head);
        if (!data.items.length) {
          wrap.appendChild(el('p', 'rights-empty', t('rights.no_rights_records')));
        } else {
          var ul = el('ul', 'rights-list');
          data.items.forEach(function (g) {
            var row = el('li', 'rights-grant');
            var link = el('button', 'btn-link', g.title);
            link.type = 'button';
            link.addEventListener('click', function () {
              window.FMScreens.rights(container, { type: g.subject_type, id: g.subject_id });
            });
            row.appendChild(link);
            row.appendChild(el('span', 'fm-badge', STATUS_LABEL[g.effective] || g.effective));
            if (g.synthetic) row.appendChild(badgeSynthetic(g));
            row.appendChild(el('span', 'rights-note', g.applies_now ? t('rights.applies_to_current_content') : t('rights.earlier_content_record')));
            ul.appendChild(row);
          });
          wrap.appendChild(ul);
        }
        container.appendChild(wrap);
      }).catch(function (err) {
        fail(container, err.userMessage || t('rights.could_not_load_rights_records'), reload);
      });
    }
    reload();
  }

  window.FMScreens.rights = function (container, opts) {
    if (!container) return;
    if (opts && opts.type && opts.id) {
      panel(container, opts);
    } else {
      listing(container);
    }
  };
})();
