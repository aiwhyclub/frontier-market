/*
 * s2-records.js — S2 지식 DB: 유형별 목록·원문·연결 상세 (STORY 2.4, F2.2)
 *
 * window.FMScreens.s2(container)
 *   container 안에 유형 선택, 목록, 상세를 그린다. app.js의 render()가 호출한다.
 *
 * 읽기 API 계약
 *   GET /api/records/<type>        -> {"items":[{"id","title","status","synthetic","version","updated_at"}]}
 *   GET /api/records/<type>/<id>   -> {"item":{..., "body"?, "material_hash", "input_hash", "version"},
 *                                      "links":[{"type","id","title","relation"}],
 *                                      "missing_links":[{"type","reason"}]}
 *   오류                            -> {"error":{"code","message"}}
 *
 * 규칙
 *  - 서버 문자열은 textContent로만 넣는다. innerHTML을 쓰지 않는다.
 *  - 오류 화면에는 원시 message·code·스택을 쓰지 않고 HTTP 상태에 맞춘 안내만 보여준다.
 *  - 빈 상태는 첫 등록 행동(fm:open-form 이벤트)이나 해당 단계 화면 이동을 제공한다.
 *  - 저장소(localStorage 등)를 쓰지 않는다. 유형·선택은 이 화면 인스턴스 안에만 둔다.
 */
