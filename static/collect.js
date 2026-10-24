/* S1 자료 수집: URL 가져오기, 본문 직접 등록, 목록·상세, 원문·근거 개정, 정리 초안 채택/교정. */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var REASONS = {
    invalid_url: t('collect.the_address_format_is_not'),
    unsupported_scheme: t('collect.only_http_or_https_addresses'),
    blocked_port: t('collect.only_public_addresses_on_port'),
    blocked_address: t('collect.this_is_an_internal_or'),
    dns_error: t('collect.the_address_could_not_be'),
    http_error: t('collect.the_server_did_not_respond'),
    too_many_redirects: t('collect.too_many_redirects'),
    unsupported_type: t('collect.only_html_text_and_json'),
    too_large: t('collect.the_material_is_larger_than'),
    text_too_long: t('collect.the_text_is_longer_than'),
    timeout: t('collect.no_response_within_10_seconds'),
    network_error: t('collect.could_not_connect'),
    empty: t('collect.there_is_no_readable_text'),
    unreadable: t('collect.the_character_encoding_could_not')
  };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'class') node.className = attrs[k];
        else if (k === 'text') node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  function badge() {
    return el('span', { class: 'cl-badge', text: t('catalog.synthetic') });
  }

  function request(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    }, function () {
      return { ok: false, status: 0, data: { error: { code: 'network', message: t('collect.could_not_reach_the_server') } } };
    });
  }

  function errorText(res) {
    var err = (res.data && res.data.error) || {};
    var text = err.message || t('collect.could_not_save_your_input');
    if (err.reason && REASONS[err.reason]) text += ' ' + t('collect.reason') + ' ' + REASONS[err.reason];
    if (err.fields) {
      var parts = Object.keys(err.fields).map(function (k) { return k + ': ' + err.fields[k]; });
      if (parts.length) text += ' (' + parts.join(', ') + ')';
    }
    return text;
  }

  function toast(msg) {
    if (typeof window.showToast === 'function') window.showToast(msg);
  }

  function field(label, input) {
    return el('label', { class: 'cl-field' }, [el('span', { text: label }), input]);
  }

  function input(name, value, multiline) {
    var node = el(multiline ? 'textarea' : 'input', { name: name, class: 'cl-input' });
    if (multiline) node.rows = 5; else node.type = 'text';
    node.value = value || '';
    return node;
  }

  function section(title, children) {
    return el('section', { class: 'cl-section' }, [el('h3', { text: title })].concat(children));
  }

  function statusLine(node, text, isError) {
    node.textContent = text;
    node.className = 'cl-status' + (isError ? ' cl-status-error' : '');
  }

  function fetchForm(root, onDone) {
    var url = input('url', '');
    url.placeholder = 'https://…';
    url.inputMode = 'url';
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, [
      field(t('collect.public_address_url'), url),
      el('p', { class: 'cl-hint', text: t('collect.only_public_pages_are_fetched') }),
      el('button', { type: 'submit', class: 'cl-btn', text: t('collect.fetch') }),
      status
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var btn = form.querySelector('button');
      btn.disabled = true;
      statusLine(status, t('collect.fetching'));
      request('POST', '/api/collect/fetch', { url: url.value }).then(function (res) {
        btn.disabled = false;
        if (res.ok) {
          toast(t('collect.material_saved'));
          onDone(res.data.item.id);
          return;
        }
        // Keep the typed URL so the user can fix it and retry, or switch to direct body entry.
        statusLine(status, errorText(res), true);
      });
    });
    return section(t('collect.fetch_from_url'), [form]);
  }

  function manualForm(onDone) {
    var title = input('title', '');
    var kind = input('kind', '');
    var sourceType = input('source_type', '');
    var sourceName = input('source_name', '');
    var body = input('body', '', true);
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, [
      field(t('collect.title'), title),
      field(t('catalog.domain'), kind),
      field(t('collect.type'), sourceType),
      field(t('collect.source_name'), sourceName),
      field(t('collect.body_text'), body),
      el('p', { class: 'cl-hint', text: t('collect.even_if_fetching_fails_you') }),
      el('button', { type: 'submit', class: 'cl-btn', text: t('collect.save_text') }),
      status
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      statusLine(status, t('collect.saving'));
      request('POST', '/api/collect/materials', {
        title: title.value, body: body.value, kind: kind.value,
        source_type: sourceType.value, source_name: sourceName.value, synthetic: true
      }).then(function (res) {
        if (res.ok) {
          toast(t('collect.text_saved'));
          onDone(res.data.item.id);
          return;
        }
        statusLine(status, errorText(res), true);
      });
    });
    return section(t('collect.register_text_directly'), [form]);
  }

  function listPanel(onOpen) {
    var status = el('p', { class: 'cl-status', text: t('collect.loading_the_list'), 'aria-live': 'polite' });
    var list = el('ul', { class: 'cl-list' });
    var wrap = el('div', null, [status, list]);
    function load() {
      statusLine(status, t('collect.loading_the_list'));
      list.textContent = '';
      request('GET', '/api/records/materials?limit=100').then(function (res) {
        if (!res.ok) { statusLine(status, errorText(res), true); return; }
        var items = (res.data && res.data.items) || [];
        if (!items.length) { statusLine(status, t('collect.no_materials_collected_yet_fetch')); return; }
        statusLine(status, t('collect.materials') + ' ' + items.length + t('collect.items'));
        items.forEach(function (it) {
          var btn = el('button', { type: 'button', class: 'cl-link' }, [
            el('span', { text: it.title }),
            it.synthetic ? badge() : null,
            el('span', { class: 'cl-meta', text: ' v' + it.version })
          ]);
          btn.addEventListener('click', function () { onOpen(it.id); });
          list.appendChild(el('li', null, [btn]));
        });
      });
    }
    load();
    return { node: section(t('collect.collected_materials'), [wrap]), reload: load };
  }

  function detailPanel(id, onChanged) {
    var box = el('div', { class: 'cl-detail' }, [el('p', { class: 'cl-status', text: t('collect.loading_the_material') })]);
    request('GET', '/api/collect/materials/' + encodeURIComponent(id)).then(function (res) {
      box.textContent = '';
      if (!res.ok) {
        box.appendChild(el('p', { class: 'cl-status cl-status-error', text: errorText(res) }));
        return;
      }
      renderDetail(box, res.data, onChanged);
    });
    return box;
  }

  function kv(label, value) {
    return el('div', { class: 'cl-kv' }, [el('dt', { text: label }), el('dd', { text: value == null || value === '' ? '—' : String(value) })]);
  }

  function renderDetail(box, data, onChanged) {
    var item = data.item;
    var f = item;
    var head = el('header', { class: 'cl-detail-head' }, [
      el('h3', { text: item.title }), item.synthetic ? badge() : null,
      el('span', { class: 'cl-meta', text: ' ' + t('collect.current_v') + item.version })
    ]);
    var meta = el('dl', { class: 'cl-kvs' }, [
      kv(t('collect.source_type'), f.origin === 'url' ? t('collect.fetched_from_public_url') : t('collect.registered_directly')),
      kv(t('collect.requested_url'), f.source_url), kv(t('collect.final_url'), f.final_url),
      kv(t('collect.collected_at'), f.fetched_at), kv(t('collect.content_fingerprint_sha_256'), f.content_hash),
      kv(t('collect.record_id'), item.id), kv(t('collect.revisions'), f.revision_count || 0)
    ]);
    if (f.source_url) {
      var openLink = el('a', { href: f.final_url || f.source_url, target: '_blank', rel: 'noopener noreferrer', text: t('collect.open_original_source') });
      meta.appendChild(el('div', { class: 'cl-kv' }, [openLink]));
    }

    var original = el('section', { class: 'cl-section' }, [
      el('h4', { text: t('collect.original_text_as_collected_kept') }),
      el('pre', { class: 'cl-original', text: item.body || '' }),
      el('p', { class: 'cl-meta', text: t('collect.evidence') + ' ' + (f.evidence || t('collect.none')) })
    ]);

    var revision = revisionForm(item, function (res) {
      toast(res.data.changed === false ? t('collect.nothing_has_changed') : t('collect.saved_as_a_new_version'));
      onChanged(item.id);
    });
    var metaForm = metadataForm(item, function () { toast(t('collect.information_saved')); onChanged(item.id); });
    var draft = draftPanel(item, function () { onChanged(item.id); }, data.ai);
    var history = historyPanel(data);

    box.appendChild(head);
    box.appendChild(meta);
    box.appendChild(original);
    box.appendChild(section(t('collect.edit_information_saved_under_the'), [metaForm]));
    box.appendChild(section(t('collect.revise_text_and_evidence_reason'), [revision]));
    box.appendChild(section(t('collect.organized_draft_ai_draft_or'), [draft]));
    box.appendChild(history);
  }

  function metadataForm(item, onSaved) {
    var fields = {
      title: input('title', item.title),
      kind: input('kind', item.kind),
      source_type: input('source_type', item.source_type),
      source_name: input('source_name', item.source_name),
      source_url: input('source_url', item.source_url)
    };
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, Object.keys(fields).map(function (k) {
      return field({ title: t('collect.title'), kind: t('catalog.domain'), source_type: t('collect.type'), source_name: t('collect.source_name'), source_url: t('collect.source_url') }[k], fields[k]);
    }).concat([el('button', { type: 'submit', class: 'cl-btn', text: t('collect.save_information') }), status]));
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var payload = { expected_version: item.version };
      Object.keys(fields).forEach(function (k) { payload[k] = fields[k].value; });
      request('PATCH', '/api/collect/materials/' + encodeURIComponent(item.id), payload).then(function (res) {
        if (res.ok) { onSaved(res); return; }
        statusLine(status, errorText(res), true);
      });
    });
    return form;
  }

  function revisionForm(item, onSaved) {
    var body = input('body', item.body, true);
    var evidence = input('evidence', item.evidence || '', true);
    var reason = input('reason', '');
    var owner = input('owner', '');
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, [
      field(t('collect.original_text'), body), field(t('catalog.evidence'), evidence), field(t('collect.revision_reason'), reason), field(t('collect.owner'), owner),
      el('p', { class: 'cl-hint', text: t('collect.saving_creates_a_new_version') }),
      el('button', { type: 'submit', class: 'cl-btn', text: t('collect.save_revision') }), status
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      statusLine(status, t('collect.saving'));
      request('PATCH', '/api/collect/materials/' + encodeURIComponent(item.id), {
        expected_version: item.version, body: body.value, evidence: evidence.value,
        reason: reason.value, owner: owner.value
      }).then(function (res) {
        if (res.ok) { onSaved(res); return; }
        statusLine(status, errorText(res), true);
      });
    });
    return form;
  }

  function draftPanel(item, onChanged, ai) {
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var out = el('div', null);
    var draftBtn = el('button', { type: 'button', class: 'cl-btn', text: t('collect.create_organized_draft') });
    draftBtn.addEventListener('click', function () {
      statusLine(status, t('collect.creating_the_draft'));
      request('POST', '/api/collect/materials/' + encodeURIComponent(item.id) + '/draft', { expected_version: item.version })
        .then(function (res) {
          if (res.ok) { onChanged(item.id); return; }
          statusLine(status, errorText(res), true);
        });
    });
    out.appendChild(draftBtn);
    if (!ai || !ai.adopted) {
      out.appendChild(el('p', { class: 'cl-meta', text: t('collect.ai_drafts_are_turned_off') }));
    }
    if (item.draft) {
      var isAi = item.draft.mode === 'ai';
      out.appendChild(el('p', { class: 'cl-badge', text: isAi ? t('collect.ai_draft') + item.draft.model + ')' : t('collect.rule_based_draft') }));
      if (item.draft.ai_failure) {
        out.appendChild(el('p', { class: 'cl-meta', text: t('collect.ai_draft_failed') + item.draft.ai_failure.error + t('collect.continuing_with_the_rule_based') }));
      }
      out.appendChild(el('pre', { class: 'cl-draft', text: item.draft.text }));
      out.appendChild(el('p', { class: 'cl-meta', text: t('collect.draft_source') + ' ' + (isAi ? t('collect.ai_draft_before_adoption') : t('collect.rule_based')) + ' ' + t('collect.original_fingerprint') + ' ' + String(item.draft.body_hash).slice(0, 12) + '…' }));
      var reviewer = input('reviewer', '');
      var notes = input('correction_notes', '');
      var corrected = input('text', item.draft.text, true);
      var adoptBtn = el('button', { type: 'button', class: 'cl-btn', text: t('collect.adopt_draft_as_is') });
      var correctBtn = el('button', { type: 'button', class: 'cl-btn', text: t('collect.correct_and_save') });
      adoptBtn.addEventListener('click', function () {
        if (!reviewer.value.trim()) { statusLine(status, t('collect.please_enter_the_owner'), true); return; }
        request('POST', '/api/collect/materials/' + encodeURIComponent(item.id) + '/organized',
          { expected_version: item.version, action: 'adopt', reviewer: reviewer.value }).then(function (res) {
            if (res.ok) { onChanged(item.id); return; }
            statusLine(status, errorText(res), true);
          });
      });
      correctBtn.addEventListener('click', function () {
        request('POST', '/api/collect/materials/' + encodeURIComponent(item.id) + '/organized',
          { expected_version: item.version, action: 'correct', text: corrected.value,
            correction_notes: notes.value, reviewer: reviewer.value }).then(function (res) {
            if (res.ok) { onChanged(item.id); return; }
            statusLine(status, errorText(res), true);
          });
      });
      out.appendChild(el('div', { class: 'cl-form' }, [
        field(t('collect.owner'), reviewer), field(t('collect.correction_reason'), notes), field(t('collect.corrected_version'), corrected),
        el('div', { class: 'cl-actions' }, [adoptBtn, correctBtn])
      ]));
    }
    if (item.organized) {
      var stale = item.organized_stale ? ' ' + t('collect.the_original_changed_and_needs') : '';
      out.appendChild(el('h4', { text: t('collect.adopted_organized_version_stored_apart') }));
      out.appendChild(el('pre', { class: 'cl-draft', text: item.organized.text }));
      out.appendChild(el('p', { class: 'cl-meta', text: t('collect.organized_version_v') + item.organized.organized_version + ' · ' +
        (item.organized.origin === 'rule_draft_adopted' ? t('collect.draft_adopted') : t('collect.human_correction')) + ' ' + t('collect.owner_2') + ' ' + item.organized.reviewer + stale }));
    }
    out.appendChild(status);
    return out;
  }

  function historyPanel(data) {
    var events = (data.events || []).map(function (e) {
      return el('li', null, [el('span', { text: e.created_at + ' · ' + e.kind + ' · v' + (e.from_version || '?') + '→v' + (e.to_version || '?') +
        (e.reason ? ' ' + t('collect.reason_2') + ' ' + e.reason : '') + (e.owner ? ' ' + t('collect.owner_3') + ' ' + e.owner : '') })]);
    });
    var revs = (data.revisions || []).map(function (r) {
      return el('li', null, [el('span', { text: 'v' + r.version + ' · ' + r.created_at })]);
    });
    var works = (data.dependent_works || []).map(function (w) {
      return el('li', null, [el('span', { text: w.title + ' (v' + w.version + ')' })]);
    });
    return section(t('collect.revision_and_event_history'), [
      el('h4', { text: t('collect.event') }), el('ul', { class: 'cl-plain' }, events.length ? events : [el('li', { text: t('collect.no_events') })]),
      el('h4', { text: t('collect.version') }), el('ul', { class: 'cl-plain' }, revs),
      el('h4', { text: t('collect.work_that_uses_this_original') }), el('ul', { class: 'cl-plain' }, works.length ? works : [el('li', { text: t('collect.no_linked_work') })])
    ]);
  }

  window.FMScreens.s1 = function (container) {
    container.textContent = '';
    var detailHost = el('div', { class: 'cl-detail-host' });
    var list = listPanel(function (id) { openDetail(id); });

    function openDetail(id) {
      detailHost.textContent = '';
      detailHost.appendChild(el('button', { type: 'button', class: 'cl-btn cl-ghost', text: t('collect.back_to_list') }, []));
      detailHost.firstChild.addEventListener('click', function () { detailHost.textContent = ''; });
      detailHost.appendChild(detailPanel(id, function (changedId) { openDetail(changedId); list.reload(); }));
      detailHost.scrollIntoView && detailHost.scrollIntoView({ block: 'start' });
    }

    function onCreated(id) { list.reload(); openDetail(id); }

    container.appendChild(el('div', { class: 'cl-screen' }, [
      el('header', { class: 'cl-head' }, [el('h2', { text: t('collect.s1_collect') }),
        el('p', { class: 'cl-hint', text: t('collect.only_synthetic_or_fabricated_cases') })]),
      fetchForm(container, onCreated),
      manualForm(onCreated),
      list.node,
      detailHost
    ]));
  };
})();
