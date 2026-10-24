/*
 * fde.js — S6 FDE 프로젝트 화면 (EPIC 8, F6.1~F6.4)
 * window.FMScreens.s6 (container) 로 등록. 모든 서버 텍스트는 textContent로만 넣는다.
 */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var KIND = { plan: t('fde.plan'), progress: t('fde.progress'), review: t('fde.review'), site_result: t('fde.on_site_result') };
  var BASIS = { planned: t('fde.planned_value'), synthetic: t('fde.synthetic_site'), actual: t('fde.actual_result_recorded_by_a') };

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

  function badge() { return el('span', { className: 'badge-synthetic', text: t('catalog.synthetic') }); }

  function request(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error('request failed');
          err.userMessage = (data.error && data.error.message) || t('fde.the_request_could_not_be');
          err.fields = (data.error && data.error.fields) || {};
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error('network');
      err.userMessage = t('fde.could_not_reach_the_server');
      throw err;
    });
  }

  function fieldErrorText(err) {
    var f = err.fields || {};
    var keys = Object.keys(f);
    return keys.length ? keys.map(function (k) { return k + ': ' + f[k]; }).join(' / ') : '';
  }

  function withFields(err) {
    var extra = fieldErrorText(err);
    if (extra) err.userMessage = err.userMessage + ' (' + extra + ')';
    return err;
  }

  function line(label, value) {
    var v = value === null || value === undefined || value === '' ? t('collect.none') : String(value);
    return el('div', { className: 'fde-row' }, [
      el('dt', { text: label }), el('dd', { text: v })
    ]);
  }

  function section(title, children) {
    return el('section', { className: 'fde-section' }, [el('h3', { text: title })].concat(children));
  }

  function renderState(container, kind, text) {
    container.textContent = '';
    container.appendChild(el('p', { className: kind === 'error' ? 'error-state' : 'empty-state', text: text,
      role: kind === 'error' ? 'alert' : 'status' }));
  }

  function renderList(container, state) {
    var root = el('div', { className: 'fde-root' });
    var head = el('div', { className: 'fde-head' }, [
      el('div', {}, [
        el('h2', { text: t('fde.s6_fde_projects') }), badge(),
        el('p', { className: 'fde-note', text: t('fde.the_cost_to_the_company') })
      ]),
      el('button', { type: 'button', className: 'btn primary', text: t('fde.new_project'), id: 'fde-new' })
    ]);
    root.appendChild(head);
    root.appendChild(el('div', { className: 'fde-options', id: 'fde-options' }));
    root.appendChild(el('ul', { className: 'fde-list', id: 'fde-list' }));
    container.textContent = '';
    container.appendChild(root);
    root.querySelector('#fde-new').addEventListener('click', function () { openCreate(container); });
    loadOptions(root.querySelector('#fde-options'));
    loadList(container, root.querySelector('#fde-list'));
  }

  function loadOptions(box) {
    request('GET', '/api/fde/options').then(function (data) {
      var o = data.options || {};
      box.textContent = '';
      box.appendChild(el('p', { className: 'fde-note', text: t('fde.linkable_record_ids_enter_in') }));
      ['catalog', 'works', 'experts'].forEach(function (k) {
        var label = { catalog: t('app.catalog'), works: t('demo.work'), experts: t('app.experts') }[k];
        var items = o[k] || [];
        var text = items.length
          ? items.map(function (i) { return i.id + ' (' + i.title + ')'; }).join(', ')
          : t('collect.none');
        box.appendChild(el('p', { text: label + ': ' + text }));
      });
    }).catch(function () {
      box.textContent = t('fde.could_not_load_the_linkable');
    });
  }

  function loadList(container, list) {
    list.textContent = '';
    list.appendChild(el('li', { className: 'empty-state', text: t('demo.loading') }));
    request('GET', '/api/fde/projects').then(function (data) {
      var items = data.items || [];
      list.textContent = '';
      if (!items.length) {
        list.appendChild(el('li', { className: 'empty-state', text: t('fde.no_projects_yet_try_registering') }));
        return;
      }
      items.forEach(function (p) {
        var btn = el('button', { type: 'button', className: 'fde-item', 'data-id': p.id }, [
          el('strong', { text: p.title }), badge(),
          el('span', { text: ' · ' + (p.stage_label || t('fde.plan')) + ' ' + t('fde.company_ax_0_krw') })
        ]);
        btn.addEventListener('click', function () { openDetail(container, p.id); });
        list.appendChild(el('li', {}, [btn]));
      });
    }).catch(function (err) {
      list.textContent = '';
      list.appendChild(el('li', { className: 'error-state', role: 'alert',
        text: (err && err.userMessage) || t('fde.could_not_load_the_list') }));
    });
  }

  function openCreate(container) {
    window.FMForms.openForm({
      title: t('fde.register_fde_project'),
      submitLabel: t('experts.register'),
      fields: [
        { name: 'title', label: t('fde.project_name'), type: 'text', required: true },
        { name: 'catalog_id', label: t('fde.catalog_id'), type: 'text', required: true },
        { name: 'work_ids', label: t('fde.work_ids_comma_separated'), type: 'text', required: true },
        { name: 'expert_ids', label: t('fde.expert_ids_comma_separated'), type: 'text', required: false },
        { name: 'company_name', label: t('fde.company_name'), type: 'text', required: false },
        { name: 'site_name', label: t('fde.synthetic_site_name'), type: 'text', required: false },
        { name: 'objective', label: t('fde.objective'), type: 'textarea', required: false },
        { name: 'scope', label: t('fde.scope'), type: 'textarea', required: false },
        { name: 'deliverables', label: t('fde.deliverables'), type: 'textarea', required: false },
        { name: 'owner', label: t('fde.owner'), type: 'text', required: false },
        { name: 'notes', label: t('fde.notes'), type: 'textarea', required: false },
        { name: 'planned_start', label: t('fde.planned_start_date_yyyy_mm'), type: 'date', required: false },
        { name: 'planned_end', label: t('fde.planned_end_date_yyyy_mm'), type: 'date', required: false }
      ],
      onSubmit: function (values) {
        var body = {
          synthetic: true,
          title: values.title,
          catalog_id: values.catalog_id,
          work_ids: splitIds(values.work_ids),
          expert_ids: splitIds(values.expert_ids),
          company_name: values.company_name,
          site_name: values.site_name,
          objective: values.objective,
          scope: values.scope,
          deliverables: values.deliverables,
          owner: values.owner,
          notes: values.notes,
          planned_start: values.planned_start,
          planned_end: values.planned_end,
          company_ax_fee: 0
        };
        return request('POST', '/api/fde/projects', body).then(function (data) {
          window.FMForms.showToast(t('fde.project_registered'));
          openDetail(container, data.item.id);
        }, function (err) { throw withFields(err); });
      }
    });
  }

  function splitIds(text) {
    return (text || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function openDetail(container, id) {
    container.textContent = '';
    container.appendChild(el('p', { className: 'empty-state', text: t('demo.loading') }));
    request('GET', '/api/fde/projects/' + encodeURIComponent(id)).then(function (data) {
      renderDetail(container, data);
    }, function (err) {
      renderState(container, 'error', (err && err.userMessage) || t('fde.could_not_load_the_project'));
    });
  }

  function renderDetail(container, data) {
    var p = data.item;
    var f = p.fields || {};
    var links = data.links || [];
    var back = el('button', { type: 'button', className: 'btn', text: t('collect.back_to_list') });
    back.addEventListener('click', function () { renderList(container); });
    var root = el('div', { className: 'fde-root' });
    root.appendChild(el('div', { className: 'fde-head' }, [
      el('div', {}, [el('h2', { text: p.title }), badge(),
        el('p', { className: 'fde-note', text: t('collect.version') + ' ' + p.version + ' ' + t('fde.phase') + ' ' + (f.stage_label || KIND[f.stage] || t('fde.plan')) })]),
      back
    ]));

    var linkItems = links.map(function (l) {
      var t = (l.type || '') + ' · ' + (l.relation || '') + ' · ' + (l.id || '') + ' ' + (l.title || '');
      return el('li', { text: t });
    });
    root.appendChild(section(t('fde.linked_records'), [
      el('ul', {}, linkItems.length ? linkItems : [el('li', { text: t('fde.no_linked_records') })]),
      (data.missing_links && data.missing_links.length)
        ? el('p', { className: 'fde-note', text: t('fde.missing') + ' ' + data.missing_links.map(function (m) { return m.reason || m.relation || ''; }).join(' / ') })
        : null
    ]));

    root.appendChild(section(t('fde.free_ax_scope'), [
      el('dl', {}, [
        line(t('fde.objective_2'), f.objective), line(t('fde.scope_2'), f.scope), line(t('fde.deliverables_2'), f.deliverables),
        line(t('fde.owner'), f.owner), line(t('fde.notes'), f.notes), line(t('fde.company_name'), f.company_name),
        line(t('fde.synthetic_site'), f.site_name), line(t('fde.planned_period'), (f.planned_start || t('collect.none')) + ' ~ ' + (f.planned_end || t('collect.none')))
      ]),
      el('p', { className: 'fde-note', text: t('fde.company_ax_cost_0_krw') })
    ]));

    var stageItems = (f.stage_log || []).map(function (s) {
      return el('li', {}, [
        el('strong', { text: (KIND[s.kind] || s.kind) + ' · ' + (BASIS[s.basis] || s.basis) }),
        el('div', { text: t('fde.details') + ' ' + (s.note || t('collect.none')) }),
        el('div', { text: t('collect.evidence') + ' ' + (s.evidence || t('collect.none')) }),
        el('div', { className: 'fde-note', text: t('fde.recorded_at') + ' ' + (s.recorded_at || '') })
      ]);
    });
    var addStage = el('button', { type: 'button', className: 'btn', text: t('fde.add_phase_record') });
    addStage.addEventListener('click', function () { openStage(container, p); });
    root.appendChild(section(t('fde.phase_records'), [
      el('ul', {}, stageItems.length ? stageItems : [el('li', { text: t('fde.no_phases_recorded_yet') })]), addStage
    ]));

    root.appendChild(section(t('fde.result_type'), [
      el('dl', {}, [
        line(t('fde.company_improvement_handover_result'), f.enterprise_outcome),
        line(t('fde.buyer_delivery_and_acceptance'), f.buyer_delivery),
        line(t('fde.site_result_notes'), f.actual_result)
      ]),
      el('p', { className: 'fde-note', text: t('fde.company_improvement_results_and_buyer') })
    ]));

    root.appendChild(aiProposalSection(container, p, f));
    root.appendChild(pnlSection(container, p, f));

    var edit = el('button', { type: 'button', className: 'btn primary', text: t('fde.edit_information') });
    edit.addEventListener('click', function () { openEdit(container, p); });
    root.appendChild(el('div', { className: 'fde-actions' }, [edit]));

    container.textContent = '';
    container.appendChild(root);
  }

  /* F10.5: AI 범위·교육·도구 제안 초안. 읽고 고쳐 채택해도 실제 현장 설치·성과로 표시하지 않는다. */
  function aiProposalSection(container, p, f) {
    var box = el('div', {});
    var saved = f.ai_proposal;
    var children = [];
    if (saved) {
      children.push(el('p', { className: 'fde-note', text: saved.label + ' ' + t('fde.adopted_proposal') + (saved.adopted_at || '') + ')' }));
      children.push(el('dl', {}, [line(t('fde.scope_proposal'), saved.scope), line(t('fde.training_proposal'), saved.training), line(t('fde.tools_proposal'), saved.tools)]));
      children.push(el('p', { className: 'fde-note', text: saved.note }));
    } else {
      children.push(el('p', { className: 'fde-note', text: t('fde.no_adopted_ai_proposal_you') }));
    }
    var status = el('p', { className: 'fde-note', role: 'status', id: 'fde-ai-status' });
    var btn = el('button', { type: 'button', className: 'btn', text: t('fde.create_ai_scope_training_tools'), id: 'fde-ai-make' });
    var result = el('div', { id: 'fde-ai-result' });
    btn.addEventListener('click', function () {
      btn.setAttribute('disabled', '');
      result.textContent = '';
      request('POST', '/api/fde/projects/' + encodeURIComponent(p.id) + '/ai-draft').then(function (d) {
        if (!d.draft) {
          status.textContent = t('fde.no_ai_draft_was_created') + (d.ai.error || t('fde.ai_not_adopted')) + t('fde.keep_using_manual_entry');
          return;
        }
        status.textContent = d.ai.label + ' ' + t('fde.a_proposal_that_has_not') + ' ' + d.note;
        renderProposalForm(container, p, d, result);
      }, function (err) { status.textContent = err.userMessage || t('catalog.could_not_create_a_draft'); })
        .then(function () { btn.removeAttribute('disabled'); });
    });
    return section(t('fde.ai_proposal_draft_optional'), children.concat([btn, status, result]));
  }

  function renderProposalForm(container, p, d, result) {
    var inputs = {};
    var form = el('form', { className: 'fde-ai-form' });
    [['scope', t('fde.scope_proposal')], ['training', t('fde.training_proposal')], ['tools', t('fde.tools_proposal')]].forEach(function (f) {
      var input = el('textarea', { id: 'fde-ai-' + f[0], rows: '4' });
      input.value = d.draft[f[0]] || '';
      inputs[f[0]] = input;
      form.appendChild(el('div', {}, [el('label', { for: 'fde-ai-' + f[0], text: f[1] }), input]));
    });
    var msg = el('p', { className: 'fde-note', role: 'status' });
    form.appendChild(msg);
    form.appendChild(el('button', { type: 'submit', className: 'btn primary', text: t('fde.adopt_proposal_with_edits_not') }));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var body = { expected_version: p.version };
      Object.keys(inputs).forEach(function (k) { body[k] = inputs[k].value.trim(); });
      request('POST', '/api/fde/projects/' + encodeURIComponent(p.id) + '/ai-draft/' + encodeURIComponent(d.ai.run_id) + '/adopt', body)
        .then(function () { openDetail(container, p.id); },
          function (err) { msg.textContent = withFields(err).userMessage; });
    });
    result.appendChild(form);
  }

  /* F6.5: 선택 가정 손익. 기업 AX 0원·현장 인수·실수금과 섞지 않는다. */
  function pnlSection(container, p, f) {
    var pnl = f.pnl;
    var children = [];
    if (pnl) {
      children.push(el('p', { className: 'fde-note', text: pnl.label + ' ' + t('fde.currency') + ' ' + pnl.currency + ' ' + t('fde.unit') + ' ' + pnl.unit }));
      children.push(el('dl', {}, [
        line(t('fde.assumed_price'), pnl.price + ' ' + pnl.currency), line(t('fde.assumed_cost'), pnl.cost + ' ' + pnl.currency),
        line(t('fde.assumed_profit'), pnl.margin + ' ' + pnl.currency), line(t('fde.assumed_profit_margin'), pnl.margin_rate_label)
      ]));
      children.push(el('p', { className: 'fde-note', text: pnl.note }));
    } else {
      children.push(el('p', { className: 'fde-note', text: t('fde.no_assumed_profit_entered_this') }));
    }
    children.push(el('p', { className: 'fde-note', text: t('fde.the_company_ax_cost_is') }));
    var btn = el('button', { type: 'button', className: 'btn', text: pnl ? t('fde.edit_assumed_profit') : t('fde.enter_assumed_profit') });
    btn.addEventListener('click', function () { openPnl(container, p, pnl); });
    children.push(btn);
    if (pnl) {
      var clear = el('button', { type: 'button', className: 'btn', text: t('fde.clear_assumed_profit') });
      clear.addEventListener('click', function () {
        request('DELETE', '/api/fde/projects/' + encodeURIComponent(p.id) + '/pnl', { expected_version: p.version })
          .then(function () { openDetail(container, p.id); });
      });
      children.push(clear);
    }
    return section(t('fde.assumed_profit_optional'), children);
  }

  function openPnl(container, p, pnl) {
    window.FMForms.openForm({
      title: t('fde.enter_assumed_profit_assumed_values'),
      submitLabel: t('experts.save'),
      fields: [
        { name: 'currency', label: t('fde.currency_krw_usd_jpy_eur'), type: 'text', required: true },
        { name: 'price', label: t('fde.assumed_price_digits_only_e'), type: 'text', required: true },
        { name: 'cost', label: t('fde.assumed_cost_digits_only'), type: 'text', required: true },
        { name: 'unit', label: t('fde.unit_e_g_1_project'), type: 'text', required: true },
        { name: 'note', label: t('fde.notes_on_assumptions'), type: 'text', required: false }
      ],
      onSubmit: function (values) {
        var body = { expected_version: p.version, currency: values.currency.toUpperCase(), price: values.price,
          cost: values.cost, unit: values.unit };
        if (values.note) body.note = values.note;
        return request('PUT', '/api/fde/projects/' + encodeURIComponent(p.id) + '/pnl', body).then(function () {
          window.FMForms.showToast(t('fde.assumed_profit_saved_these_are'));
          openDetail(container, p.id);
        }, function (err) { throw withFields(err); });
      }
    });
  }

  function openEdit(container, p) {
    window.FMForms.openForm({
      title: t('fde.edit_project_information'),
      submitLabel: t('experts.save'),
      fields: [
        { name: 'objective', label: t('fde.objective_leave_blank_to_keep'), type: 'textarea', required: false },
        { name: 'scope', label: t('fde.scope_leave_blank_to_keep'), type: 'textarea', required: false },
        { name: 'deliverables', label: t('fde.deliverables_leave_blank_to_keep'), type: 'textarea', required: false },
        { name: 'owner', label: t('fde.owner_leave_blank_to_keep'), type: 'text', required: false },
        { name: 'notes', label: t('fde.notes_leave_blank_to_keep'), type: 'textarea', required: false },
        { name: 'enterprise_outcome', label: t('fde.company_improvement_handover_result_leav'), type: 'textarea', required: false },
        { name: 'buyer_delivery', label: t('fde.buyer_delivery_and_acceptance_leave'), type: 'textarea', required: false }
      ],
      onSubmit: function (values) {
        var body = { expected_version: p.version };
        Object.keys(values).forEach(function (k) { if (values[k]) body[k] = values[k]; });
        if (Object.keys(body).length === 1) return Promise.resolve();
        return request('PATCH', '/api/fde/projects/' + encodeURIComponent(p.id), body).then(function () {
          window.FMForms.showToast(t('fde.saved'));
          openDetail(container, p.id);
        }, function (err) { throw withFields(err); });
      }
    });
  }

  function openStage(container, p) {
    window.FMForms.openForm({
      title: t('fde.add_phase_record'),
      submitLabel: t('fde.records'),
      fields: [
        { name: 'kind', label: t('fde.phase_plan_progress_review_site'), type: 'text', required: true },
        { name: 'basis', label: t('fde.evidence_type_planned_synthetic_actual'), type: 'text', required: true },
        { name: 'note', label: t('fde.details_2'), type: 'textarea', required: true },
        { name: 'evidence', label: t('fde.basis_for_what_was_done'), type: 'textarea', required: false }
      ],
      onSubmit: function (values) {
        var body = {
          expected_version: p.version,
          kind: values.kind,
          basis: values.basis,
          note: values.note,
          evidence: values.evidence
        };
        return request('POST', '/api/fde/projects/' + encodeURIComponent(p.id) + '/stages', body).then(function () {
          window.FMForms.showToast(t('fde.phase_recorded_the_record_is'));
          openDetail(container, p.id);
        }, function (err) { throw withFields(err); });
      }
    });
  }

  window.FMScreens.s6 = function (container) {
    renderList(container);
  };
})();
