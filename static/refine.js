/* S5 정제·검증 (F5.1~F5.6). Renders into the screen container; server text is set with textContent only. */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var STATE_LABELS = {
    no_correction: t('refine.no_corrected_version'),
    pending: t('demo.awaiting_review'),
    approved: t('experts.approved'),
    rejected: t('experts.rejected'),
    review_required: t('refine.re_review_needed')
  };
  var SECTION_KEYS = ['context', 'observation', 'novice_errors', 'judgement', 'outcome_check', 'criteria', 'questions'];
  var SECTION_LABELS = {
    context: t('refine.context'), observation: t('refine.observed_facts'), novice_errors: t('refine.novice_errors'), judgement: t('refine.judgement_exceptions'),
    outcome_check: t('refine.result_check'), criteria: t('refine.evaluation_criteria'), questions: t('refine.follow_up_questions')
  };
  var VIEW_TABS = [
    { key: 'original', label: t('collect.original_text') },
    { key: 'draft', label: t('catalog.draft') },
    { key: 'ai_draft', label: t('refine.ai_draft') },
    { key: 'corrected', label: t('collect.corrected_version') },
    { key: 'evidence', label: t('catalog.evidence') }
  ];

  // Module-level memory of the selected work and tab so switching tabs or re-rendering keeps the choice.
  var memo = { workId: null, tab: 'original', list: null };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'className') node.className = attrs[k];
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

  function badge(synthetic) {
    return synthetic ? el('span', { className: 'fm-badge', text: t('catalog.synthetic') }) : null;
  }

  function api(method, url, body) {
    var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (data) {
        if (!r.ok) {
          var msg = (data && data.error && data.error.message) || t('refine.the_request_could_not_be');
          var err = new Error(msg);
          err.userMessage = msg;
          err.status = r.status;
          err.details = data && data.error;
          throw err;
        }
        return data;
      });
    });
  }

  function errorBox(message) {
    return el('p', { className: 'empty-state', role: 'alert', text: message });
  }

  function emptyBox(message) {
    return el('p', { className: 'empty-state', text: message });
  }

  function operatorName(detail) {
    return detail.review && detail.review.operator ? detail.review.operator : null;
  }

  function renderPrereq(prereq) {
    var list = el('ul', { className: 'refine-prereq' });
    prereq.checks.forEach(function (c) {
      var item = el('li', { className: c.ok ? 'refine-ok' : 'refine-missing' });
      item.appendChild(el('strong', { text: (c.ok ? t('refine.met') + ' ' : t('refine.missing') + ' ') + c.label }));
      if (!c.ok) {
        item.appendChild(el('p', { text: c.reason || '' }));
        item.appendChild(el('a', { href: c.fix_link, text: c.fix_label }));
      }
      list.appendChild(item);
    });
    return el('section', { className: 'refine-section', 'aria-label': t('refine.pre_refinement_conditions') }, [
      el('h3', { text: prereq.ready ? t('refine.refinement_conditions_met') : t('refine.check_pre_refinement_conditions_complete') }),
      list
    ]);
  }

  function renderTabs(detail, host) {
    var tabs = el('div', { className: 'refine-tabs', role: 'tablist' });
    VIEW_TABS.forEach(function (t) {
      var btn = el('button', {
        type: 'button', role: 'tab', className: 'fm-button' + (memo.tab === t.key ? ' is-active' : ''),
        'aria-selected': memo.tab === t.key ? 'true' : 'false', text: t.label
      });
      btn.addEventListener('click', function () { memo.tab = t.key; render(host, detail); });
      tabs.appendChild(btn);
    });
    var panel = el('div', { className: 'refine-panel', role: 'tabpanel' });
    panel.appendChild(memo.tab === 'ai_draft' ? renderAiPanel(detail, host) : renderView(detail, memo.tab));
    return [tabs, panel];
  }

  function sectionList(content) {
    var dl = el('dl', { className: 'refine-sections' });
    SECTION_KEYS.forEach(function (k) {
      if (!content || !content[k]) return;
      dl.appendChild(el('dt', { text: SECTION_LABELS[k] }));
      dl.appendChild(el('dd', { text: content[k] }));
    });
    return dl;
  }

  function aiLabel(v) {
    return v && v.model ? t('collect.ai_draft') + v.model + ')' : t('collect.rule_based_draft');
  }

  function inlineField(label, tag) {
    var input = el(tag, { className: 'fm-input', rows: '3' });
    return { input: input, row: el('label', { className: 'fm-label' }, [el('span', { text: label }), input]) };
  }

  // F10.2: the AI draft is shown apart from the original, rule draft and correction.
  // Requesting, receiving, failing or rejecting it never changes the current approval.
  function renderAiPanel(detail, host) {
    var v = detail.views.ai_draft;
    var id = detail.item.id;
    var wrap = el('div', {});
    wrap.appendChild(el('p', { className: 'refine-method',
      text: t('refine.showing') + ' ' + (v.content ? aiLabel(v) : t('refine.no_ai_draft_yet')) + ' ' + t('refine.kept_separately_from_the_rule') }));
    if (!detail.ai || !detail.ai.adopted) {
      wrap.appendChild(emptyBox(t('refine.ai_refinement_drafts_are_turned')));
    } else {
      var reqBtn = el('button', { type: 'button', className: 'fm-button', text: t('experts.request_ai_draft') });
      reqBtn.disabled = !detail.prereq.ready;
      reqBtn.addEventListener('click', function () {
        api('POST', '/api/refine/works/' + id + '/ai-draft', { expected_version: detail.item.version })
          .then(function (d) {
            memo.tab = 'ai_draft'; render(host, d);
            if (d.ai_result && !d.ai_result.ok) window.alert(t('catalog.could_not_get_an_ai') + d.ai_result.error + t('refine.the_current_approval_and_original'));
          })
          .catch(function (e) { window.alert(e.userMessage); });
      });
      wrap.appendChild(reqBtn);
    }
    if (!v.content) return wrap;
    wrap.appendChild(el('p', { className: 'refine-meta',
      text: t('refine.status') + ' ' + ({ proposed: t('refine.awaiting_adoption_reference_only'), adopted: t('refine.adopted'), rejected: t('refine.dismissed') }[v.status] || v.status) +
        ' ' + t('refine.time') + ' ' + (v.finished_at || '') }));
    wrap.appendChild(sectionList(v.content));
    var who = inlineField(t('collect.owner'), 'input');
    function decide(kind, label) {
      var b = el('button', { type: 'button', className: 'fm-button', text: label });
      b.addEventListener('click', function () {
        if (!who.input.value.trim()) { window.alert(t('collect.please_enter_the_owner')); return; }
        api('POST', '/api/refine/works/' + id + '/ai-draft/' + kind,
          { expected_version: detail.item.version, reviewer: who.input.value.trim() })
          .then(function (d) { memo.tab = 'ai_draft'; render(host, d); })
          .catch(function (e) { window.alert(e.userMessage); });
      });
      return b;
    }
    if (v.status === 'proposed') {
      wrap.appendChild(who.row);
      wrap.appendChild(decide('adopt', t('refine.adopt_start_correcting')));
      wrap.appendChild(decide('reject', t('refine.dismiss')));
    }
    if (v.status === 'adopted') {
      wrap.appendChild(el('p', { className: 'refine-meta',
        text: t('refine.saving_edits_to_an_adopted') }));
      var edits = {};
      SECTION_KEYS.forEach(function (k) {
        var f = inlineField(SECTION_LABELS[k], 'textarea');
        f.input.value = v.content[k] || '';
        edits[k] = f.input;
        wrap.appendChild(f.row);
      });
      var reason = inlineField(t('collect.correction_reason'), 'textarea');
      var reviewer = inlineField(t('collect.owner'), 'input');
      wrap.appendChild(reason.row);
      wrap.appendChild(reviewer.row);
      var save = el('button', { type: 'button', className: 'fm-button', text: t('refine.save_ai_draft_correction_new') });
      save.disabled = !detail.prereq.ready;
      save.addEventListener('click', function () {
        var refined = {};
        SECTION_KEYS.forEach(function (k) { if (edits[k].value.trim()) refined[k] = edits[k].value.trim(); });
        api('POST', '/api/refine/works/' + id + '/correction', {
          expected_version: detail.item.version, refined: refined, from_ai_draft: true,
          correction_reason: reason.input.value, reviewer: reviewer.input.value
        }).then(function (d) { memo.tab = 'corrected'; render(host, d); window.FMForms.showToast(t('refine.corrected_version_saved_as_a')); })
          .catch(function (e) { window.alert(e.userMessage); });
      });
      wrap.appendChild(save);
    }
    return wrap;
  }

  function renderView(detail, key) {
    var v = detail.views[key];
    var wrap = el('div', {});
    wrap.appendChild(el('p', { className: 'refine-method', text: t('refine.generated_by') + ' ' + v.method_label }));
    if (key === 'original') {
      wrap.appendChild(el('h4', { text: v.title }));
      wrap.appendChild(el('p', { text: v.body || t('refine.the_original_text_is_empty') }));
      (v.materials || []).forEach(function (m) {
        wrap.appendChild(el('div', { className: 'refine-material' }, [
          el('strong', { text: t('refine.linked_material') + ' ' + m.title }), badge(m.synthetic), el('p', { text: m.body || '' })
        ]));
      });
    } else if (key === 'draft') {
      if (!v.content) wrap.appendChild(emptyBox(t('refine.no_draft_yet_press_create')));
      else wrap.appendChild(sectionList(v.content));
    } else if (key === 'corrected') {
      if (!v.content) wrap.appendChild(emptyBox(t('refine.no_corrected_version_yet_correct')));
      else {
        wrap.appendChild(sectionList(v.content));
        wrap.appendChild(el('p', { className: 'refine-meta',
          text: t('refine.correction_reason') + ' ' + (v.reason || '') + ' ' + t('refine.owner') + ' ' + (v.reviewer || '') + ' ' + t('refine.time') + ' ' + (v.at || '') }));
      }
    } else if (key === 'evidence') {
      if (!v.items || !v.items.length) wrap.appendChild(emptyBox(t('refine.no_evidence_registered')));
      else {
        var ul = el('ul', {});
        v.items.forEach(function (e) { ul.appendChild(el('li', { text: typeof e === 'string' ? e : JSON.stringify(e) })); });
        wrap.appendChild(ul);
      }
      wrap.appendChild(el('p', { className: 'refine-meta', text: t('refine.linked_materials') + ' ' + (v.materials || []).length + t('collect.items') }));
    }
    return wrap;
  }

  function renderReviews(detail) {
    var box = el('section', { className: 'refine-section', 'aria-label': t('refine.approval_history') });
    box.appendChild(el('h3', { text: t('refine.decision_history_past_decisions_are') }));
    if (!detail.review.reviews.length) {
      box.appendChild(emptyBox(t('refine.no_approval_or_rejection_recorded')));
    } else {
      var ul = el('ul', { className: 'refine-history' });
      detail.review.reviews.forEach(function (r) {
        ul.appendChild(el('li', {}, [
          el('strong', { text: (r.decision === 'approved' ? t('experts.approved') : t('experts.rejected')) + (r.current_input ? ' ' + t('refine.current_input') : ' ' + t('refine.previous_input')) }),
          el('p', { text: t('collect.reason') + ' ' + r.notes }),
          el('p', { text: t('refine.owner_2') + ' ' + r.reviewer + ' ' + t('refine.time') + ' ' + r.created_at + ' ' + t('refine.work_version') + ' ' + r.work_version }),
          el('p', { className: 'refine-hash', text: t('refine.input_hash') + ' ' + r.input_hash.slice(0, 12) + '…' })
        ]));
      });
      box.appendChild(ul);
    }
    if (detail.revisions.length) {
      var rv = el('ul', { className: 'refine-history' });
      detail.revisions.forEach(function (r) {
        rv.appendChild(el('li', { text: t('refine.original_revision_v') + r.from_version + ' → v' + r.to_version + ' · ' + r.reason + ' · ' + r.reviewer }));
      });
      box.appendChild(el('h4', { text: t('refine.original_revision_history') }));
      box.appendChild(rv);
    }
    return box;
  }

  function actionBar(detail, host) {
    var bar = el('div', { className: 'refine-actions' });
    var id = detail.item.id;
    var ready = detail.prereq.ready;

    var draftBtn = el('button', { type: 'button', className: 'fm-button', text: t('refine.create_draft_rules_not_ai') });
    draftBtn.disabled = !ready;
    draftBtn.addEventListener('click', function () {
      api('POST', '/api/refine/works/' + id + '/draft', { expected_version: detail.item.version })
        .then(function (d) { memo.tab = 'draft'; render(host, d); })
        .catch(function (e) { window.alert(e.userMessage); });
    });

    var correctBtn = el('button', { type: 'button', className: 'fm-button', text: t('refine.save_correction_new_version') });
    correctBtn.disabled = !ready;
    correctBtn.addEventListener('click', function () {
      var fields = SECTION_KEYS.map(function (k) { return { name: k, label: SECTION_LABELS[k], type: 'textarea', required: false }; });
      fields.push({ name: 'correction_reason', label: t('collect.correction_reason'), type: 'textarea', required: true });
      fields.push({ name: 'reviewer', label: t('collect.owner'), type: 'text', required: true });
      window.FMForms.openForm({
        title: t('refine.refinement_correction'), fields: fields,
        onSubmit: function (values) {
          var refined = {};
          SECTION_KEYS.forEach(function (k) { if (values[k]) refined[k] = values[k]; });
          return api('POST', '/api/refine/works/' + id + '/correction', {
            expected_version: detail.item.version, refined: refined, from_draft: !!detail.views.draft.content,
            correction_reason: values.correction_reason, reviewer: values.reviewer
          }).then(function (d) { memo.tab = 'corrected'; render(host, d); window.FMForms.showToast(t('refine.corrected_version_saved_as_a_2')); });
        }
      });
    });

    var op = operatorName(detail);
    var canReview = !!detail.views.corrected.content;
    ['approved', 'rejected'].forEach(function (decision) {
      var btn = el('button', { type: 'button', className: 'fm-button',
        text: decision === 'approved' ? t('refine.approval_record') : t('refine.rejection_record') });
      btn.disabled = !canReview || (decision === 'approved' && !ready);
      btn.addEventListener('click', function () {
        window.FMForms.openForm({
          title: decision === 'approved' ? t('refine.approve_work') : t('refine.reject_work'),
          fields: [
            { name: 'notes', label: t('experts.decision_reason'), type: 'textarea', required: true },
            { name: 'reviewer', label: t('refine.operations_owner_name_the_designated'), type: 'text', required: true }
          ],
          onSubmit: function (values) {
            return api('POST', '/api/refine/works/' + id + '/validate', {
              decision: decision, notes: values.notes, reviewer: values.reviewer,
              expected_version: detail.item.version, input_hash: detail.input_hash, evidence_refs: []
            }).then(function (d) { render(host, d); window.FMForms.showToast(t('refine.decision_recorded')); });
          }
        });
      });
      bar.appendChild(btn);
    });

    var revBtn = el('button', { type: 'button', className: 'fm-button', text: t('refine.revise_original_text') });
    revBtn.addEventListener('click', function () {
      window.FMForms.openForm({
        title: t('refine.revise_original_text'),
        fields: [
          { name: 'body', label: t('refine.new_original_text'), type: 'textarea', required: true },
          { name: 'reason', label: t('collect.revision_reason'), type: 'textarea', required: true },
          { name: 'reviewer', label: t('collect.owner'), type: 'text', required: true }
        ],
        onSubmit: function (values) {
          return api('POST', '/api/refine/works/' + id + '/revise', {
            expected_version: detail.item.version, body: values.body, reason: values.reason, reviewer: values.reviewer
          }).then(function (d) { render(host, d); window.FMForms.showToast(t('refine.original_text_revised_the_current')); });
        }
      });
    });

    var opBtn = el('button', { type: 'button', className: 'fm-button', text: op ? t('refine.change_operations_owner') : t('refine.designate_operations_owner') });
    opBtn.addEventListener('click', function () {
      window.FMForms.openForm({
        title: t('refine.designated_operations_owner'), fields: [{ name: 'name', label: t('experts.name'), type: 'text', required: true }],
        onSubmit: function (values) {
          return api('POST', '/api/refine/operator', { name: values.name }).then(function () {
            return api('GET', '/api/refine/works/' + id).then(function (d) { render(host, d); });
          });
        }
      });
    });

    bar.insertBefore(draftBtn, bar.firstChild);
    bar.insertBefore(correctBtn, draftBtn.nextSibling);
    bar.appendChild(revBtn);
    bar.appendChild(opBtn);
    return bar;
  }

  function renderDetail(host, detail) {
    var item = detail.item;
    var stateKey = detail.review.state;
    var head = el('div', { className: 'refine-head' }, [
      el('h2', { text: item.title }), badge(item.synthetic),
      el('p', { className: 'refine-status', text: t('refine.review_status') + ' ' + (STATE_LABELS[stateKey] || stateKey) +
        ' ' + t('refine.work_version') + ' ' + item.version + ' ' + t('refine.designated_operations_owner_2') + ' ' + (operatorName(detail) || t('feedback.unspecified')) })
    ]);
    if (stateKey === 'review_required') {
      head.appendChild(el('p', { className: 'refine-warning', role: 'status',
        text: t('refine.the_original_text_or_review') }));
    }
    var back = el('button', { type: 'button', className: 'fm-button', text: t('collect.back_to_list') });
    back.addEventListener('click', function () { memo.workId = null; load(host); });

    host.replaceChildren(back, head, renderPrereq(detail.prereq));
    var tabs = renderTabs(detail, host);
    host.appendChild(tabs[0]);
    host.appendChild(tabs[1]);
    host.appendChild(actionBar(detail, host));
    host.appendChild(renderReviews(detail));
  }

  function render(host, detail) {
    memo.workId = detail.item.id;
    renderDetail(host, detail);
  }

  function renderList(host, data) {
    var items = data.items || [];
    var head = el('p', { text: t('refine.choose_work_to_refine_original') });
    if (!items.length) { host.replaceChildren(head, emptyBox(t('refine.no_work_to_refine'))); return; }
    var ul = el('ul', { className: 'refine-list' });
    items.forEach(function (it) {
      var btn = el('button', { type: 'button', className: 'fm-button refine-pick' }, [
        el('strong', { text: it.title }), badge(it.synthetic),
        el('span', { text: ' · ' + (STATE_LABELS[it.state] || it.state) + (it.prereq_ready ? '' : ' ' + t('refine.conditions_not_met')) })
      ]);
      btn.addEventListener('click', function () { openWork(host, it.id); });
      ul.appendChild(el('li', {}, [btn]));
    });
    host.replaceChildren(head, ul);
  }

  function openWork(host, id) {
    host.replaceChildren(emptyBox(t('refine.loading')));
    api('GET', '/api/refine/works/' + id).then(function (d) { render(host, d); })
      .catch(function (e) { host.replaceChildren(errorBox(e.userMessage || t('refine.could_not_load_the_work'))); });
  }

  function load(host) {
    host.replaceChildren(emptyBox(t('refine.loading')));
    api('GET', '/api/refine/works').then(function (data) {
      memo.list = data;
      renderList(host, data);
    }).catch(function (e) {
      host.replaceChildren(errorBox(e.userMessage || t('refine.could_not_load_the_refinement')));
    });
  }

  window.FMScreens.s5 = function (container) {
    if (!container) return;
    if (memo.workId) openWork(container, memo.workId);
    else load(container);
  };
})();