(function () {
  'use strict';

  window.FMScreens = window.FMScreens || {};

  var TYPES = [
    { key: 'materials', label: '자료', empty: {
      body: 'S1에서 공개 URL을 수집하거나 본문을 직접 입력하면 첫 자료가 여기에 나타납니다.',
      form: 'source', action: '자료 등록 열기' } },
    { key: 'experts', label: '전문가', empty: {
      body: 'S3에서 후보를 등록하고 사람이 평가하면 여기에 나타납니다. 승인 전에는 작업을 업로드할 수 없습니다.',
      form: 'expert', action: '후보 등록 열기' } },
    { key: 'works', label: '작업', empty: {
      body: '승인된 전문가의 작업은 S3에서 업로드한 뒤 여기에서 원문을 확인할 수 있습니다.',
      href: '#s3', action: 'S3 전문가로 이동' } },
    { key: 'catalog', label: '카탈로그', empty: {
      body: 'S4에서 과제 카드를 만들면 여기에 나타납니다. 연결할 자료가 아직 없어도 카드는 저장할 수 있습니다.',
      form: 'catalog', action: '과제 카드 만들기' } },
    { key: 'projects', label: '프로젝트', empty: {
      body: 'S6에서 프로젝트를 등록하면 목표와 범위가 여기에 나타납니다.',
      form: 'project', action: '프로젝트 등록 열기' } },
    { key: 'companies', label: '회사', empty: {
      body: '가상회사는 S7에서 승인된 작업과 프로젝트가 연결되면 만들어집니다.',
      href: '#s7', action: 'S7 가상회사로 이동' } },
    { key: 'rights', label: '권리', empty: {
      body: '권리 확인은 자료와 작업을 등록할 때 함께 기록됩니다.',
      href: '#s1', action: 'S1 자료 수집으로 이동' } },
    { key: 'feedback', label: '의견', empty: {
      body: '구매자 의견은 S4 카탈로그 화면에서 남길 수 있습니다.',
      href: '#s4', action: 'S4 카탈로그로 이동' } }
  ];

  var MSG = {
    loading: '불러오는 중…',
    network: '서버에 연결하지 못했어요. 인터넷 연결을 확인한 뒤 다시 시도해 주세요. 화면의 선택은 그대로 있어요.',
    server: '기록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.',
    listNotFound: '이 유형의 기록 목록을 찾을 수 없어요. 다른 유형을 선택해 주세요.',
    detailNotFound: '이 기록을 찾을 수 없어요. 삭제되었거나 주소가 바뀌었을 수 있어요. 목록에서 다시 찾아 주세요.',
    retry: '다시 시도',
    back: '목록으로',
    pick: '목록에서 기록을 선택하면 원문과 연결된 기록을 볼 수 있어요.',
    noBody: '이 기록에는 원문 본문이 없어요.',
    noLinks: '연결된 기록이 없어요.',
    noReason: '연결 사유가 기록되지 않았어요.',
    synthetic: '합성 예시',
    unknownType: '기타 기록'
  };

  // 화면 인스턴스 사이에 유지되는 선택 상태 (화면 이동 뒤 돌아와도 같은 유형을 연다).
  var state = { type: 'materials', selectedId: null, listToken: 0, detailToken: 0 };

  function typeInfo(key) {
    for (var i = 0; i < TYPES.length; i += 1) {
      if (TYPES[i].key === key) return TYPES[i];
    }
    return null;
  }

  function typeLabel(key) {
    var info = typeInfo(key);
    return info ? info.label : MSG.unknownType;
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === 'text') node.textContent = String(value);
        else if (key === 'className') node.className = value;
        else node.setAttribute(key, String(value));
      });
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function failure(kind) {
    var err = new Error(kind);
    err.kind = kind;
    return err;
  }

  // 응답을 JSON으로 읽는다. 원시 본문은 사용자에게 보이지 않는다.
  function request(path) {
    return fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' }).then(
      function (res) {
        return res.text().then(function (text) {
          var body = null;
          try {
            body = text ? JSON.parse(text) : null;
          } catch (parseError) {
            body = null;
          }
          if (!res.ok) throw failure(res.status === 404 ? 'notfound' : 'server');
          if (!body || typeof body !== 'object') throw failure('server');
          return body;
        });
      },
      function () { throw failure('network'); }
    );
  }

  function errorText(err, notFoundText) {
    if (err && err.kind === 'network') return MSG.network;
    if (err && err.kind === 'notfound') return notFoundText;
    return MSG.server;
  }

  function formatDate(value) {
    // 서버 문자열을 그대로 보여준다. 날짜로 해석할 수 없으면 원문 문자열을 쓴다.
    if (!value) return '';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function statusBlock(kind, text) {
    return el('p', { className: 's2-status s2-status-' + kind, role: kind === 'error' ? 'alert' : 'status', text: text });
  }

  function emptyBlock(info) {
    var guide = info.empty;
    var actions = [];
    if (guide.form) {
      actions.push(el('button', {
        type: 'button', className: 'btn btn-primary', text: guide.action
      }));
      actions[actions.length - 1].addEventListener('click', function () {
        document.dispatchEvent(new CustomEvent('fm:open-form', {
          bubbles: true, detail: { form: guide.form }
        }));
      });
    } else {
      actions.push(el('a', { className: 'btn btn-primary', href: guide.href, text: guide.action }));
    }
    return el('section', { className: 'empty-state s2-empty', 'aria-labelledby': 's2-empty-title' }, [
      el('h3', { id: 's2-empty-title', text: info.label + ' 기록이 아직 없습니다' }),
      el('p', { text: guide.body }),
      el('div', { className: 'next-action' }, [
        el('span', { className: 'next-action-label', text: '다음 행동' })
      ].concat(actions))
    ]);
  }

  function buildSwitcher(ui) {
    var select = el('select', { id: 's2-type', className: 's2-select' });
    TYPES.forEach(function (info) {
      select.appendChild(el('option', { value: info.key, text: info.label }));
    });
    select.value = state.type;
    select.addEventListener('change', function () { setType(select.value); });
    ui.select = select;
    return el('div', { className: 's2-switcher' }, [
      el('label', { 'for': 's2-type', className: 's2-label', text: '기록 유형' }),
      select
    ]);
  }

  function buildListPane(ui) {
    ui.count = el('p', { className: 's2-count', role: 'status' });
    ui.listBody = el('div', { className: 's2-list-body' });
    ui.listHeading = el('h2', { className: 'section-title', id: 's2-list-heading', tabindex: '-1', text: '목록' });
    return el('section', { className: 's2-list', 'aria-labelledby': 's2-list-heading' }, [
      ui.listHeading,
      ui.count,
      ui.listBody
    ]);
  }

  function buildDetailPane(ui) {
    ui.back = el('button', { type: 'button', className: 'btn btn-secondary s2-back', text: MSG.back });
    ui.back.addEventListener('click', function () { clearSelection(); });
    ui.detailBody = el('div', { className: 's2-detail-body' });
    return el('section', { className: 's2-detail', 'aria-labelledby': 's2-detail-heading' }, [
      ui.back,
      ui.detailBody
    ]);
  }

  var ui = null;

  function setView() {
    if (!ui || !ui.layout) return;
    ui.layout.setAttribute('data-view', state.selectedId ? 'detail' : 'list');
  }

  function setType(key) {
    if (!typeInfo(key)) return;
    state.type = key;
    state.selectedId = null;
    if (ui && ui.select) ui.select.value = key;
    setView();
    showPick();
    loadList();
  }

  function clearSelection() {
    state.selectedId = null;
    setView();
    showPick();
    renderListSelection();
    if (ui && ui.listHeading) ui.listHeading.focus({ preventScroll: false });
  }

  function selectRecord(type, id) {
    if (!typeInfo(type)) return;
    var typeChanged = type !== state.type;
    state.type = type;
    state.selectedId = id;
    if (ui && ui.select) ui.select.value = type;
    setView();
    if (typeChanged) loadList(); else renderListSelection();
    loadDetail(type, id);
  }

  function showPick() {
    if (!ui) return;
    ui.detailBody.textContent = '';
    ui.detailBody.appendChild(el('p', { className: 's2-pick', text: MSG.pick }));
    ui.back.hidden = true;
  }

  function renderListSelection() {
    if (!ui || !ui.listBody) return;
    var buttons = ui.listBody.querySelectorAll('button[data-id]');
    Array.prototype.forEach.call(buttons, function (button) {
      var on = button.getAttribute('data-id') === state.selectedId;
      if (on) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
  }

  function loadList() {
    if (!ui) return;
    var token = ++state.listToken;
    var type = state.type;
    var info = typeInfo(type);
    ui.listBody.textContent = '';
    ui.count.textContent = '';
    ui.listBody.appendChild(statusBlock('loading', MSG.loading));

    request('/api/records/' + encodeURIComponent(type)).then(function (data) {
      if (token !== state.listToken || !ui || !ui.listBody.isConnected) return;
      var items = Array.isArray(data.items) ? data.items : [];
      renderList(info, items);
    }, function (err) {
      if (token !== state.listToken || !ui || !ui.listBody.isConnected) return;
      renderListError(errorText(err, MSG.listNotFound));
    });
  }

  function renderListError(text) {
    ui.count.textContent = '';
    ui.listBody.textContent = '';
    var retry = el('button', { type: 'button', className: 'btn btn-secondary', text: MSG.retry });
    retry.addEventListener('click', loadList);
    ui.listBody.appendChild(el('div', { className: 's2-error' }, [
      statusBlock('error', text),
      retry
    ]));
  }

  function renderList(info, items) {
    ui.listBody.textContent = '';
    ui.count.textContent = '저장된 ' + info.label + ' 기록 ' + items.length + '건';
    if (!items.length) {
      ui.listBody.appendChild(emptyBlock(info));
      return;
    }
    var list = el('ul', { className: 's2-cards' });
    items.forEach(function (item) {
      var id = String(item.id === undefined || item.id === null ? '' : item.id);
      var meta = [];
      if (item.synthetic) meta.push(el('span', { className: 's2-badge', text: MSG.synthetic }));
      if (item.status) meta.push(el('span', { className: 's2-meta', text: '상태 ' + item.status }));
      if (item.version !== undefined && item.version !== null) meta.push(el('span', { className: 's2-meta', text: '버전 ' + item.version }));
      if (item.updated_at) meta.push(el('span', { className: 's2-meta', text: formatDate(item.updated_at) }));
      var button = el('button', {
        type: 'button', className: 's2-card-button', 'data-id': id
      }, [
        el('span', { className: 's2-card-title', text: item.title || '(제목 없음)' }),
        el('span', { className: 's2-card-meta' }, meta)
      ]);
      button.addEventListener('click', function () { selectRecord(state.type, id); });
      list.appendChild(el('li', null, [button]));
    });
    ui.listBody.appendChild(list);
    renderListSelection();
  }

  function loadDetail(type, id) {
    if (!ui) return;
    var token = ++state.detailToken;
    ui.detailBody.textContent = '';
    ui.back.hidden = false;
    ui.detailBody.appendChild(statusBlock('loading', MSG.loading));

    request('/api/records/' + encodeURIComponent(type) + '/' + encodeURIComponent(id)).then(function (data) {
      if (token !== state.detailToken || !ui || !ui.detailBody.isConnected) return;
      renderDetail(type, id, data);
    }, function (err) {
      if (token !== state.detailToken || !ui || !ui.detailBody.isConnected) return;
      renderDetailError(type, id, errorText(err, MSG.detailNotFound));
    });
  }

  function renderDetailError(type, id, text) {
    ui.detailBody.textContent = '';
    var retry = el('button', { type: 'button', className: 'btn btn-secondary', text: MSG.retry });
    retry.addEventListener('click', function () { loadDetail(type, id); });
    ui.detailBody.appendChild(el('div', { className: 's2-error' }, [statusBlock('error', text), retry]));
    focusDetailHeading();
  }

  function focusDetailHeading() {
    if (!ui) return;
    var heading = ui.detailBody.querySelector('h3');
    if (heading) heading.focus({ preventScroll: false });
  }

  function definition(label, value) {
    return [el('dt', { text: label }), el('dd', { text: value === undefined || value === null || value === '' ? '없음' : String(value) })];
  }

  function renderDetail(type, id, data) {
    var item = data.item && typeof data.item === 'object' ? data.item : {};
    var links = Array.isArray(data.links) ? data.links : [];
    var missing = Array.isArray(data.missing_links) ? data.missing_links : [];
    var info = typeInfo(type);
    var body = typeof item.body === 'string' ? item.body : '';

    var heading = el('h3', { id: 's2-detail-heading', tabindex: '-1', text: item.title || '(제목 없음)' });
    var head = el('div', { className: 's2-detail-head' }, [
      el('p', { className: 's2-kicker', text: (info ? info.label : MSG.unknownType) + ' 기록' }),
      heading,
      el('p', { className: 's2-meta-row' }, [
        item.synthetic ? el('span', { className: 's2-badge', text: MSG.synthetic }) : null,
        item.status ? el('span', { className: 's2-meta', text: '상태 ' + item.status }) : null
      ].filter(Boolean))
    ]);

    var original = el('section', { className: 's2-section', 'aria-labelledby': 's2-original-title' }, [
      el('h4', { id: 's2-original-title', text: '원문' }),
      body
        ? el('div', { className: 's2-body', tabindex: '0', text: body })
        : el('p', { className: 's2-muted', text: MSG.noBody })
    ]);

    var versions = el('section', { className: 's2-section', 'aria-labelledby': 's2-version-title' }, [
      el('h4', { id: 's2-version-title', text: '버전과 해시' }),
      el('dl', { className: 's2-dl' }, [].concat(
        definition('버전', item.version),
        definition('원문 해시', item.material_hash),
        definition('입력 해시', item.input_hash)
      ))
    ]);

    var linkList = links.length
      ? el('ul', { className: 's2-links' }, links.map(function (link) {
          var linkType = link.type;
          var linkId = String(link.id === undefined || link.id === null ? '' : link.id);
          var button = el('button', { type: 'button', className: 's2-link-button' }, [
            el('span', { className: 's2-card-title', text: link.title || '(제목 없음)' }),
            el('span', { className: 's2-meta', text: typeLabel(linkType) + ' · ' + (link.relation || '관계 미표시') })
          ]);
          button.addEventListener('click', function () { selectRecord(linkType, linkId); });
          return el('li', null, [button]);
        }))
      : el('p', { className: 's2-muted', text: MSG.noLinks });

    var linksSection = el('section', { className: 's2-section', 'aria-labelledby': 's2-links-title' }, [
      el('h4', { id: 's2-links-title', text: '연결된 기록' }),
      linkList
    ]);

    var sections = [head, original, versions, linksSection];

    if (missing.length) {
      sections.push(el('section', { className: 's2-section', 'aria-labelledby': 's2-missing-title' }, [
        el('h4', { id: 's2-missing-title', text: '연결되지 않은 항목' }),
        el('ul', { className: 's2-missing' }, missing.map(function (miss) {
          return el('li', null, [
            el('span', { className: 's2-card-title', text: typeLabel(miss.type) }),
            el('span', { className: 's2-muted', text: miss.reason || MSG.noReason })
          ]);
        }))
      ]));
    }

    ui.detailBody.textContent = '';
    sections.forEach(function (node) { ui.detailBody.appendChild(node); });
    focusDetailHeading();
  }

  function mount(container) {
    if (!container) return;
    ui = {};
    container.textContent = '';
    container.classList.add('s2-root');

    var switcher = buildSwitcher(ui);
    var listPane = buildListPane(ui);
    var detailPane = buildDetailPane(ui);
    ui.layout = el('div', { className: 's2-layout', 'data-view': state.selectedId ? 'detail' : 'list' }, [
      switcher,
      listPane,
      detailPane
    ]);
    container.appendChild(ui.layout);

    if (state.selectedId) {
      loadList();
      loadDetail(state.type, state.selectedId);
    } else {
      showPick();
      loadList();
    }
  }

  window.FMScreens.s2 = mount;
})();
