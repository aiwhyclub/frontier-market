/* S4 판단 과제 카탈로그 (F4.1~F4.5). 모든 서버 텍스트는 textContent로만 표시한다. */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var STATUS_LABEL = { draft: t('catalog.draft'), ready: t('catalog.ready'), hold: t('catalog.on_hold') };
  var AXIS_HINT = t('catalog.write_the_evidence_or_write');

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'className') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function badge(text, cls) { return el('span', { className: 'fm-badge ' + (cls || ''), text: text }); }
  function synthBadge(item) { return item && item.synthetic ? badge(t('catalog.synthetic'), 'fm-badge-synthetic') : null; }

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
          var e = data.error || {};
          err.userMessage = e.message || t('catalog.could_not_save_your_input');
          err.fieldErrors = e.fields || e.field_errors || null;
          throw err;
        }
        return data;
      });
    });
  }

  function fieldsOfForm(values, keys) {
    var out = {};
    keys.forEach(function (k) { if (values[k] !== undefined && values[k] !== '') out[k] = values[k]; });
    return out;
  }
  function lines(v) { return v ? v.split('\n').map(function (s) { return s.trim(); }).filter(Boolean) : []; }

  var CREATE_FIELDS = [
    { name: 'title', label: t('catalog.title_judgement_task_name'), type: 'text', required: true },
    { name: 'domain', label: t('catalog.domain'), type: 'text', required: true },
    { name: 'summary', label: t('catalog.summary'), type: 'textarea', required: true },
    { name: 'judgement_task', label: t('catalog.judgement_task_to_buy'), type: 'textarea', required: true },
    { name: 'target_capability', label: t('catalog.target_capability'), type: 'text', required: true },
    { name: 'expected_deliverables', label: t('catalog.expected_deliverables'), type: 'textarea', required: true },
    { name: 'apprentice_context', label: t('catalog.apprenticeship_learning_context'), type: 'textarea' },
    { name: 'tacit_signals', label: t('catalog.expert_cues_one_per_line'), type: 'textarea' },
    { name: 'novice_gap', label: t('catalog.novice_differences'), type: 'textarea' },
    { name: 'evidence_refs', label: t('catalog.evidence_references_one_per_line'), type: 'textarea' },
    { name: 'media_refs', label: t('catalog.media_references_one_per_line'), type: 'textarea' }
  ];
  var AXIS_FIELDS = [
    { name: 'origin', label: t('catalog.origin_in_apprenticeship_experience'), type: 'textarea' },
    { name: 'documentation_gap', label: t('catalog.non_standard_undocumented_gap'), type: 'textarea' },
    { name: 'field_need', label: t('catalog.on_site_baseline_need_cost'), type: 'textarea' },
    { name: 'novice_difference', label: t('catalog.expert_vs_novice_difference'), type: 'textarea' },
    { name: 'outcome_verifiability', label: t('catalog.verifiability_of_results'), type: 'textarea' },
    { name: 'lab_link', label: t('catalog.link_to_lab_evaluated_capability'), type: 'textarea' }
  ];
  var EDIT_FIELDS = CREATE_FIELDS.map(function (f) {
    return { name: f.name, label: f.label, type: f.type, required: false };
  });
  var LINK_FIELDS = [
    { name: 'source_ids', label: t('catalog.material_ids_comma_separated'), type: 'text' },
    { name: 'expert_ids', label: t('catalog.approved_expert_ids_comma_separated'), type: 'text' },
    { name: 'work_ids', label: t('catalog.raw_work_ids_comma_separated'), type: 'text' }
  ];

  function ids(v) { return v ? v.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : []; }

  function listView(root, openCard) {
    root.textContent = '';
    root.appendChild(el('p', { className: 'fm-note', text: t('catalog.loading') }));
    api('GET', '/api/catalog/').then(function (data) {
      root.textContent = '';
      var head = el('div', { className: 'fm-toolbar' }, [
        el('h2', { text: t('catalog.judgement_task_catalog') }),
        el('button', { className: 'fm-btn fm-btn-primary', type: 'button', text: t('catalog.new_task_card'),
          onclick: function () { createCard(root, openCard); } }),
        el('button', { className: 'fm-btn', type: 'button', text: t('catalog.create_ai_draft'), id: 'catalog-ai-open',
          onclick: function () { aiDraftPanel(root, openCard); } })
      ]);
      root.appendChild(head);
      root.appendChild(el('div', { id: 'catalog-ai-panel' }));
      if (!data.items || !data.items.length) {
        root.appendChild(el('p', { className: 'fm-empty', text: t('catalog.no_task_cards_yet_try') }));
        return;
      }
      var grid = el('ul', { className: 'catalog-grid' });
      data.items.forEach(function (c) {
        var card = el('li', { className: 'catalog-card' }, [
          el('button', { className: 'catalog-card-open', type: 'button', onclick: function () { openCard(c.id); } }, [
            el('strong', { text: c.title }),
            el('div', {}, [
              badge(STATUS_LABEL[c.card_status] || c.card_status, 'catalog-status-' + c.card_status),
              badge(c.domain || t('catalog.domain_not_entered')),
              badge(t('catalog.discovery_evidence') + ' ' + c.evidence_axis_count + '/6'),
              synthBadge(c)
            ])
          ])
        ]);
        grid.appendChild(card);
      });
      root.appendChild(grid);
    }).catch(function (err) {
      root.textContent = '';
      root.appendChild(el('p', { className: 'fm-error', role: 'alert', text: err.userMessage || t('catalog.could_not_load_the_list') }));
      root.appendChild(el('button', { className: 'fm-btn', type: 'button', text: t('catalog.try_again'), onclick: function () { listView(root, openCard); } }));
    });
  }

  var DRAFT_FIELDS = [
    ['title', t('catalog.title_judgement_task_name'), 'input'], ['domain', t('catalog.domain'), 'input'], ['summary', t('catalog.summary'), 'textarea'],
    ['judgement_task', t('catalog.judgement_task_to_buy'), 'textarea'], ['target_capability', t('catalog.target_capability'), 'input'],
    ['expected_deliverables', t('catalog.expected_deliverables'), 'textarea']
  ];

  /* F10.4: AI가 자료를 근거로 만든 초안을 사람이 읽고 고쳐서 채택한다. 수동 작성이 기본 경로다. */
  function aiDraftPanel(root, openCard) {
    var box = root.querySelector('#catalog-ai-panel');
    box.textContent = '';
    var msg = el('p', { className: 'fm-note', role: 'status', text: t('catalog.checking_ai_status') });
    var idInput = el('input', { id: 'catalog-ai-sources', className: 'fm-input', type: 'text', autocomplete: 'off' });
    var go = el('button', { className: 'fm-btn fm-btn-primary', type: 'button', text: t('catalog.create_draft'), disabled: '' });
    var result = el('div', { id: 'catalog-ai-result' });
    box.appendChild(el('section', { className: 'catalog-ai' }, [
      el('h3', { text: t('catalog.ai_draft_optional') }),
      el('p', { className: 'fm-note', text: t('catalog.an_ai_draft_is_only') }),
      msg,
      el('label', { for: 'catalog-ai-sources', text: t('catalog.source_material_ids_comma_separated') }), idInput, go, result
    ]));
    api('GET', '/api/catalog/ai-status').then(function (st) {
      if (st.adopted) {
        msg.textContent = t('catalog.ai_available') + ' ' + st.label + t('catalog.you_can_still_write_manually');
        go.removeAttribute('disabled');
      } else {
        msg.textContent = t('catalog.ai_is_not_adopted_please');
      }
    }).catch(function () { msg.textContent = t('catalog.could_not_check_ai_status'); });
    go.addEventListener('click', function () {
      result.textContent = '';
      go.setAttribute('disabled', '');
      api('POST', '/api/catalog/ai-draft', { source_ids: ids(idInput.value) }).then(function (d) {
        if (!d.draft) {
          result.appendChild(el('p', { className: 'fm-error', role: 'alert',
            text: t('catalog.could_not_get_an_ai') + (d.ai.error || t('catalog.not_adopted')) + t('catalog.continue_with_manual_writing') }));
          return;
        }
        renderDraft(result, d, openCard);
      }).catch(function (err) {
        result.appendChild(el('p', { className: 'fm-error', role: 'alert', text: err.userMessage || t('catalog.could_not_create_a_draft') }));
      }).then(function () { go.removeAttribute('disabled'); });
    });
  }

  function renderDraft(result, d, openCard) {
    var inputs = {};
    var form = el('form', { className: 'catalog-ai-draft' }, [
      el('h4', { text: d.ai.label }),
      el('p', { className: 'fm-note', text: t('catalog.source_materials') + ' ' + d.source_ids.join(', ') + ' ' + t('catalog.this_draft_has_not_been') })
    ]);
    DRAFT_FIELDS.forEach(function (f) {
      var id = 'catalog-ai-' + f[0];
      var input = el(f[2], { id: id, className: 'fm-input' });
      if (f[2] === 'textarea') input.setAttribute('rows', '3'); else input.setAttribute('type', 'text');
      input.value = d.draft[f[0]] || '';
      inputs[f[0]] = input;
      form.appendChild(el('div', { className: 'fm-field' }, [el('label', { for: id, text: f[1] }), input]));
    });
    var status = el('p', { className: 'fm-note', role: 'status' });
    form.appendChild(status);
    form.appendChild(el('button', { className: 'fm-btn fm-btn-primary', type: 'submit', text: t('catalog.adopt_with_edits_and_create') }));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var body = { synthetic: false };
      Object.keys(inputs).forEach(function (k) { body[k] = inputs[k].value.trim(); });
      api('POST', '/api/catalog/ai-draft/' + encodeURIComponent(d.ai.run_id) + '/adopt', body).then(function (r) {
        openCard(r.item.id);
      }).catch(function (err) { status.textContent = err.userMessage || t('catalog.could_not_adopt_it'); });
    });
    result.textContent = '';
    result.appendChild(form);
  }

  function createCard(root, openCard) {
    window.FMForms.openForm({
      title: t('catalog.new_judgement_task_card'),
      fields: CREATE_FIELDS.concat([{ name: 'synthetic', label: t('catalog.synthetic_material_flag_true_false'), type: 'text' }]),
      onSubmit: function (values) {
        var body = fieldsOfForm(values, CREATE_FIELDS.map(function (f) { return f.name; }));
        ['tacit_signals', 'evidence_refs'].forEach(function (k) { if (body[k]) body[k] = lines(body[k]); });
        body.synthetic = String(values.synthetic || '').toLowerCase() === 'true';
        return api('POST', '/api/catalog/', body).then(function (data) {
          window.FMForms && window.FMForms.showToast && window.FMForms.showToast(t('catalog.task_card_saved'));
          openCard(data.item.id);
        });
      }
    });
  }

  function detailView(root, cardId, goList) {
    root.textContent = '';
    root.appendChild(el('p', { className: 'fm-note', text: t('catalog.loading') }));
    api('GET', '/api/catalog/' + encodeURIComponent(cardId)).then(function (d) {
      render(d);
    }).catch(function (err) {
      root.textContent = '';
      root.appendChild(el('p', { className: 'fm-error', role: 'alert', text: err.userMessage || t('catalog.could_not_load_the_task') }));
    });

    function render(d) {
      var item = d.item;
      root.textContent = '';
      root.appendChild(el('button', { className: 'fm-btn', type: 'button', text: t('catalog.list'), onclick: goList }));
      root.appendChild(el('h2', { text: item.title }));
      root.appendChild(el('div', {}, [
        badge(STATUS_LABEL[d.card.status] || d.card.status, 'catalog-status-' + d.card.status), synthBadge(item)
      ]));
      root.appendChild(el('p', { className: 'fm-note',
        text: item.ai_draft ? item.ai_draft.label + ' ' + t('catalog.a_card_adopted_after_human') : t('catalog.written_manually') }));
      root.appendChild(el('p', { className: 'fm-note', text: t('catalog.readiness_is_not_rights_or') + ' ' + d.card.limits.join(' / ') }));

      function section(title, rows) {
        var dl = el('dl', { className: 'catalog-dl' });
        rows.forEach(function (r) { dl.appendChild(el('dt', { text: r[0] })); dl.appendChild(el('dd', { text: r[1] || t('catalog.not_entered') })); });
        root.appendChild(el('section', {}, [el('h3', { text: title }), dl]));
      }
      section(t('catalog.task_card'), [[t('catalog.domain'), item.domain], [t('catalog.summary'), item.summary], [t('catalog.judgement_task_to_buy'), item.judgement_task],
        [t('catalog.target_capability'), item.target_capability], [t('catalog.expected_deliverables'), item.expected_deliverables]]);
      section(t('catalog.apprenticeship_cues'), [[t('catalog.learning_context'), item.apprentice_context], [t('catalog.expert_cues'), (item.tacit_signals || []).join(' / ')],
        [t('catalog.novice_differences'), item.novice_gap], [t('catalog.evidence'), (item.evidence_refs || []).join(' / ')],
        [t('catalog.media_references'), (item.media_refs || []).map(function (m) { return m.address + ' | ' + m.captured_at + ' | ' + m.description; }).join('\n')]]);
      root.appendChild(el('section', {}, [el('h3', { text: t('catalog.six_discovery_axes') }),
        el('ul', {}, d.axes.map(function (a) {
          return el('li', { text: a.label + ': ' + (a.status === 'evidence' ? a.note : (a.status === 'unknown' ? t('catalog.unconfirmed') : t('catalog.not_entered'))) });
        }))]));
      if (d.card.hold_reasons.length) {
        root.appendChild(el('section', {}, [el('h3', { text: t('catalog.hold_reason_readiness_conditions') }),
          el('ul', {}, d.card.hold_reasons.map(function (r) { return el('li', { text: r }); }))]));
      }
      var linkItems = [].concat(
        (d.links.sources || []).map(function (l) { return el('li', { text: t('catalog.material') + ' ' + l.title }); }),
        (d.links.experts || []).map(function (l) { return el('li', { text: t('catalog.expert') + ' ' + l.title }); }),
        (d.links.works || []).map(function (l) { return el('li', { text: t('catalog.work') + ' ' + l.title }); }),
        (d.missing_links || []).map(function (m) { return el('li', { className: 'fm-note', text: t('catalog.not_linked') + ' ' + m.reason }); })
      );
      root.appendChild(el('section', {}, [el('h3', { text: t('catalog.links') }), el('ul', {}, linkItems)]));

      root.appendChild(el('div', { className: 'fm-toolbar' }, [
        el('button', { className: 'fm-btn', type: 'button', text: t('catalog.edit_card'), onclick: function () { editCard(); } }),
        el('button', { className: 'fm-btn', type: 'button', text: t('catalog.save_links'), onclick: function () { linkCard(); } }),
        el('button', { className: 'fm-btn', type: 'button', text: t('catalog.record_six_axes'), onclick: function () { axesCard(); } })
      ]));

      function editCard() {
        window.FMForms.openForm({
          title: t('catalog.edit_task_card_blank_fields'),
          fields: EDIT_FIELDS,
          onSubmit: function (values) {
            var body = fieldsOfForm(values, EDIT_FIELDS.map(function (f) { return f.name; }));
            ['tacit_signals', 'evidence_refs'].forEach(function (k) { if (body[k]) body[k] = lines(body[k]); });
            body.expected_version = item.version;
            return api('PATCH', '/api/catalog/' + encodeURIComponent(cardId), body).then(function () { detailView(root, cardId, goList); });
          }
        });
      }
      function linkCard() {
        window.FMForms.openForm({
          title: t('catalog.link_sources_experts_and_work'),
          fields: LINK_FIELDS,
          onSubmit: function (values) {
            return api('POST', '/api/catalog/' + encodeURIComponent(cardId) + '/link', {
              expected_version: item.version,
              source_ids: ids(values.source_ids), expert_ids: ids(values.expert_ids), work_ids: ids(values.work_ids)
            }).then(function () { detailView(root, cardId, goList); });
          }
        });
      }
      function axesCard() {
        window.FMForms.openForm({
          title: t('catalog.record_six_axes_2') + ' ' + AXIS_HINT,
          fields: AXIS_FIELDS,
          onSubmit: function (values) {
            var axes = {};
            AXIS_FIELDS.forEach(function (f) { if (values[f.name]) axes[f.name] = values[f.name]; });
            return api('PATCH', '/api/catalog/' + encodeURIComponent(cardId), { expected_version: item.version, axes: axes })
              .then(function () { detailView(root, cardId, goList); });
          }
        });
      }
    }
  }

  window.FMScreens.s4 = function (container) {
    var root = el('div', { className: 'catalog-root' });
    container.textContent = '';
    container.appendChild(root);
    function openCard(id) { detailView(root, id, function () { listView(root, openCard); }); }
    listView(root, openCard);
  };
})();
