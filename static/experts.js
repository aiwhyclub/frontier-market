/*
 * experts.js — S3 전문가: 후보 등록·프로필 수정·4축 평가·판정·원시 작업 업로드 (EPIC 4)
 *
 * window.FMScreens.s3(container)
 * API: GET /api/records/experts | POST /api/experts | GET/PATCH /api/experts/<id>
 *      POST /api/experts/<id>/evaluate | POST /api/experts/<id>/uploads
 * 규칙: 서버 문자열은 textContent로만 넣는다. 미입력(빈 값)과 0점을 구분한다. 합계는 서버 값만 표시한다.
 */
(function () {
  'use strict';

  window.FMScreens = window.FMScreens || {};

  var AXES = [
    { key: 'apprenticeship', label: t('experts.apprenticeship_experience') },
    { key: 'signals', label: t('experts.observation_cues') },
    { key: 'judgment', label: t('experts.judgement_exceptions') },
    { key: 'evidence', label: t('experts.result_evidence') }
  ];
  var DECISION_LABEL = { candidate: t('experts.applied_candidate'), approved: t('experts.approved'), supplement: t('experts.needs_supplement'), rejected: t('experts.rejected') };
  // The decision field accepts the English codes, the current-language labels and the original Korean words.
  var DECISION_KEY = { approved: 'approved', supplement: 'supplement', rejected: 'rejected', '승인': 'approved', '보완': 'supplement', '반려': 'rejected' };
  DECISION_KEY[t('experts.approved')] = 'approved';
  DECISION_KEY[t('experts.needs_supplement')] = 'supplement';
  DECISION_KEY[t('experts.rejected')] = 'rejected';
  var SYNTH = t('catalog.synthetic');

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

  function badge() { return el('span', { className: 's2-badge', text: SYNTH }); }

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
          var msg = (data && data.error && data.error.message) || t('experts.the_request_could_not_be');
          var fields = data && data.error && data.error.fields;
          if (fields) msg += ' ' + Object.keys(fields).map(function (k) { return k + ': ' + fields[k]; }).join(' / ');
          err.userMessage = msg;
          err.status = res.status;
          throw err;
        }
        return data;
      });
    }, function () {
      var e = new Error('network');
      e.userMessage = t('experts.could_not_reach_the_server');
      throw e;
    });
  }

  function yesNo(v) {
    var s = String(v == null ? '' : v).trim().toLowerCase();
    return s === '예' || s === 'yes' || s === 'y' || s === 'true' || s === t('experts.yes').toLowerCase();
  }

  function scoreInput(raw) {
    var v = String(raw == null ? '' : raw).trim();
    if (v === '') return null;               // 미입력: 0점과 구분
    return /^\d+$/.test(v) ? Number(v) : v;  // 서버가 0~3 정수 여부를 다시 검사한다
  }

  function renderList(container, showDetail) {
    container.textContent = '';
    var createBtn = el('button', { type: 'button', className: 'fm-button', text: t('experts.register_candidate') });
    createBtn.addEventListener('click', function () { openCreate(showDetail); });
    var status = el('p', { className: 'empty-state', text: t('demo.loading'), role: 'status' });
    var list = el('ul', { className: 's2-list' });
    container.appendChild(el('div', { className: 's2-head' }, [el('h2', { text: t('experts.s3_expert_candidates') }), createBtn]));
    container.appendChild(status);
    container.appendChild(list);
    api('GET', '/api/records/experts').then(function (data) {
      var items = (data && data.items) || [];
      status.textContent = '';
      if (!items.length) {
        status.textContent = t('experts.no_candidates_yet_registered_candidates');
        return;
      }
      items.forEach(function (it) {
        var btn = el('button', { type: 'button', className: 's2-row-button' }, [
          el('span', { text: it.title }),
          el('span', { className: 's2-meta', text: DECISION_LABEL[it.status] || it.status }),
          it.synthetic ? badge() : null
        ]);
        btn.addEventListener('click', function () { showDetail(it.id); });
        list.appendChild(el('li', { className: 's2-row' }, [btn]));
      });
    }).catch(function (err) {
      status.textContent = (err && err.userMessage) || t('catalog.could_not_load_the_list');
    });
  }

  function openCreate(showDetail) {
    window.FMForms.openForm({
      title: t('experts.register_expert_candidate'),
      submitLabel: t('experts.register'),
      fields: [
        { name: 'name', label: t('experts.name'), type: 'text', required: true },
        { name: 'field', label: t('catalog.domain'), type: 'text', required: true },
        { name: 'experience', label: t('experts.experience'), type: 'textarea', required: true },
        { name: 'apprenticeship', label: t('experts.observation_practice_feedback_context'), type: 'textarea', required: true },
        { name: 'participation_notes', label: t('experts.participation_notes'), type: 'textarea', required: false },
        { name: 'synthetic', label: t('experts.synthetic_material_yes_no_leave'), type: 'text', required: false }
      ],
      onSubmit: function (v) {
        return api('POST', '/api/experts', {
          name: v.name, field: v.field, experience: v.experience, apprenticeship: v.apprenticeship,
          participation_notes: v.participation_notes || '', synthetic: yesNo(v.synthetic)
        }).then(function (data) { showDetail(data.item.id); });
      }
    });
  }

  function openEdit(item, reload) {
    window.FMForms.openForm({
      title: t('experts.edit_profile_previous_content_is'),
      submitLabel: t('experts.save'),
      fields: [
        { name: 'name', label: t('experts.name'), type: 'text', required: true },
        { name: 'field', label: t('catalog.domain'), type: 'text', required: true },
        { name: 'experience', label: t('experts.experience'), type: 'textarea', required: true },
        { name: 'apprenticeship', label: t('experts.observation_practice_feedback_context'), type: 'textarea', required: true },
        { name: 'participation_notes', label: t('experts.participation_notes'), type: 'textarea', required: false },
        { name: 'provided_scope', label: t('experts.scope_of_what_is_provided'), type: 'textarea', required: false }
      ],
      onSubmit: function (v) {
        return api('PATCH', '/api/experts/' + encodeURIComponent(item.id), {
          expected_version: item.version, name: v.name, field: v.field, experience: v.experience,
          apprenticeship: v.apprenticeship, participation_notes: v.participation_notes || '',
          provided_scope: v.provided_scope || ''
        }).then(function () { reload(); });
      }
    });
  }

  function openEvaluate(detail, reload) {
    var item = detail.item;
    window.FMForms.openForm({
      title: t('experts.four_axis_evaluation_decision_by'),
      submitLabel: t('experts.save_decision'),
      fields: AXES.map(function (a) {
        return { name: a.key, label: a.label + ' ' + t('experts.0_3_blank_not_entered'), type: 'number', required: true };
      }).concat([
        { name: 'decision', label: t('experts.decision_approved_supplement_rejected'), type: 'text', required: true },
        { name: 'notes', label: t('experts.decision_reason'), type: 'textarea', required: true },
        { name: 'reviewer', label: t('experts.evaluator'), type: 'text', required: true }
      ]),
      onSubmit: function (v) {
        var scores = {};
        AXES.forEach(function (a) { scores[a.key] = scoreInput(v[a.key]); });
        var key = DECISION_KEY[v.decision];
        if (!key) {
          var e = new Error('decision');
          e.userMessage = t('experts.enter_the_decision_as_one');
          throw e;
        }
        return api('POST', '/api/experts/' + encodeURIComponent(item.id) + '/evaluate', {
          expected_version: item.version, decision: key, scores: scores, notes: v.notes, reviewer: v.reviewer
        }).then(function () { reload(); });
      }
    });
  }

  function openUpload(item, reload) {
    window.FMForms.openForm({
      title: t('experts.upload_raw_work_saved_unlinked'),
      submitLabel: t('experts.upload'),
      fields: [
        { name: 'title', label: t('collect.title'), type: 'text', required: true },
        { name: 'raw_text', label: t('collect.original_text'), type: 'textarea', required: true },
        { name: 'evidence', label: t('experts.evidence_source_notes'), type: 'textarea', required: false },
        { name: 'synthetic', label: t('experts.synthetic_material_yes_no'), type: 'text', required: false }
      ],
      onSubmit: function (v) {
        return api('POST', '/api/experts/' + encodeURIComponent(item.id) + '/uploads', {
          title: v.title, raw_text: v.raw_text, evidence: v.evidence || '', synthetic: yesNo(v.synthetic)
        }).then(function (data) {
          window.FMForms.showToast(data.guidance || t('experts.work_saved'));
          reload();
        });
      }
    });
  }

  // F10.3: recruiting post / experience summary / evaluation questions as an editable draft only.
  function aiSection(d, item, reload) {
    var sec = el('section', { className: 's2-section' }, [el('h3', { text: t('experts.ai_drafts_recruiting_post_experience') })]);
    var draft = d.ai_draft;
    if (!d.ai || !d.ai.adopted) {
      sec.appendChild(el('p', { className: 'empty-state',
        text: t('experts.ai_drafts_are_turned_off') }));
    } else {
      var reqBtn = el('button', { type: 'button', className: 'fm-button', text: t('experts.request_ai_draft') });
      reqBtn.addEventListener('click', function () {
        api('POST', '/api/experts/' + encodeURIComponent(item.id) + '/ai-draft', { expected_version: item.version })
          .then(function (res) {
            if (res.ai_result && !res.ai_result.ok) window.alert(t('catalog.could_not_get_an_ai') + res.ai_result.error + t('experts.the_decision_is_unchanged'));
            reload();
          }, function (err) { window.alert((err && err.userMessage) || t('experts.could_not_make_the_request')); });
      });
      sec.appendChild(reqBtn);
    }
    if (!draft) return sec;
    var c = draft.content;
    sec.appendChild(el('p', { className: 's2-badge', text: draft.label + ' · ' + (draft.status === 'edited' ? t('experts.edited') : t('catalog.draft')) }));
    sec.appendChild(el('p', { className: 'fm-error', role: 'note', text: draft.notice }));
    var post = el('textarea', { className: 'fm-input', rows: '4', 'aria-label': t('experts.recruiting_post_draft') });
    var summary = el('textarea', { className: 'fm-input', rows: '3', 'aria-label': t('experts.experience_summary_draft') });
    var questions = el('textarea', { className: 'fm-input', rows: '5', 'aria-label': t('experts.evaluation_question_drafts_one_per') });
    post.value = c.recruiting_post; summary.value = c.experience_summary; questions.value = c.evaluation_questions.join('\n');
    var save = el('button', { type: 'button', className: 'fm-button', text: t('experts.save_draft_edits_not_sent') });
    save.addEventListener('click', function () {
      api('PATCH', '/api/experts/' + encodeURIComponent(item.id) + '/ai-draft', {
        expected_version: item.version, recruiting_post: post.value, experience_summary: summary.value,
        evaluation_questions: questions.value.split('\n').map(function (q) { return q.trim(); }).filter(Boolean)
      }).then(reload, function (err) { window.alert((err && err.userMessage) || t('experts.could_not_save')); });
    });
    [[t('experts.recruiting_post_draft'), post], [t('experts.experience_summary_draft'), summary], [t('experts.evaluation_question_drafts_one_per'), questions]].forEach(function (p) {
      sec.appendChild(el('label', { className: 'fm-label' }, [el('span', { text: p[0] }), p[1]]));
    });
    sec.appendChild(save);
    return sec;
  }

  function renderDetail(container, id, showList) {
    container.textContent = '';
    var back = el('button', { type: 'button', className: 'fm-button', text: t('collect.back_to_list') });
    back.addEventListener('click', showList);
    var status = el('p', { className: 'empty-state', text: t('demo.loading'), role: 'status' });
    container.appendChild(back);
    container.appendChild(status);

    function reload() { renderDetail(container, id, showList); }

    api('GET', '/api/experts/' + encodeURIComponent(id)).then(function (d) {
      var item = d.item;
      container.textContent = '';
      container.appendChild(back);
      container.appendChild(el('div', { className: 's2-head' }, [
        el('h2', { text: item.title }), item.synthetic ? badge() : null
      ]));

      var verdict = d.current_verdict || {};
      var evalText = verdict.evaluation
        ? t('experts.four_axis_total_server_calculated') + ' ' + verdict.evaluation.score + ' ' + t('experts.12_evaluator') + ' ' + verdict.evaluation.reviewer
        : t('experts.no_evaluation_yet_a_score');
      container.appendChild(el('section', { className: 's2-section' }, [
        el('h3', { text: t('experts.current_decision') }),
        el('p', { text: t('experts.decision') + ' ' + (DECISION_LABEL[verdict.status] || verdict.status) }),
        el('p', { text: evalText }),
        el('p', { className: 'empty-state', text: d.next_action })
      ]));

      if (d.reevaluation_required) {
        container.appendChild(el('p', { className: 'fm-error', role: 'status',
          text: t('experts.re_evaluation_needed') + ' ' + (item.reevaluation_reason || t('experts.experience_or_scope_of_provision')) }));
      }

      container.appendChild(el('dl', { className: 's2-section' }, [
        el('dt', { text: t('catalog.domain') }), el('dd', { text: item.field || '' }),
        el('dt', { text: t('experts.experience') }), el('dd', { text: item.experience || '' }),
        el('dt', { text: t('experts.observation_practice_feedback_context') }), el('dd', { text: item.apprenticeship || '' }),
        el('dt', { text: t('experts.participation_notes') }), el('dd', { text: item.participation_notes || t('collect.none') }),
        el('dt', { text: t('experts.scope_of_what_is_provided_2') }), el('dd', { text: item.provided_scope || t('collect.none') })
      ]));

      var editBtn = el('button', { type: 'button', className: 'fm-button', text: t('experts.edit_profile') });
      editBtn.addEventListener('click', function () { openEdit(item, reload); });
      var evalBtn = el('button', { type: 'button', className: 'fm-button', text: t('experts.evaluation_and_decision_record') });
      evalBtn.addEventListener('click', function () { openEvaluate(d, reload); });
      var upBtn = el('button', { type: 'button', className: 'fm-button', text: t('experts.upload_raw_work') });
      upBtn.disabled = !d.upload_allowed;
      upBtn.addEventListener('click', function () { openUpload(item, reload); });
      container.appendChild(el('div', { className: 's2-actions' }, [editBtn, evalBtn, upBtn]));
      if (!d.upload_allowed) {
        container.appendChild(el('p', { className: 'empty-state',
          text: t('experts.only_approved_experts_can_upload') }));
      }

      container.appendChild(aiSection(d, item, reload));

      var past = el('ol', { className: 's2-list' });
      (d.past_evaluations || []).forEach(function (ev) {
        past.appendChild(el('li', { text: (DECISION_LABEL[ev.decision] || ev.decision) + ' ' + t('experts.total') + ' ' + ev.score +
          ' ' + t('collect.reason_2') + ' ' + ev.notes + ' ' + t('experts.evaluator_2') + ' ' + ev.reviewer + ' · ' + ev.evaluated_at }));
      });
      container.appendChild(el('section', { className: 's2-section' }, [
        el('h3', { text: t('experts.past_evaluations') }),
        past.childNodes.length ? past : el('p', { className: 'empty-state', text: t('experts.no_past_evaluations') })
      ]));

      var works = el('ul', { className: 's2-list' });
      (d.works || []).forEach(function (w) {
        works.appendChild(el('li', { text: w.title + ' · ' + w.status + ' ' + t('experts.not_linked_to_a_catalog') }));
      });
      container.appendChild(el('section', { className: 's2-section' }, [
        el('h3', { text: t('experts.uploaded_work') }),
        works.childNodes.length ? works : el('p', { className: 'empty-state', text: t('experts.no_work_uploaded') })
      ]));
    }).catch(function (err) {
      status.textContent = (err && err.status === 404) ? t('experts.expert_candidate_not_found')
        : ((err && err.userMessage) || t('demo.could_not_load_the_details'));
    });
  }

  window.FMScreens.s3 = function (container) {
    function showList() { renderList(container, showDetail); }
    function showDetail(id) { renderDetail(container, id, showList); }
    showList();
  };
})();
