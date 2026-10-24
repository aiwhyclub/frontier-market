/* S5 정제·검증 (F5.1~F5.6). Renders into the screen container; server text is set with textContent only. */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var STATE_LABELS = {
    no_correction: '교정본 없음',
    pending: '검토 대기',
    approved: '승인',
    rejected: '반려',
    review_required: '재검토 필요'
  };
  var SECTION_KEYS = ['context', 'observation', 'novice_errors', 'judgement', 'outcome_check', 'criteria', 'questions'];
  var SECTION_LABELS = {
    context: '맥락', observation: '관찰 사실', novice_errors: '초보자 오류', judgement: '판단·예외',
    outcome_check: '결과 확인', criteria: '평가 기준', questions: '추가 질문'
  };
  var VIEW_TABS = [
    { key: 'original', label: '원문' },
    { key: 'draft', label: '초안' },
    { key: 'corrected', label: '교정본' },
    { key: 'evidence', label: '근거' }
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
    return synthetic ? el('span', { className: 'fm-badge', text: '합성' }) : null;
  }

  function api(method, url, body) {
    var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (data) {
        if (!r.ok) {
          var msg = (data && data.error && data.error.message) || '요청을 처리하지 못했어요. 입력한 내용은 그대로 있어요.';
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
      item.appendChild(el('strong', { text: (c.ok ? '충족 · ' : '부족 · ') + c.label }));
      if (!c.ok) {
        item.appendChild(el('p', { text: c.reason || '' }));
        item.appendChild(el('a', { href: c.fix_link, text: c.fix_label }));
      }
      list.appendChild(item);
    });
    return el('section', { className: 'refine-section', 'aria-label': '정제 전 조건' }, [
      el('h3', { text: prereq.ready ? '정제 조건 충족' : '정제 전 조건 확인 (부족한 항목을 먼저 보완하세요)' }),
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
    panel.appendChild(renderView(detail, memo.tab));
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

  function renderView(detail, key) {
    var v = detail.views[key];
    var wrap = el('div', {});
    wrap.appendChild(el('p', { className: 'refine-method', text: '생성 방식: ' + v.method_label }));
    if (key === 'original') {
      wrap.appendChild(el('h4', { text: v.title }));
      wrap.appendChild(el('p', { text: v.body || '원문이 비어 있어요.' }));
      (v.materials || []).forEach(function (m) {
        wrap.appendChild(el('div', { className: 'refine-material' }, [
          el('strong', { text: '연결 자료: ' + m.title }), badge(m.synthetic), el('p', { text: m.body || '' })
        ]));
      });
    } else if (key === 'draft') {
      if (!v.content) wrap.appendChild(emptyBox('아직 초안이 없어요. "초안 만들기"를 누르면 규칙으로 구성해요.'));
      else wrap.appendChild(sectionList(v.content));
    } else if (key === 'corrected') {
      if (!v.content) wrap.appendChild(emptyBox('아직 교정본이 없어요. 초안을 바탕으로 교정해 저장하세요.'));
      else {
        wrap.appendChild(sectionList(v.content));
        wrap.appendChild(el('p', { className: 'refine-meta',
          text: '교정 사유: ' + (v.reason || '') + ' · 담당자: ' + (v.reviewer || '') + ' · 시각: ' + (v.at || '') }));
      }
    } else if (key === 'evidence') {
      if (!v.items || !v.items.length) wrap.appendChild(emptyBox('등록된 근거가 없어요.'));
      else {
        var ul = el('ul', {});
        v.items.forEach(function (e) { ul.appendChild(el('li', { text: typeof e === 'string' ? e : JSON.stringify(e) })); });
        wrap.appendChild(ul);
      }
      wrap.appendChild(el('p', { className: 'refine-meta', text: '연결 자료 ' + (v.materials || []).length + '건' }));
    }
    return wrap;
  }

  function renderReviews(detail) {
    var box = el('section', { className: 'refine-section', 'aria-label': '승인 이력' });
    box.appendChild(el('h3', { text: '판정 이력 (과거 판정은 보존됩니다)' }));
    if (!detail.review.reviews.length) {
      box.appendChild(emptyBox('아직 승인·반려 기록이 없어요.'));
    } else {
      var ul = el('ul', { className: 'refine-history' });
      detail.review.reviews.forEach(function (r) {
        ul.appendChild(el('li', {}, [
          el('strong', { text: (r.decision === 'approved' ? '승인' : '반려') + (r.current_input ? ' · 현재 입력' : ' · 이전 입력') }),
          el('p', { text: '사유: ' + r.notes }),
          el('p', { text: '담당자: ' + r.reviewer + ' · 시각: ' + r.created_at + ' · 작업 버전 ' + r.work_version }),
          el('p', { className: 'refine-hash', text: '입력 해시: ' + r.input_hash.slice(0, 12) + '…' })
        ]));
      });
      box.appendChild(ul);
    }
    if (detail.revisions.length) {
      var rv = el('ul', { className: 'refine-history' });
      detail.revisions.forEach(function (r) {
        rv.appendChild(el('li', { text: '원문 개정 v' + r.from_version + ' → v' + r.to_version + ' · ' + r.reason + ' · ' + r.reviewer }));
      });
      box.appendChild(el('h4', { text: '원문 개정 이력' }));
      box.appendChild(rv);
    }
    return box;
  }

  function actionBar(detail, host) {
    var bar = el('div', { className: 'refine-actions' });
    var id = detail.item.id;
    var ready = detail.prereq.ready;

    var draftBtn = el('button', { type: 'button', className: 'fm-button', text: '초안 만들기 (규칙, AI 아님)' });
    draftBtn.disabled = !ready;
    draftBtn.addEventListener('click', function () {
      api('POST', '/api/refine/works/' + id + '/draft', { expected_version: detail.item.version })
        .then(function (d) { memo.tab = 'draft'; render(host, d); })
        .catch(function (e) { window.alert(e.userMessage); });
    });

    var correctBtn = el('button', { type: 'button', className: 'fm-button', text: '교정 저장 (새 버전)' });
    correctBtn.disabled = !ready;
    correctBtn.addEventListener('click', function () {
      var fields = SECTION_KEYS.map(function (k) { return { name: k, label: SECTION_LABELS[k], type: 'textarea', required: false }; });
      fields.push({ name: 'correction_reason', label: '교정 사유', type: 'textarea', required: true });
      fields.push({ name: 'reviewer', label: '담당자', type: 'text', required: true });
      window.FMForms.openForm({
        title: '정제 교정', fields: fields,
        onSubmit: function (values) {
          var refined = {};
          SECTION_KEYS.forEach(function (k) { if (values[k]) refined[k] = values[k]; });
          return api('POST', '/api/refine/works/' + id + '/correction', {
            expected_version: detail.item.version, refined: refined, from_draft: !!detail.views.draft.content,
            correction_reason: values.correction_reason, reviewer: values.reviewer
          }).then(function (d) { memo.tab = 'corrected'; render(host, d); window.FMForms.showToast('교정본을 새 버전으로 저장했어요.'); });
        }
      });
    });

    var op = operatorName(detail);
    var canReview = !!detail.views.corrected.content;
    ['approved', 'rejected'].forEach(function (decision) {
      var btn = el('button', { type: 'button', className: 'fm-button',
        text: decision === 'approved' ? '승인 기록' : '반려 기록' });
      btn.disabled = !canReview || (decision === 'approved' && !ready);
      btn.addEventListener('click', function () {
        window.FMForms.openForm({
          title: decision === 'approved' ? '작업 승인' : '작업 반려',
          fields: [
            { name: 'notes', label: '판정 사유', type: 'textarea', required: true },
            { name: 'reviewer', label: '운영책임자 이름 (지정된 사람)', type: 'text', required: true }
          ],
          onSubmit: function (values) {
            return api('POST', '/api/refine/works/' + id + '/validate', {
              decision: decision, notes: values.notes, reviewer: values.reviewer,
              expected_version: detail.item.version, input_hash: detail.input_hash, evidence_refs: []
            }).then(function (d) { render(host, d); window.FMForms.showToast('판정을 기록했어요.'); });
          }
        });
      });
      bar.appendChild(btn);
    });

    var revBtn = el('button', { type: 'button', className: 'fm-button', text: '원문 개정' });
    revBtn.addEventListener('click', function () {
      window.FMForms.openForm({
        title: '원문 개정',
        fields: [
          { name: 'body', label: '새 원문', type: 'textarea', required: true },
          { name: 'reason', label: '개정 사유', type: 'textarea', required: true },
          { name: 'reviewer', label: '담당자', type: 'text', required: true }
        ],
        onSubmit: function (values) {
          return api('POST', '/api/refine/works/' + id + '/revise', {
            expected_version: detail.item.version, body: values.body, reason: values.reason, reviewer: values.reviewer
          }).then(function (d) { render(host, d); window.FMForms.showToast('원문을 개정했어요. 현재 검토는 재검토가 필요해요.'); });
        }
      });
    });

    var opBtn = el('button', { type: 'button', className: 'fm-button', text: op ? '운영책임자 변경' : '운영책임자 지정' });
    opBtn.addEventListener('click', function () {
      window.FMForms.openForm({
        title: '지정 운영책임자', fields: [{ name: 'name', label: '이름', type: 'text', required: true }],
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
      el('p', { className: 'refine-status', text: '검토 상태: ' + (STATE_LABELS[stateKey] || stateKey) +
        ' · 작업 버전 ' + item.version + ' · 지정 운영책임자: ' + (operatorName(detail) || '미지정') })
    ]);
    if (stateKey === 'review_required') {
      head.appendChild(el('p', { className: 'refine-warning', role: 'status',
        text: '원문 또는 검토 입력이 바뀌었어요. 이전 승인은 이력으로 남아 있고, 현재 입력에 대한 새 검토가 필요해요.' }));
    }
    var back = el('button', { type: 'button', className: 'fm-button', text: '목록으로' });
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
    var head = el('p', { text: '정제할 작업을 고르세요. 원문·전문가·카탈로그 조건을 먼저 확인합니다.' });
    if (!items.length) { host.replaceChildren(head, emptyBox('정제할 작업이 없어요.')); return; }
    var ul = el('ul', { className: 'refine-list' });
    items.forEach(function (it) {
      var btn = el('button', { type: 'button', className: 'fm-button refine-pick' }, [
        el('strong', { text: it.title }), badge(it.synthetic),
        el('span', { text: ' · ' + (STATE_LABELS[it.state] || it.state) + (it.prereq_ready ? '' : ' · 조건 부족') })
      ]);
      btn.addEventListener('click', function () { openWork(host, it.id); });
      ul.appendChild(el('li', {}, [btn]));
    });
    host.replaceChildren(head, ul);
  }

  function openWork(host, id) {
    host.replaceChildren(emptyBox('불러오는 중이에요…'));
    api('GET', '/api/refine/works/' + id).then(function (d) { render(host, d); })
      .catch(function (e) { host.replaceChildren(errorBox(e.userMessage || '작업을 불러오지 못했어요.')); });
  }

  function load(host) {
    host.replaceChildren(emptyBox('불러오는 중이에요…'));
    api('GET', '/api/refine/works').then(function (data) {
      memo.list = data;
      renderList(host, data);
    }).catch(function (e) {
      host.replaceChildren(errorBox(e.userMessage || '정제 목록을 불러오지 못했어요. 잠시 후 다시 시도하세요.'));
    });
  }

  window.FMScreens.s5 = function (container) {
    if (!container) return;
    if (memo.workId) openWork(container, memo.workId);
    else load(container);
  };
})();
