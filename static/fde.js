/*
 * fde.js — S6 FDE 프로젝트 화면 (EPIC 8, F6.1~F6.4)
 * window.FMScreens.s6 (container) 로 등록. 모든 서버 텍스트는 textContent로만 넣는다.
 */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var KIND = { plan: '계획', progress: '진행', review: '검토', site_result: '현장 결과' };
  var BASIS = { planned: '계획값', synthetic: '합성 현장', actual: '사람이 기록한 실제 결과' };

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

  function badge() { return el('span', { className: 'badge-synthetic', text: '합성' }); }

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
          err.userMessage = (data.error && data.error.message) || '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.';
          err.fields = (data.error && data.error.fields) || {};
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error('network');
      err.userMessage = '서버에 연결하지 못했어요. 연결을 확인해 주세요.';
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
    var v = value === null || value === undefined || value === '' ? '없음' : String(value);
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
        el('h2', { text: 'S6 · FDE 프로젝트' }), badge(),
        el('p', { className: 'fde-note', text: '무료 AX 기업 비용은 0원으로 고정돼요. 현장 기록은 사람이 남긴 값이며 실제 설치 완료를 뜻하지 않아요.' })
      ]),
      el('button', { type: 'button', className: 'btn primary', text: '새 프로젝트', id: 'fde-new' })
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
      box.appendChild(el('p', { className: 'fde-note', text: '연결 가능한 기록 ID (등록 폼에 입력)' }));
      ['catalog', 'works', 'experts'].forEach(function (k) {
        var label = { catalog: '카탈로그', works: '작업', experts: '전문가' }[k];
        var items = o[k] || [];
        var text = items.length
          ? items.map(function (i) { return i.id + ' (' + i.title + ')'; }).join(', ')
          : '없음';
        box.appendChild(el('p', { text: label + ': ' + text }));
      });
    }).catch(function () {
      box.textContent = '연결 가능한 기록 목록을 불러오지 못했어요.';
    });
  }

  function loadList(container, list) {
    list.textContent = '';
    list.appendChild(el('li', { className: 'empty-state', text: '불러오는 중…' }));
    request('GET', '/api/fde/projects').then(function (data) {
      var items = data.items || [];
      list.textContent = '';
      if (!items.length) {
        list.appendChild(el('li', { className: 'empty-state', text: '아직 프로젝트가 없어요. 새 프로젝트를 등록해 보세요.' }));
        return;
      }
      items.forEach(function (p) {
        var btn = el('button', { type: 'button', className: 'fde-item', 'data-id': p.id }, [
          el('strong', { text: p.title }), badge(),
          el('span', { text: ' · ' + (p.stage_label || '계획') + ' · 기업 AX 0원' })
        ]);
        btn.addEventListener('click', function () { openDetail(container, p.id); });
        list.appendChild(el('li', {}, [btn]));
      });
    }).catch(function (err) {
      list.textContent = '';
      list.appendChild(el('li', { className: 'error-state', role: 'alert',
        text: (err && err.userMessage) || '목록을 불러오지 못했어요.' }));
    });
  }

  function openCreate(container) {
    window.FMForms.openForm({
      title: 'FDE 프로젝트 등록',
      submitLabel: '등록',
      fields: [
        { name: 'title', label: '프로젝트 이름', type: 'text', required: true },
        { name: 'catalog_id', label: '카탈로그 ID', type: 'text', required: true },
        { name: 'work_ids', label: '작업 ID (쉼표로 구분)', type: 'text', required: true },
        { name: 'expert_ids', label: '전문가 ID (쉼표로 구분)', type: 'text', required: false },
        { name: 'company_name', label: '기업 이름', type: 'text', required: false },
        { name: 'site_name', label: '합성 현장 이름', type: 'text', required: false },
        { name: 'objective', label: '목표 (objective)', type: 'textarea', required: false },
        { name: 'scope', label: '범위 (scope)', type: 'textarea', required: false },
        { name: 'deliverables', label: '산출물 (deliverables)', type: 'textarea', required: false },
        { name: 'owner', label: '담당', type: 'text', required: false },
        { name: 'notes', label: '메모', type: 'textarea', required: false },
        { name: 'planned_start', label: '계획 시작일 (YYYY-MM-DD)', type: 'date', required: false },
        { name: 'planned_end', label: '계획 종료일 (YYYY-MM-DD)', type: 'date', required: false }
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
          window.FMForms.showToast('프로젝트를 등록했어요.');
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
    container.appendChild(el('p', { className: 'empty-state', text: '불러오는 중…' }));
    request('GET', '/api/fde/projects/' + encodeURIComponent(id)).then(function (data) {
      renderDetail(container, data);
    }, function (err) {
      renderState(container, 'error', (err && err.userMessage) || '프로젝트를 불러오지 못했어요.');
    });
  }

  function renderDetail(container, data) {
    var p = data.item;
    var f = p.fields || {};
    var links = data.links || [];
    var back = el('button', { type: 'button', className: 'btn', text: '목록으로' });
    back.addEventListener('click', function () { renderList(container); });
    var root = el('div', { className: 'fde-root' });
    root.appendChild(el('div', { className: 'fde-head' }, [
      el('div', {}, [el('h2', { text: p.title }), badge(),
        el('p', { className: 'fde-note', text: '버전 ' + p.version + ' · 단계: ' + (f.stage_label || KIND[f.stage] || '계획') })]),
      back
    ]));

    var linkItems = links.map(function (l) {
      var t = (l.type || '') + ' · ' + (l.relation || '') + ' · ' + (l.id || '') + ' ' + (l.title || '');
      return el('li', { text: t });
    });
    root.appendChild(section('연결된 기록', [
      el('ul', {}, linkItems.length ? linkItems : [el('li', { text: '연결된 기록이 없어요.' })]),
      (data.missing_links && data.missing_links.length)
        ? el('p', { className: 'fde-note', text: '누락: ' + data.missing_links.map(function (m) { return m.reason || m.relation || ''; }).join(' / ') })
        : null
    ]));

    root.appendChild(section('무료 AX 범위', [
      el('dl', {}, [
        line('목표', f.objective), line('범위', f.scope), line('산출물', f.deliverables),
        line('담당', f.owner), line('메모', f.notes), line('기업 이름', f.company_name),
        line('합성 현장', f.site_name), line('계획 기간', (f.planned_start || '없음') + ' ~ ' + (f.planned_end || '없음'))
      ]),
      el('p', { className: 'fde-note', text: '기업 AX 비용: 0원 (고정)' })
    ]));

    var stageItems = (f.stage_log || []).map(function (s) {
      return el('li', {}, [
        el('strong', { text: (KIND[s.kind] || s.kind) + ' · ' + (BASIS[s.basis] || s.basis) }),
        el('div', { text: '내용: ' + (s.note || '없음') }),
        el('div', { text: '근거: ' + (s.evidence || '없음') }),
        el('div', { className: 'fde-note', text: '기록 시각: ' + (s.recorded_at || '') })
      ]);
    });
    var addStage = el('button', { type: 'button', className: 'btn', text: '단계 기록 추가' });
    addStage.addEventListener('click', function () { openStage(container, p); });
    root.appendChild(section('단계 기록', [
      el('ul', {}, stageItems.length ? stageItems : [el('li', { text: '아직 기록된 단계가 없어요.' })]), addStage
    ]));

    root.appendChild(section('결과 구분', [
      el('dl', {}, [
        line('기업용 개선/인수 결과', f.enterprise_outcome),
        line('구매자 납품·검수', f.buyer_delivery),
        line('현장 결과 메모', f.actual_result)
      ]),
      el('p', { className: 'fde-note', text: '기업용 개선 결과와 구매자 납품은 분리해서 기록해요.' })
    ]));

    var edit = el('button', { type: 'button', className: 'btn primary', text: '정보 수정' });
    edit.addEventListener('click', function () { openEdit(container, p); });
    root.appendChild(el('div', { className: 'fde-actions' }, [edit]));

    container.textContent = '';
    container.appendChild(root);
  }

  function openEdit(container, p) {
    window.FMForms.openForm({
      title: '프로젝트 정보 수정',
      submitLabel: '저장',
      fields: [
        { name: 'objective', label: '목표 (비우면 유지)', type: 'textarea', required: false },
        { name: 'scope', label: '범위 (비우면 유지)', type: 'textarea', required: false },
        { name: 'deliverables', label: '산출물 (비우면 유지)', type: 'textarea', required: false },
        { name: 'owner', label: '담당 (비우면 유지)', type: 'text', required: false },
        { name: 'notes', label: '메모 (비우면 유지)', type: 'textarea', required: false },
        { name: 'enterprise_outcome', label: '기업용 개선/인수 결과 (비우면 유지)', type: 'textarea', required: false },
        { name: 'buyer_delivery', label: '구매자 납품·검수 (비우면 유지)', type: 'textarea', required: false }
      ],
      onSubmit: function (values) {
        var body = { expected_version: p.version };
        Object.keys(values).forEach(function (k) { if (values[k]) body[k] = values[k]; });
        if (Object.keys(body).length === 1) return Promise.resolve();
        return request('PATCH', '/api/fde/projects/' + encodeURIComponent(p.id), body).then(function () {
          window.FMForms.showToast('저장했어요.');
          openDetail(container, p.id);
        }, function (err) { throw withFields(err); });
      }
    });
  }

  function openStage(container, p) {
    window.FMForms.openForm({
      title: '단계 기록 추가',
      submitLabel: '기록',
      fields: [
        { name: 'kind', label: '단계 (plan·progress·review·site_result)', type: 'text', required: true },
        { name: 'basis', label: '근거 구분 (planned·synthetic·actual)', type: 'text', required: true },
        { name: 'note', label: '내용', type: 'textarea', required: true },
        { name: 'evidence', label: '수행 근거 (진행·검토·현장 결과는 필수)', type: 'textarea', required: false }
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
          window.FMForms.showToast('단계를 기록했어요. 기록은 사람이 남긴 값이에요.');
          openDetail(container, p.id);
        }, function (err) { throw withFields(err); });
      }
    });
  }

  window.FMScreens.s6 = function (container) {
    renderList(container);
  };
})();
