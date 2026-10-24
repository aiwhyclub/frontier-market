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
    { key: 'apprenticeship', label: '도제 경험' },
    { key: 'signals', label: '관측 단서' },
    { key: 'judgment', label: '판단/예외' },
    { key: 'evidence', label: '결과/근거' }
  ];
  var DECISION_LABEL = { candidate: '지원(후보)', approved: '승인', supplement: '보완', rejected: '반려' };
  var DECISION_KEY = { '승인': 'approved', '보완': 'supplement', '반려': 'rejected' };
  var SYNTH = '합성';

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
          var msg = (data && data.error && data.error.message) || '요청을 처리하지 못했습니다. 다시 시도해 주세요.';
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
      e.userMessage = '서버에 연결하지 못했습니다. 연결 후 다시 시도해 주세요.';
      throw e;
    });
  }

  function yesNo(v) { return v === '예'; }

  function scoreInput(raw) {
    var v = String(raw == null ? '' : raw).trim();
    if (v === '') return null;               // 미입력: 0점과 구분
    return /^\d+$/.test(v) ? Number(v) : v;  // 서버가 0~3 정수 여부를 다시 검사한다
  }

  function renderList(container, showDetail) {
    container.textContent = '';
    var createBtn = el('button', { type: 'button', className: 'fm-button', text: '후보 등록' });
    createBtn.addEventListener('click', function () { openCreate(showDetail); });
    var status = el('p', { className: 'empty-state', text: '불러오는 중…', role: 'status' });
    var list = el('ul', { className: 's2-list' });
    container.appendChild(el('div', { className: 's2-head' }, [el('h2', { text: 'S3 전문가 후보' }), createBtn]));
    container.appendChild(status);
    container.appendChild(list);
    api('GET', '/api/records/experts').then(function (data) {
      var items = (data && data.items) || [];
      status.textContent = '';
      if (!items.length) {
        status.textContent = '아직 후보가 없습니다. 후보를 등록하면 여기에 나타납니다.';
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
      status.textContent = (err && err.userMessage) || '목록을 불러오지 못했습니다.';
    });
  }

  function openCreate(showDetail) {
    window.FMForms.openForm({
      title: '전문가 후보 등록',
      submitLabel: '등록',
      fields: [
        { name: 'name', label: '이름', type: 'text', required: true },
        { name: 'field', label: '분야', type: 'text', required: true },
        { name: 'experience', label: '경험', type: 'textarea', required: true },
        { name: 'apprenticeship', label: '관찰/실습/피드백 맥락', type: 'textarea', required: true },
        { name: 'participation_notes', label: '참여 메모', type: 'textarea', required: false },
        { name: 'synthetic', label: '합성 자료 여부 (예/아니오, 비우면 직접 입력)', type: 'text', required: false }
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
      title: '프로필 수정 (이전 내용은 이력으로 남습니다)',
      submitLabel: '저장',
      fields: [
        { name: 'name', label: '이름', type: 'text', required: true },
        { name: 'field', label: '분야', type: 'text', required: true },
        { name: 'experience', label: '경험', type: 'textarea', required: true },
        { name: 'apprenticeship', label: '관찰/실습/피드백 맥락', type: 'textarea', required: true },
        { name: 'participation_notes', label: '참여 메모', type: 'textarea', required: false },
        { name: 'provided_scope', label: '제공 범위 설명 (실제 이용 허용은 권리 기록으로 정합니다)', type: 'textarea', required: false }
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
      title: '4축 평가 · 지정 운영책임자 판정',
      submitLabel: '판정 저장',
      fields: AXES.map(function (a) {
        return { name: a.key, label: a.label + ' (0~3, 비우면 미입력)', type: 'number', required: true };
      }).concat([
        { name: 'decision', label: '판정 (승인 / 보완 / 반려)', type: 'text', required: true },
        { name: 'notes', label: '판정 사유', type: 'textarea', required: true },
        { name: 'reviewer', label: '평가자', type: 'text', required: true }
      ]),
      onSubmit: function (v) {
        var scores = {};
        AXES.forEach(function (a) { scores[a.key] = scoreInput(v[a.key]); });
        var key = DECISION_KEY[v.decision];
        if (!key) {
          var e = new Error('decision');
          e.userMessage = '판정은 승인, 보완, 반려 중 하나로 입력해 주세요.';
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
      title: '원시 작업 업로드 (카탈로그 없이 미연결 저장)',
      submitLabel: '업로드',
      fields: [
        { name: 'title', label: '제목', type: 'text', required: true },
        { name: 'raw_text', label: '원문', type: 'textarea', required: true },
        { name: 'evidence', label: '근거/출처 메모', type: 'textarea', required: false },
        { name: 'synthetic', label: '합성 자료 여부 (예/아니오)', type: 'text', required: false }
      ],
      onSubmit: function (v) {
        return api('POST', '/api/experts/' + encodeURIComponent(item.id) + '/uploads', {
          title: v.title, raw_text: v.raw_text, evidence: v.evidence || '', synthetic: yesNo(v.synthetic)
        }).then(function (data) {
          window.FMForms.showToast(data.guidance || '작업을 저장했습니다.');
          reload();
        });
      }
    });
  }

  // F10.3: recruiting post / experience summary / evaluation questions as an editable draft only.
  function aiSection(d, item, reload) {
    var sec = el('section', { className: 's2-section' }, [el('h3', { text: 'AI 초안 (모집문·경험 정리·평가 질문)' })]);
    var draft = d.ai_draft;
    if (!d.ai || !d.ai.adopted) {
      sec.appendChild(el('p', { className: 'empty-state',
        text: 'AI 초안 기능이 꺼져 있어요. 모집문과 평가 질문은 직접 작성하세요.' }));
    } else {
      var reqBtn = el('button', { type: 'button', className: 'fm-button', text: 'AI 초안 요청' });
      reqBtn.addEventListener('click', function () {
        api('POST', '/api/experts/' + encodeURIComponent(item.id) + '/ai-draft', { expected_version: item.version })
          .then(function (res) {
            if (res.ai_result && !res.ai_result.ok) window.alert('AI 초안을 받지 못했어요(' + res.ai_result.error + '). 판정은 그대로예요.');
            reload();
          }, function (err) { window.alert((err && err.userMessage) || '요청하지 못했어요.'); });
      });
      sec.appendChild(reqBtn);
    }
    if (!draft) return sec;
    var c = draft.content;
    sec.appendChild(el('p', { className: 's2-badge', text: draft.label + ' · ' + (draft.status === 'edited' ? '수정됨' : '초안') }));
    sec.appendChild(el('p', { className: 'fm-error', role: 'note', text: draft.notice }));
    var post = el('textarea', { className: 'fm-input', rows: '4', 'aria-label': '모집문 초안' });
    var summary = el('textarea', { className: 'fm-input', rows: '3', 'aria-label': '경험 정리 초안' });
    var questions = el('textarea', { className: 'fm-input', rows: '5', 'aria-label': '평가 질문 초안 (한 줄에 하나)' });
    post.value = c.recruiting_post; summary.value = c.experience_summary; questions.value = c.evaluation_questions.join('\n');
    var save = el('button', { type: 'button', className: 'fm-button', text: '초안 수정 저장 (발송 아님)' });
    save.addEventListener('click', function () {
      api('PATCH', '/api/experts/' + encodeURIComponent(item.id) + '/ai-draft', {
        expected_version: item.version, recruiting_post: post.value, experience_summary: summary.value,
        evaluation_questions: questions.value.split('\n').map(function (q) { return q.trim(); }).filter(Boolean)
      }).then(reload, function (err) { window.alert((err && err.userMessage) || '저장하지 못했어요.'); });
    });
    [['모집문 초안', post], ['경험 정리 초안', summary], ['평가 질문 초안 (한 줄에 하나)', questions]].forEach(function (p) {
      sec.appendChild(el('label', { className: 'fm-label' }, [el('span', { text: p[0] }), p[1]]));
    });
    sec.appendChild(save);
    return sec;
  }

  function renderDetail(container, id, showList) {
    container.textContent = '';
    var back = el('button', { type: 'button', className: 'fm-button', text: '목록으로' });
    back.addEventListener('click', showList);
    var status = el('p', { className: 'empty-state', text: '불러오는 중…', role: 'status' });
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
        ? '4축 합계(서버 계산): ' + verdict.evaluation.score + ' / 12 · 평가자 ' + verdict.evaluation.reviewer
        : '아직 평가가 없습니다. 점수만으로 승인되지 않습니다.';
      container.appendChild(el('section', { className: 's2-section' }, [
        el('h3', { text: '현재 판정' }),
        el('p', { text: '판정: ' + (DECISION_LABEL[verdict.status] || verdict.status) }),
        el('p', { text: evalText }),
        el('p', { className: 'empty-state', text: d.next_action })
      ]));

      if (d.reevaluation_required) {
        container.appendChild(el('p', { className: 'fm-error', role: 'status',
          text: '재평가 필요: ' + (item.reevaluation_reason || '경험 또는 제공 범위가 바뀌었습니다.') }));
      }

      container.appendChild(el('dl', { className: 's2-section' }, [
        el('dt', { text: '분야' }), el('dd', { text: item.field || '' }),
        el('dt', { text: '경험' }), el('dd', { text: item.experience || '' }),
        el('dt', { text: '관찰/실습/피드백 맥락' }), el('dd', { text: item.apprenticeship || '' }),
        el('dt', { text: '참여 메모' }), el('dd', { text: item.participation_notes || '없음' }),
        el('dt', { text: '제공 범위 설명' }), el('dd', { text: item.provided_scope || '없음' })
      ]));

      var editBtn = el('button', { type: 'button', className: 'fm-button', text: '프로필 수정' });
      editBtn.addEventListener('click', function () { openEdit(item, reload); });
      var evalBtn = el('button', { type: 'button', className: 'fm-button', text: '평가·판정 기록' });
      evalBtn.addEventListener('click', function () { openEvaluate(d, reload); });
      var upBtn = el('button', { type: 'button', className: 'fm-button', text: '원시 작업 업로드' });
      upBtn.disabled = !d.upload_allowed;
      upBtn.addEventListener('click', function () { openUpload(item, reload); });
      container.appendChild(el('div', { className: 's2-actions' }, [editBtn, evalBtn, upBtn]));
      if (!d.upload_allowed) {
        container.appendChild(el('p', { className: 'empty-state',
          text: '승인된 전문가만 원시 작업을 업로드할 수 있습니다. 현재 판정을 확인하고 평가를 기록하세요.' }));
      }

      container.appendChild(aiSection(d, item, reload));

      var past = el('ol', { className: 's2-list' });
      (d.past_evaluations || []).forEach(function (ev) {
        past.appendChild(el('li', { text: (DECISION_LABEL[ev.decision] || ev.decision) + ' · 합계 ' + ev.score +
          ' · 사유: ' + ev.notes + ' · 평가자: ' + ev.reviewer + ' · ' + ev.evaluated_at }));
      });
      container.appendChild(el('section', { className: 's2-section' }, [
        el('h3', { text: '과거 평가' }),
        past.childNodes.length ? past : el('p', { className: 'empty-state', text: '과거 평가가 없습니다.' })
      ]));

      var works = el('ul', { className: 's2-list' });
      (d.works || []).forEach(function (w) {
        works.appendChild(el('li', { text: w.title + ' · ' + w.status + ' (카탈로그 미연결, S4에서 연결)' }));
      });
      container.appendChild(el('section', { className: 's2-section' }, [
        el('h3', { text: '업로드된 작업' }),
        works.childNodes.length ? works : el('p', { className: 'empty-state', text: '업로드된 작업이 없습니다.' })
      ]));
    }).catch(function (err) {
      status.textContent = (err && err.status === 404) ? '전문가 후보를 찾을 수 없습니다.'
        : ((err && err.userMessage) || '상세를 불러오지 못했습니다.');
    });
  }

  window.FMScreens.s3 = function (container) {
    function showList() { renderList(container, showDetail); }
    function showDetail(id) { renderDetail(container, id, showList); }
    showList();
  };
})();
