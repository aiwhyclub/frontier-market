/* S4 판단 과제 카탈로그 (F4.1~F4.5). 모든 서버 텍스트는 textContent로만 표시한다. */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var STATUS_LABEL = { draft: '초안', ready: '준비', hold: '보류' };
  var AXIS_HINT = '근거를 적거나, 근거가 없으면 "미확인"이라고 적으세요. 비워 두면 미입력으로 표시됩니다.';

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
  function synthBadge(item) { return item && item.synthetic ? badge('합성', 'fm-badge-synthetic') : null; }

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
          err.userMessage = e.message || '저장하지 못했습니다. 입력을 보존했으니 다시 시도해 주세요.';
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
    { name: 'title', label: '제목 (판단 과제명)', type: 'text', required: true },
    { name: 'domain', label: '분야', type: 'text', required: true },
    { name: 'summary', label: '요약', type: 'textarea', required: true },
    { name: 'judgement_task', label: '구매할 판단 과제', type: 'textarea', required: true },
    { name: 'target_capability', label: '대상 능력', type: 'text', required: true },
    { name: 'expected_deliverables', label: '예상 납품물', type: 'textarea', required: true },
    { name: 'apprentice_context', label: '도제 학습 맥락', type: 'textarea' },
    { name: 'tacit_signals', label: '숙련 단서 (줄마다 하나)', type: 'textarea' },
    { name: 'novice_gap', label: '초보자 차이', type: 'textarea' },
    { name: 'evidence_refs', label: '근거 참조 (줄마다 하나)', type: 'textarea' },
    { name: 'media_refs', label: '매체 참조 (줄마다 "주소 | 시점 | 설명")', type: 'textarea' }
  ];
  var AXIS_FIELDS = [
    { name: 'origin', label: '도제 경험 기원', type: 'textarea' },
    { name: 'documentation_gap', label: '비표준/문서화 공백', type: 'textarea' },
    { name: 'field_need', label: '현장 기본 필요성/오판 비용', type: 'textarea' },
    { name: 'novice_difference', label: '숙련자-초보자 차이', type: 'textarea' },
    { name: 'outcome_verifiability', label: '결과 검증 가능성', type: 'textarea' },
    { name: 'lab_link', label: 'Lab 평가 능력 연결', type: 'textarea' }
  ];
  var EDIT_FIELDS = CREATE_FIELDS.map(function (f) {
    return { name: f.name, label: f.label, type: f.type, required: false };
  });
  var LINK_FIELDS = [
    { name: 'source_ids', label: '자료 ID (쉼표로 구분)', type: 'text' },
    { name: 'expert_ids', label: '승인 전문가 ID (쉼표로 구분)', type: 'text' },
    { name: 'work_ids', label: '원시 작업 ID (쉼표로 구분)', type: 'text' }
  ];

  function ids(v) { return v ? v.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : []; }

  function listView(root, openCard) {
    root.textContent = '';
    root.appendChild(el('p', { className: 'fm-note', text: '로딩 중…' }));
    api('GET', '/api/catalog/').then(function (data) {
      root.textContent = '';
      var head = el('div', { className: 'fm-toolbar' }, [
        el('h2', { text: '판단 과제 카탈로그' }),
        el('button', { className: 'fm-btn fm-btn-primary', type: 'button', text: '새 과제 카드',
          onclick: function () { createCard(root, openCard); } }),
        el('button', { className: 'fm-btn', type: 'button', text: 'AI 초안 만들기', id: 'catalog-ai-open',
          onclick: function () { aiDraftPanel(root, openCard); } })
      ]);
      root.appendChild(head);
      root.appendChild(el('div', { id: 'catalog-ai-panel' }));
      if (!data.items || !data.items.length) {
        root.appendChild(el('p', { className: 'fm-empty', text: '아직 과제 카드가 없습니다. 새 과제 카드를 작성해 보세요.' }));
        return;
      }
      var grid = el('ul', { className: 'catalog-grid' });
      data.items.forEach(function (c) {
        var card = el('li', { className: 'catalog-card' }, [
          el('button', { className: 'catalog-card-open', type: 'button', onclick: function () { openCard(c.id); } }, [
            el('strong', { text: c.title }),
            el('div', {}, [
              badge(STATUS_LABEL[c.card_status] || c.card_status, 'catalog-status-' + c.card_status),
              badge(c.domain || '분야 미입력'),
              badge('발굴 근거 ' + c.evidence_axis_count + '/6'),
              synthBadge(c)
            ])
          ])
        ]);
        grid.appendChild(card);
      });
      root.appendChild(grid);
    }).catch(function (err) {
      root.textContent = '';
      root.appendChild(el('p', { className: 'fm-error', role: 'alert', text: err.userMessage || '목록을 불러오지 못했습니다.' }));
      root.appendChild(el('button', { className: 'fm-btn', type: 'button', text: '다시 시도', onclick: function () { listView(root, openCard); } }));
    });
  }

  var DRAFT_FIELDS = [
    ['title', '제목 (판단 과제명)', 'input'], ['domain', '분야', 'input'], ['summary', '요약', 'textarea'],
    ['judgement_task', '구매할 판단 과제', 'textarea'], ['target_capability', '대상 능력', 'input'],
    ['expected_deliverables', '예상 납품물', 'textarea']
  ];

  /* F10.4: AI가 자료를 근거로 만든 초안을 사람이 읽고 고쳐서 채택한다. 수동 작성이 기본 경로다. */
  function aiDraftPanel(root, openCard) {
    var box = root.querySelector('#catalog-ai-panel');
    box.textContent = '';
    var msg = el('p', { className: 'fm-note', role: 'status', text: 'AI 상태를 확인하는 중…' });
    var idInput = el('input', { id: 'catalog-ai-sources', className: 'fm-input', type: 'text', autocomplete: 'off' });
    var go = el('button', { className: 'fm-btn fm-btn-primary', type: 'button', text: '초안 만들기', disabled: '' });
    var result = el('div', { id: 'catalog-ai-result' });
    box.appendChild(el('section', { className: 'catalog-ai' }, [
      el('h3', { text: 'AI 초안 (선택)' }),
      el('p', { className: 'fm-note', text: 'AI 초안은 연결한 자료만 근거로 한 제안이며 저장되지 않습니다. 읽고 고친 뒤 채택해야 카드가 만들어지고, 준비 상태·권리·수요 승인은 따로 확인합니다.' }),
      msg,
      el('label', { for: 'catalog-ai-sources', text: '원천 자료 ID (쉼표로 구분)' }), idInput, go, result
    ]));
    api('GET', '/api/catalog/ai-status').then(function (st) {
      if (st.adopted) {
        msg.textContent = 'AI 사용 가능: ' + st.label + '. 수동 작성도 계속 쓸 수 있어요.';
        go.removeAttribute('disabled');
      } else {
        msg.textContent = 'AI를 채택하지 않았어요. 새 과제 카드로 직접 작성해 주세요.';
      }
    }).catch(function () { msg.textContent = 'AI 상태를 확인하지 못했어요. 직접 작성해 주세요.'; });
    go.addEventListener('click', function () {
      result.textContent = '';
      go.setAttribute('disabled', '');
      api('POST', '/api/catalog/ai-draft', { source_ids: ids(idInput.value) }).then(function (d) {
        if (!d.draft) {
          result.appendChild(el('p', { className: 'fm-error', role: 'alert',
            text: 'AI 초안을 받지 못했어요(' + (d.ai.error || '미채택') + '). 수동 작성으로 계속하세요.' }));
          return;
        }
        renderDraft(result, d, openCard);
      }).catch(function (err) {
        result.appendChild(el('p', { className: 'fm-error', role: 'alert', text: err.userMessage || '초안을 만들지 못했어요.' }));
      }).then(function () { go.removeAttribute('disabled'); });
    });
  }

  function renderDraft(result, d, openCard) {
    var inputs = {};
    var form = el('form', { className: 'catalog-ai-draft' }, [
      el('h4', { text: d.ai.label }),
      el('p', { className: 'fm-note', text: '원천 자료: ' + d.source_ids.join(', ') + ' · 아직 저장되지 않은 초안입니다.' })
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
    form.appendChild(el('button', { className: 'fm-btn fm-btn-primary', type: 'submit', text: '수정 내용으로 채택해 카드 만들기' }));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var body = { synthetic: false };
      Object.keys(inputs).forEach(function (k) { body[k] = inputs[k].value.trim(); });
      api('POST', '/api/catalog/ai-draft/' + encodeURIComponent(d.ai.run_id) + '/adopt', body).then(function (r) {
        openCard(r.item.id);
      }).catch(function (err) { status.textContent = err.userMessage || '채택하지 못했어요.'; });
    });
    result.textContent = '';
    result.appendChild(form);
  }

  function createCard(root, openCard) {
    window.FMForms.openForm({
      title: '새 판단 과제 카드',
      fields: CREATE_FIELDS.concat([{ name: 'synthetic', label: '합성 자료 표시 (true/false)', type: 'text' }]),
      onSubmit: function (values) {
        var body = fieldsOfForm(values, CREATE_FIELDS.map(function (f) { return f.name; }));
        ['tacit_signals', 'evidence_refs'].forEach(function (k) { if (body[k]) body[k] = lines(body[k]); });
        body.synthetic = String(values.synthetic || '').toLowerCase() === 'true';
        return api('POST', '/api/catalog/', body).then(function (data) {
          window.FMForms && window.FMForms.showToast && window.FMForms.showToast('과제 카드를 저장했습니다.');
          openCard(data.item.id);
        });
      }
    });
  }

  function detailView(root, cardId, goList) {
    root.textContent = '';
    root.appendChild(el('p', { className: 'fm-note', text: '로딩 중…' }));
    api('GET', '/api/catalog/' + encodeURIComponent(cardId)).then(function (d) {
      render(d);
    }).catch(function (err) {
      root.textContent = '';
      root.appendChild(el('p', { className: 'fm-error', role: 'alert', text: err.userMessage || '과제 카드를 불러오지 못했습니다.' }));
    });

    function render(d) {
      var item = d.item;
      root.textContent = '';
      root.appendChild(el('button', { className: 'fm-btn', type: 'button', text: '← 목록', onclick: goList }));
      root.appendChild(el('h2', { text: item.title }));
      root.appendChild(el('div', {}, [
        badge(STATUS_LABEL[d.card.status] || d.card.status, 'catalog-status-' + d.card.status), synthBadge(item)
      ]));
      root.appendChild(el('p', { className: 'fm-note',
        text: item.ai_draft ? item.ai_draft.label + ' — 사람이 검토·수정해 채택한 카드입니다.' : '수동 작성' }));
      root.appendChild(el('p', { className: 'fm-note', text: '준비 상태는 권리·수요 승인이 아닙니다. 관측 한계: ' + d.card.limits.join(' / ') }));

      function section(title, rows) {
        var dl = el('dl', { className: 'catalog-dl' });
        rows.forEach(function (r) { dl.appendChild(el('dt', { text: r[0] })); dl.appendChild(el('dd', { text: r[1] || '미입력' })); });
        root.appendChild(el('section', {}, [el('h3', { text: title }), dl]));
      }
      section('과제 카드', [['분야', item.domain], ['요약', item.summary], ['구매할 판단 과제', item.judgement_task],
        ['대상 능력', item.target_capability], ['예상 납품물', item.expected_deliverables]]);
      section('도제 단서', [['학습 맥락', item.apprentice_context], ['숙련 단서', (item.tacit_signals || []).join(' / ')],
        ['초보자 차이', item.novice_gap], ['근거', (item.evidence_refs || []).join(' / ')],
        ['매체 참조', (item.media_refs || []).map(function (m) { return m.address + ' | ' + m.captured_at + ' | ' + m.description; }).join('\n')]]);
      root.appendChild(el('section', {}, [el('h3', { text: '발굴 6축' }),
        el('ul', {}, d.axes.map(function (a) {
          return el('li', { text: a.label + ': ' + (a.status === 'evidence' ? a.note : (a.status === 'unknown' ? '미확인' : '미입력')) });
        }))]));
      if (d.card.hold_reasons.length) {
        root.appendChild(el('section', {}, [el('h3', { text: '보류 이유 / 준비 조건' }),
          el('ul', {}, d.card.hold_reasons.map(function (r) { return el('li', { text: r }); }))]));
      }
      var linkItems = [].concat(
        (d.links.sources || []).map(function (l) { return el('li', { text: '자료: ' + l.title }); }),
        (d.links.experts || []).map(function (l) { return el('li', { text: '전문가: ' + l.title }); }),
        (d.links.works || []).map(function (l) { return el('li', { text: '작업: ' + l.title }); }),
        (d.missing_links || []).map(function (m) { return el('li', { className: 'fm-note', text: '미연결 — ' + m.reason }); })
      );
      root.appendChild(el('section', {}, [el('h3', { text: '연결' }), el('ul', {}, linkItems)]));

      root.appendChild(el('div', { className: 'fm-toolbar' }, [
        el('button', { className: 'fm-btn', type: 'button', text: '카드 수정', onclick: function () { editCard(); } }),
        el('button', { className: 'fm-btn', type: 'button', text: '연결 저장', onclick: function () { linkCard(); } }),
        el('button', { className: 'fm-btn', type: 'button', text: '발굴 6축 기록', onclick: function () { axesCard(); } })
      ]));

      function editCard() {
        window.FMForms.openForm({
          title: '과제 카드 수정 (빈 칸은 바꾸지 않습니다)',
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
          title: '원천·전문가·작업 연결 (전체 목록으로 저장)',
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
          title: '발굴 6축 기록. ' + AXIS_HINT,
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
