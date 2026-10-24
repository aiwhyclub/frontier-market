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

  var TYPES = FMI18n.lazy(function () { return [
    { key: 'materials', label: t('collect.materials'), empty: {
      body: t('s2.collect_a_public_url_or'),
      form: 'source', action: t('app.open_material_form') } },
    { key: 'experts', label: t('app.experts'), empty: {
      body: t('s2.register_a_candidate_in_s3'),
      form: 'expert', action: t('app.open_candidate_form') } },
    { key: 'works', label: t('demo.work'), empty: {
      body: t('s2.work_from_approved_experts_is'),
      href: '#s3', action: t('app.go_to_s3_experts') } },
    { key: 'catalog', label: t('app.catalog'), empty: {
      body: t('s2.create_a_task_card_in'),
      form: 'catalog', action: t('app.create_task_card') } },
    { key: 'projects', label: t('demo.project'), empty: {
      body: t('s2.register_a_project_in_s6'),
      form: 'project', action: t('app.open_project_form') } },
    { key: 'companies', label: t('s2.company'), empty: {
      body: t('s2.a_virtual_company_is_created'),
      href: '#s7', action: t('s2.go_to_s7_virtual_company') } },
    { key: 'rights', label: t('s2.rights'), empty: {
      body: t('s2.rights_confirmation_is_recorded_together'),
      href: '#s1', action: t('app.go_to_s1_collect') } },
    { key: 'feedback', label: t('feedback.feedback_2'), empty: {
      body: t('s2.buyer_feedback_can_be_left'),
      href: '#s4', action: t('s2.go_to_s4_catalog') } }
  ]; });

  var MSG = FMI18n.lazy(function () { return {
    loading: t('demo.loading'),
    network: t('s2.could_not_reach_the_server'),
    server: t('s2.could_not_load_records_please'),
    listNotFound: t('s2.the_record_list_for_this'),
    detailNotFound: t('s2.this_record_was_not_found'),
    retry: t('catalog.try_again'),
    back: t('collect.back_to_list'),
    pick: t('s2.select_a_record_in_the'),
    noBody: t('s2.this_record_has_no_original'),
    noLinks: t('fde.no_linked_records'),
    noReason: t('s2.no_link_reason_recorded'),
    synthetic: t('s2.synthetic_example'),
    unknownType: t('s2.other_records')
  }; });

  // 화면 인스턴스 사이에 유지되는 선택 상태 (화면 이동 뒤 돌아와도 같은 유형을 연다).
  // query는 목록 검색 조건이다. 기록을 열었다 돌아와도 같은 조건으로 목록을 다시 연다.
  var state = {
    type: 'materials', selectedId: null, listToken: 0, detailToken: 0,
    query: { q: '', field: '', status: '' }
  };
  var QUERY_MAX = 100;

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
      el('h3', { id: 's2-empty-title', text: t('s2.no_records_yet', { label: info.label }) }),
      el('p', { text: guide.body }),
      el('div', { className: 'next-action' }, [
        el('span', { className: 'next-action-label', text: t('app.next_action') })
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
      el('label', { 'for': 's2-type', className: 's2-label', text: t('s2.record_type') }),
      select
    ]);
  }

  function buildListPane(ui) {
    ui.count = el('p', { className: 's2-count', role: 'status' });
    ui.listBody = el('div', { className: 's2-list-body' });
    ui.listHeading = el('h2', { className: 'section-title', id: 's2-list-heading', tabindex: '-1', text: t('s2.list') });
    return el('section', { className: 's2-list', 'aria-labelledby': 's2-list-heading' }, [
      ui.listHeading,
      buildSearch(ui),
      ui.count,
      ui.listBody
    ]);
  }

  function cleanQuery(value) {
    return String(value === undefined || value === null ? '' : value).trim();
  }

  function isSearching() {
    return !!(state.query.q || state.query.field || state.query.status);
  }

  function describeQuery() {
    var parts = [];
    if (state.query.q) parts.push(t('s2.search') + state.query.q + '"');
    if (state.query.field) parts.push(t('s2.domain') + state.query.field + '"');
    if (state.query.status) parts.push(t('s2.status') + state.query.status + '"');
    return parts.join(' · ');
  }

  function searchQueryString() {
    var params = new URLSearchParams();
    ['q', 'field', 'status'].forEach(function (key) {
      if (state.query[key]) params.set(key, state.query[key]);
    });
    var text = params.toString();
    return text ? '?' + text : '';
  }

  // 검색 입력은 제출(검색 버튼·Enter)할 때만 목록을 다시 불러온다.
  function applySearch(q, field, status) {
    state.query = {
      q: cleanQuery(q).slice(0, QUERY_MAX),
      field: cleanQuery(field).slice(0, QUERY_MAX),
      status: cleanQuery(status).slice(0, QUERY_MAX)
    };
    syncSearchInputs();
    loadList();
  }

  function syncSearchInputs() {
    if (!ui || !ui.search) return;
    ui.search.q.value = state.query.q;
    ui.search.field.value = state.query.field;
    ui.search.status.value = state.query.status;
  }

  function buildSearch(ui) {
    var qInput = el('input', {
      id: 's2-q', type: 'search', className: 's2-input', name: 'q', maxlength: String(QUERY_MAX),
      autocomplete: 'off', placeholder: t('s2.part_of_the_title_or')
    });
    var fieldInput = el('input', {
      id: 's2-field', type: 'text', className: 's2-input', name: 'field', maxlength: String(QUERY_MAX),
      autocomplete: 'off', placeholder: t('s2.e_g_administration')
    });
    var statusInput = el('input', {
      id: 's2-status', type: 'text', className: 's2-input', name: 'status', maxlength: String(QUERY_MAX),
      autocomplete: 'off', placeholder: t('s2.e_g_draft')
    });
    ui.search = { q: qInput, field: fieldInput, status: statusInput };
    syncSearchInputs();

    var submit = el('button', { type: 'submit', className: 'btn btn-primary', text: t('s2.search_2') });
    var reset = el('button', { type: 'button', className: 'btn btn-secondary', text: t('s2.clear_search') });
    reset.addEventListener('click', function () { applySearch('', '', ''); });

    var form = el('form', { className: 's2-search', role: 'search', 'aria-label': t('s2.search_records') }, [
      el('div', { className: 's2-field' }, [
        el('label', { 'for': 's2-q', className: 's2-label', text: t('s2.search_term_title_body') }), qInput
      ]),
      el('div', { className: 's2-field' }, [
        el('label', { 'for': 's2-field', className: 's2-label', text: t('catalog.domain') }), fieldInput
      ]),
      el('div', { className: 's2-field' }, [
        el('label', { 'for': 's2-status', className: 's2-label', text: t('rights.status') }), statusInput
      ]),
      el('div', { className: 's2-search-actions' }, [submit, reset])
    ]);
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      applySearch(qInput.value, fieldInput.value, statusInput.value);
    });
    return form;
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

    request('/api/records/' + encodeURIComponent(type) + searchQueryString()).then(function (data) {
      if (token !== state.listToken || !ui || !ui.listBody.isConnected) return;
      var items = Array.isArray(data.items) ? data.items : [];
      var total = typeof data.total === 'number' ? data.total : items.length;
      renderList(info, items, total);
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

  function searchEmptyBlock(info) {
    var reset = el('button', { type: 'button', className: 'btn btn-secondary', text: t('s2.clear_search') });
    reset.addEventListener('click', function () { applySearch('', '', ''); });
    return el('section', { className: 'empty-state s2-empty', 'aria-labelledby': 's2-search-empty-title' }, [
      el('h3', { id: 's2-search-empty-title', text: t('s2.no_matching_records', { label: info.label }) }),
      el('p', { text: t('s2.search_conditions') + ' ' + describeQuery() + t('s2.shorten_the_search_term_change') }),
      el('div', { className: 'next-action' }, [
        el('span', { className: 'next-action-label', text: t('app.next_action') }),
        reset
      ])
    ]);
  }

  function renderList(info, items, total) {
    ui.listBody.textContent = '';
    ui.count.textContent = isSearching()
      ? t('s2.search_results_count', { total: total, query: describeQuery() })
      : t('s2.saved_records_count', { label: info.label, total: total });
    if (!items.length) {
      ui.listBody.appendChild(isSearching() ? searchEmptyBlock(info) : emptyBlock(info));
      return;
    }
    var list = el('ul', { className: 's2-cards' });
    items.forEach(function (item) {
      var id = String(item.id === undefined || item.id === null ? '' : item.id);
      var meta = [];
      if (item.synthetic) meta.push(el('span', { className: 's2-badge', text: MSG.synthetic }));
      if (item.status) meta.push(el('span', { className: 's2-meta', text: t('rights.status') + ' ' + item.status }));
      if (item.version !== undefined && item.version !== null) meta.push(el('span', { className: 's2-meta', text: t('collect.version') + ' ' + item.version }));
      if (item.updated_at) meta.push(el('span', { className: 's2-meta', text: formatDate(item.updated_at) }));
      var button = el('button', {
        type: 'button', className: 's2-card-button', 'data-id': id
      }, [
        el('span', { className: 's2-card-title', text: item.title || t('s2.untitled') }),
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
    return [el('dt', { text: label }), el('dd', { text: value === undefined || value === null || value === '' ? t('collect.none') : String(value) })];
  }

  function renderDetail(type, id, data) {
    var item = data.item && typeof data.item === 'object' ? data.item : {};
    var links = Array.isArray(data.links) ? data.links : [];
    var missing = Array.isArray(data.missing_links) ? data.missing_links : [];
    var info = typeInfo(type);
    var body = typeof item.body === 'string' ? item.body : '';

    var heading = el('h3', { id: 's2-detail-heading', tabindex: '-1', text: item.title || t('s2.untitled') });
    var head = el('div', { className: 's2-detail-head' }, [
      el('p', { className: 's2-kicker', text: (info ? info.label : MSG.unknownType) + ' ' + t('fde.records') }),
      heading,
      el('p', { className: 's2-meta-row' }, [
        item.synthetic ? el('span', { className: 's2-badge', text: MSG.synthetic }) : null,
        item.status ? el('span', { className: 's2-meta', text: t('rights.status') + ' ' + item.status }) : null
      ].filter(Boolean))
    ]);

    var original = el('section', { className: 's2-section', 'aria-labelledby': 's2-original-title' }, [
      el('h4', { id: 's2-original-title', text: t('collect.original_text') }),
      body
        ? el('div', { className: 's2-body', tabindex: '0', text: body })
        : el('p', { className: 's2-muted', text: MSG.noBody })
    ]);

    var versions = el('section', { className: 's2-section', 'aria-labelledby': 's2-version-title' }, [
      el('h4', { id: 's2-version-title', text: t('s2.version_and_hash') }),
      el('dl', { className: 's2-dl' }, [].concat(
        definition(t('collect.version'), item.version),
        definition(t('s2.original_text_hash'), item.material_hash),
        definition(t('s2.input_hash'), item.input_hash)
      ))
    ]);

    var linkList = links.length
      ? el('ul', { className: 's2-links' }, links.map(function (link) {
          var linkType = link.type;
          var linkId = String(link.id === undefined || link.id === null ? '' : link.id);
          var button = el('button', { type: 'button', className: 's2-link-button' }, [
            el('span', { className: 's2-card-title', text: link.title || t('s2.untitled') }),
            el('span', { className: 's2-meta', text: typeLabel(linkType) + ' · ' + (link.relation || t('s2.relations_not_shown')) })
          ]);
          button.addEventListener('click', function () { selectRecord(linkType, linkId); });
          return el('li', null, [button]);
        }))
      : el('p', { className: 's2-muted', text: MSG.noLinks });

    var linksSection = el('section', { className: 's2-section', 'aria-labelledby': 's2-links-title' }, [
      el('h4', { id: 's2-links-title', text: t('fde.linked_records') }),
      linkList
    ]);

    var sections = [head, original, versions, linksSection];

    if (missing.length) {
      sections.push(el('section', { className: 's2-section', 'aria-labelledby': 's2-missing-title' }, [
        el('h4', { id: 's2-missing-title', text: t('s2.unlinked_items') }),
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
