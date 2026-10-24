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

  var TYPE_LABEL = { materials: '자료', experts: '전문가', works: '작업' };
  var STATUS_LABEL = {
    confirmed: '확인됨', restricted: '제한 확인', unconfirmed: '미확인',
    revoked: '철회', expired: '기간 만료', legacy: '이전 형식'
  };
  var PERM_LABEL = { unknown: '미확인', allowed: '허용', denied: '거부' };
  var PURPOSE_LABEL = { training: '학습', evaluation: '평가' };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function badgeSynthetic(item) {
    return item && item.synthetic ? el('span', 'fm-badge', '합성') : null;
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
            '불러오지 못했어요. 다시 시도해 주세요.';
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
      var btn = el('button', 'btn', '다시 시도');
      btn.type = 'button';
      btn.addEventListener('click', retry);
      box.appendChild(btn);
    }
    container.appendChild(box);
  }

  function loading(container) {
    container.innerHTML = '';
    container.appendChild(el('p', 'rights-loading', '불러오는 중…'));
  }

  function formFields(fixed) {
    var fields = [];
    if (!fixed) {
      fields.push({ name: 'subject_type', label: '대상 유형 (materials / experts / works)', type: 'text', required: true });
      fields.push({ name: 'subject_id', label: '대상 ID', type: 'text', required: true });
    }
    fields.push({ name: 'subject_version', label: '대상 개정 번호 (정수)', type: 'number', required: true });
    fields.push({ name: 'holder', label: '권리자', type: 'text', required: true });
    fields.push({ name: 'purposes', label: '목적 (training, evaluation 중 쉼표로)', type: 'text', required: true });
    fields.push({ name: 'derivative', label: '파생 (unknown / allowed / denied)', type: 'text', required: true });
    fields.push({ name: 'distribution', label: '배포 (unknown / allowed / denied)', type: 'text', required: true });
    fields.push({ name: 'status', label: '상태 (confirmed / restricted / unconfirmed / revoked)', type: 'text', required: true });
    fields.push({ name: 'reason', label: '사유', type: 'textarea', required: true });
    fields.push({ name: 'evidence', label: '근거 (확인·제한 상태에 필요)', type: 'textarea', required: false });
    fields.push({ name: 'restrictions', label: '제한 내용 (제한 확인에 필요)', type: 'textarea', required: false });
    fields.push({ name: 'valid_until', label: '기간 종료일 (YYYY-MM-DD, 선택)', type: 'text', required: false });
    fields.push({ name: 'actor', label: '담당자 (지정 운영책임자)', type: 'text', required: true });
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
      ['권리자', g.holder],
      ['개정 번호', g.subject_version + (g.applies_now ? ' (현재 내용과 일치)' : ' (이전 내용)')],
      ['목적', (g.purposes || []).map(function (p) { return PURPOSE_LABEL[p] || p; }).join(', ') || '없음'],
      ['파생', PERM_LABEL[g.derivative] || g.derivative],
      ['배포', PERM_LABEL[g.distribution] || g.distribution],
      ['기간 종료', g.valid_until || '없음'],
      ['제한', g.restrictions || '없음'],
      ['근거', g.evidence || '없음'],
      ['사유', g.reason || '없음'],
      ['확인자·일시', g.confirmed_by ? g.confirmed_by + ' · ' + (g.confirmed_at || '') : '없음'],
      ['기록 담당·일시', g.recorded_by ? g.recorded_by + ' · ' + (g.recorded_at || '') : '없음']
    ].forEach(function (pair) {
      dl.appendChild(el('dt', null, pair[0]));
      dl.appendChild(el('dd', null, pair[1] === undefined || pair[1] === null ? '없음' : pair[1]));
    });
    row.appendChild(dl);
    return row;
  }

  function renderState(state) {
    var box = el('section', 'rights-state');
    var isHold = state.state !== 'allowed';
    box.appendChild(el('h3', null, '이용 범위'));
    var verdict = el('p', isHold ? 'rights-hold' : 'rights-allowed',
      isHold ? '보류(HOLD): 생성·추가 내보내기가 막혀 있어요.' : '허용: 현재 내용 기준으로 확인된 범위 안에서 이용할 수 있어요.');
    box.appendChild(verdict);
    if (state.reasons && state.reasons.length) {
      var ul = el('ul', 'rights-reasons');
      state.reasons.forEach(function (r) { ul.appendChild(el('li', null, r)); });
      box.appendChild(ul);
    }
    if (state.past_grant_count) {
      box.appendChild(el('p', 'rights-note', '이전 개정에 대한 권리 기록 ' + state.past_grant_count + '건은 현재 내용에 적용되지 않아요.'));
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
        fail(container, err.userMessage || '권리 정보를 불러오지 못했어요.', reload);
      });
    }

    function render(state, list, hist) {
      container.innerHTML = '';
      var wrap = el('div', 'rights-panel');
      var head = el('div', 'rights-head');
      head.appendChild(el('h2', null, '이용 범위 · ' + (TYPE_LABEL[stype] || stype)));
      var add = el('button', 'btn', '권리 확인 기록 추가');
      add.type = 'button';
      add.addEventListener('click', function () { openRecord(null); });
      head.appendChild(add);
      wrap.appendChild(head);
      wrap.appendChild(renderState(state));

      var list2 = el('section', 'rights-grants');
      list2.appendChild(el('h3', null, '권리 확인 기록'));
      if (!list.items.length) {
        list2.appendChild(el('p', 'rights-empty', '아직 권리 확인 기록이 없어요. 권리 확인 기록을 추가해 주세요.'));
      } else {
        var ul = el('ul', 'rights-list');
        list.items.forEach(function (g) {
          var row = grantRow(g);
          var edit = el('button', 'btn', '수정');
          edit.type = 'button';
          edit.addEventListener('click', function () { openRecord(g); });
          row.appendChild(edit);
          ul.appendChild(row);
        });
        list2.appendChild(ul);
      }
      wrap.appendChild(list2);

      var histBox = el('section', 'rights-history');
      histBox.appendChild(el('h3', null, '개정 이력과 판정 사건'));
      histBox.appendChild(el('p', 'rights-note', '현재 기록: 개정 ' + hist.current.version + ' (H ' + String(hist.current.material_hash).slice(0, 12) + '…)'));
      var revs = el('ul', 'rights-revs');
      hist.revisions.forEach(function (rv) {
        var li = el('li', null, (rv.is_current ? '현재 ' : '과거 ') + '개정 ' + rv.version + ' · ' + rv.created_at);
        var btn = el('button', 'btn', '당시 내용 보기');
        btn.type = 'button';
        btn.addEventListener('click', function () { showSnapshot(rv); });
        li.appendChild(btn);
        revs.appendChild(li);
      });
      histBox.appendChild(revs);
      var evs = el('ul', 'rights-events');
      if (!hist.events.length) evs.appendChild(el('li', null, '기록된 권리 사건이 없어요.'));
      hist.events.forEach(function (e) {
        evs.appendChild(el('li', null, e.at + ' · ' + e.action + ' · ' + (e.status_from || '-') + ' → ' + (e.status_to || '-') +
          ' · 사유: ' + e.reason + ' · 담당: ' + e.actor + ' · 개정 ' + (e.subject_version || '-')));
      });
      histBox.appendChild(evs);
      wrap.appendChild(histBox);
      container.appendChild(wrap);
    }

    function showSnapshot(rv) {
      var s = rv.snapshot || {};
      var rows = [
        { label: '개정', value: String(rv.version) },
        { label: '저장 시각', value: rv.created_at },
        { label: '제목', value: s.title },
        { label: '상태', value: s.status },
        { label: '본문', value: s.body },
        { label: '내용 해시', value: rv.material_hash }
      ];
      if (window.FMForms && window.FMForms.openDetail) {
        window.FMForms.openDetail({ title: '과거 개정 기록', rows: rows });
      }
    }

    function openRecord(existing) {
      if (!window.FMForms || !window.FMForms.openForm) return;
      window.FMForms.openForm({
        title: existing ? '권리 확인 기록 수정' : '권리 확인 기록 추가',
        fields: formFields(true),
        submitLabel: existing ? '수정 저장' : '저장',
        onSubmit: function (values) {
          var body = toBody(values, { subject_type: stype, subject_id: sid });
          var req = existing
            ? api('PATCH', '/api/rights/' + encodeURIComponent(existing.id),
                  Object.assign(body, { expected_version: existing.version }))
            : api('POST', '/api/rights', body);
          return req.then(function () {
            if (window.FMForms.showToast) window.FMForms.showToast('권리 확인 기록을 저장했어요.');
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
        head.appendChild(el('h2', null, '이용 범위 기록'));
        var add = el('button', 'btn', '새 권리 기록');
        add.type = 'button';
        add.addEventListener('click', function () {
          if (!window.FMForms) return;
          window.FMForms.openForm({
            title: '새 권리 확인 기록',
            fields: formFields(false),
            onSubmit: function (values) {
              return api('POST', '/api/rights', toBody(values, {})).then(function () {
                if (window.FMForms.showToast) window.FMForms.showToast('권리 확인 기록을 저장했어요.');
                reload();
              });
            }
          });
        });
        head.appendChild(add);
        wrap.appendChild(head);
        if (!data.items.length) {
          wrap.appendChild(el('p', 'rights-empty', '권리 기록이 없어요.'));
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
            row.appendChild(el('span', 'rights-note', g.applies_now ? '현재 내용에 적용' : '이전 내용 기록'));
            ul.appendChild(row);
          });
          wrap.appendChild(ul);
        }
        container.appendChild(wrap);
      }).catch(function (err) {
        fail(container, err.userMessage || '권리 기록을 불러오지 못했어요.', reload);
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
