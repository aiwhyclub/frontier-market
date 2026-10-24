/* S1 자료 수집: URL 가져오기, 본문 직접 등록, 목록·상세, 원문·근거 개정, 정리 초안 채택/교정. */
(function () {
  'use strict';
  window.FMScreens = window.FMScreens || {};

  var REASONS = {
    invalid_url: '주소 형식이 올바르지 않습니다.',
    unsupported_scheme: 'http 또는 https 주소만 가져올 수 있습니다.',
    blocked_port: '80 또는 443 포트의 공개 주소만 가져올 수 있습니다.',
    blocked_address: '내부 또는 비공개 주소라서 수집하지 않았습니다.',
    dns_error: '주소를 찾지 못했습니다.',
    http_error: '서버가 정상 응답을 주지 않았습니다.',
    too_many_redirects: '리다이렉트가 너무 많습니다.',
    unsupported_type: 'HTML·텍스트·JSON 자료만 가져올 수 있습니다. PDF·영상은 본문을 직접 붙여 넣어 주세요.',
    too_large: '자료가 500KiB를 넘었습니다.',
    text_too_long: '본문이 10만 자를 넘었습니다.',
    timeout: '10초 안에 응답이 없었습니다.',
    network_error: '연결하지 못했습니다.',
    empty: '읽을 수 있는 본문이 없습니다.',
    unreadable: '문자 코드를 읽을 수 없습니다.'
  };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'class') node.className = attrs[k];
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

  function badge() {
    return el('span', { class: 'cl-badge', text: '합성' });
  }

  function request(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    }, function () {
      return { ok: false, status: 0, data: { error: { code: 'network', message: '서버에 연결하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.' } } };
    });
  }

  function errorText(res) {
    var err = (res.data && res.data.error) || {};
    var text = err.message || '저장하지 못했습니다. 입력은 그대로 두었습니다.';
    if (err.reason && REASONS[err.reason]) text += ' 사유: ' + REASONS[err.reason];
    if (err.fields) {
      var parts = Object.keys(err.fields).map(function (k) { return k + ': ' + err.fields[k]; });
      if (parts.length) text += ' (' + parts.join(', ') + ')';
    }
    return text;
  }

  function toast(msg) {
    if (typeof window.showToast === 'function') window.showToast(msg);
  }

  function field(label, input) {
    return el('label', { class: 'cl-field' }, [el('span', { text: label }), input]);
  }

  function input(name, value, multiline) {
    var node = el(multiline ? 'textarea' : 'input', { name: name, class: 'cl-input' });
    if (multiline) node.rows = 5; else node.type = 'text';
    node.value = value || '';
    return node;
  }

  function section(title, children) {
    return el('section', { class: 'cl-section' }, [el('h3', { text: title })].concat(children));
  }

  function statusLine(node, text, isError) {
    node.textContent = text;
    node.className = 'cl-status' + (isError ? ' cl-status-error' : '');
  }

  function fetchForm(root, onDone) {
    var url = input('url', '');
    url.placeholder = 'https://…';
    url.inputMode = 'url';
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, [
      field('공개 주소(URL)', url),
      el('p', { class: 'cl-hint', text: '공개 페이지만 가져옵니다. 로그인이 필요한 주소와 내부 주소는 수집하지 않습니다.' }),
      el('button', { type: 'submit', class: 'cl-btn', text: '가져오기' }),
      status
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var btn = form.querySelector('button');
      btn.disabled = true;
      statusLine(status, '가져오는 중입니다…');
      request('POST', '/api/collect/fetch', { url: url.value }).then(function (res) {
        btn.disabled = false;
        if (res.ok) {
          toast('자료를 저장했습니다.');
          onDone(res.data.item.id);
          return;
        }
        // Keep the typed URL so the user can fix it and retry, or switch to direct body entry.
        statusLine(status, errorText(res), true);
      });
    });
    return section('URL로 가져오기', [form]);
  }

  function manualForm(onDone) {
    var title = input('title', '');
    var kind = input('kind', '');
    var sourceType = input('source_type', '');
    var sourceName = input('source_name', '');
    var body = input('body', '', true);
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, [
      field('제목', title),
      field('분야', kind),
      field('유형', sourceType),
      field('출처 이름', sourceName),
      field('본문', body),
      el('p', { class: 'cl-hint', text: '가져오기가 실패해도 여기에 본문을 붙여 넣으면 계속할 수 있습니다. 합성 가공 사례만 등록합니다.' }),
      el('button', { type: 'submit', class: 'cl-btn', text: '본문 저장' }),
      status
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      statusLine(status, '저장하는 중입니다…');
      request('POST', '/api/collect/materials', {
        title: title.value, body: body.value, kind: kind.value,
        source_type: sourceType.value, source_name: sourceName.value, synthetic: true
      }).then(function (res) {
        if (res.ok) {
          toast('본문을 저장했습니다.');
          onDone(res.data.item.id);
          return;
        }
        statusLine(status, errorText(res), true);
      });
    });
    return section('본문 직접 등록', [form]);
  }

  function listPanel(onOpen) {
    var status = el('p', { class: 'cl-status', text: '목록을 불러오는 중입니다…', 'aria-live': 'polite' });
    var list = el('ul', { class: 'cl-list' });
    var wrap = el('div', null, [status, list]);
    function load() {
      statusLine(status, '목록을 불러오는 중입니다…');
      list.textContent = '';
      request('GET', '/api/records/materials?limit=100').then(function (res) {
        if (!res.ok) { statusLine(status, errorText(res), true); return; }
        var items = (res.data && res.data.items) || [];
        if (!items.length) { statusLine(status, '아직 수집한 자료가 없습니다. 위에서 주소를 가져오거나 본문을 직접 등록해 주세요.'); return; }
        statusLine(status, '자료 ' + items.length + '건');
        items.forEach(function (it) {
          var btn = el('button', { type: 'button', class: 'cl-link' }, [
            el('span', { text: it.title }),
            it.synthetic ? badge() : null,
            el('span', { class: 'cl-meta', text: ' v' + it.version })
          ]);
          btn.addEventListener('click', function () { onOpen(it.id); });
          list.appendChild(el('li', null, [btn]));
        });
      });
    }
    load();
    return { node: section('수집한 자료', [wrap]), reload: load };
  }

  function detailPanel(id, onChanged) {
    var box = el('div', { class: 'cl-detail' }, [el('p', { class: 'cl-status', text: '자료를 불러오는 중입니다…' })]);
    request('GET', '/api/collect/materials/' + encodeURIComponent(id)).then(function (res) {
      box.textContent = '';
      if (!res.ok) {
        box.appendChild(el('p', { class: 'cl-status cl-status-error', text: errorText(res) }));
        return;
      }
      renderDetail(box, res.data, onChanged);
    });
    return box;
  }

  function kv(label, value) {
    return el('div', { class: 'cl-kv' }, [el('dt', { text: label }), el('dd', { text: value == null || value === '' ? '—' : String(value) })]);
  }

  function renderDetail(box, data, onChanged) {
    var item = data.item;
    var f = item;
    var head = el('header', { class: 'cl-detail-head' }, [
      el('h3', { text: item.title }), item.synthetic ? badge() : null,
      el('span', { class: 'cl-meta', text: ' 현재 v' + item.version })
    ]);
    var meta = el('dl', { class: 'cl-kvs' }, [
      kv('출처 구분', f.origin === 'url' ? '공개 URL 가져오기' : '직접 등록'),
      kv('요청 주소', f.source_url), kv('최종 주소', f.final_url),
      kv('수집 시각', f.fetched_at), kv('내용 식별값(SHA-256)', f.content_hash),
      kv('기록 ID', item.id), kv('개정 횟수', f.revision_count || 0)
    ]);
    if (f.source_url) {
      var openLink = el('a', { href: f.final_url || f.source_url, target: '_blank', rel: 'noopener noreferrer', text: '원출처 열기' });
      meta.appendChild(el('div', { class: 'cl-kv' }, [openLink]));
    }

    var original = el('section', { class: 'cl-section' }, [
      el('h4', { text: '원문 (수집 그대로, 정리본과 분리)' }),
      el('pre', { class: 'cl-original', text: item.body || '' }),
      el('p', { class: 'cl-meta', text: '근거: ' + (f.evidence || '없음') })
    ]);

    var revision = revisionForm(item, function (res) {
      toast(res.data.changed === false ? '바뀐 내용이 없습니다.' : '새 버전으로 저장했습니다.');
      onChanged(item.id);
    });
    var metaForm = metadataForm(item, function () { toast('정보를 저장했습니다.'); onChanged(item.id); });
    var draft = draftPanel(item, function () { onChanged(item.id); });
    var history = historyPanel(data);

    box.appendChild(head);
    box.appendChild(meta);
    box.appendChild(original);
    box.appendChild(section('정보 수정 (같은 버전 번호에서 저장)', [metaForm]));
    box.appendChild(section('원문·근거 개정 (사유와 담당자 필수)', [revision]));
    box.appendChild(section('정리 초안 (규칙 기반, AI 미사용)', [draft]));
    box.appendChild(history);
  }

  function metadataForm(item, onSaved) {
    var fields = {
      title: input('title', item.title),
      kind: input('kind', item.kind),
      source_type: input('source_type', item.source_type),
      source_name: input('source_name', item.source_name),
      source_url: input('source_url', item.source_url)
    };
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, Object.keys(fields).map(function (k) {
      return field({ title: '제목', kind: '분야', source_type: '유형', source_name: '출처 이름', source_url: '출처 주소' }[k], fields[k]);
    }).concat([el('button', { type: 'submit', class: 'cl-btn', text: '정보 저장' }), status]));
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var payload = { expected_version: item.version };
      Object.keys(fields).forEach(function (k) { payload[k] = fields[k].value; });
      request('PATCH', '/api/collect/materials/' + encodeURIComponent(item.id), payload).then(function (res) {
        if (res.ok) { onSaved(res); return; }
        statusLine(status, errorText(res), true);
      });
    });
    return form;
  }

  function revisionForm(item, onSaved) {
    var body = input('body', item.body, true);
    var evidence = input('evidence', item.evidence || '', true);
    var reason = input('reason', '');
    var owner = input('owner', '');
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var form = el('form', { class: 'cl-form' }, [
      field('원문', body), field('근거', evidence), field('개정 사유', reason), field('담당자', owner),
      el('p', { class: 'cl-hint', text: '저장하면 새 버전이 되고, 이 원문을 쓰는 작업은 재검토가 필요해집니다. 이전 원문은 남습니다.' }),
      el('button', { type: 'submit', class: 'cl-btn', text: '개정 저장' }), status
    ]);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      statusLine(status, '저장하는 중입니다…');
      request('PATCH', '/api/collect/materials/' + encodeURIComponent(item.id), {
        expected_version: item.version, body: body.value, evidence: evidence.value,
        reason: reason.value, owner: owner.value
      }).then(function (res) {
        if (res.ok) { onSaved(res); return; }
        statusLine(status, errorText(res), true);
      });
    });
    return form;
  }

  function draftPanel(item, onChanged) {
    var status = el('p', { class: 'cl-status', 'aria-live': 'polite' });
    var out = el('div', null);
    var draftBtn = el('button', { type: 'button', class: 'cl-btn', text: '정리 초안 만들기' });
    draftBtn.addEventListener('click', function () {
      statusLine(status, '초안을 만드는 중입니다…');
      request('POST', '/api/collect/materials/' + encodeURIComponent(item.id) + '/draft', { expected_version: item.version })
        .then(function (res) {
          if (res.ok) { onChanged(item.id); return; }
          statusLine(status, errorText(res), true);
        });
    });
    out.appendChild(draftBtn);
    if (item.draft) {
      out.appendChild(el('pre', { class: 'cl-draft', text: item.draft.text }));
      out.appendChild(el('p', { class: 'cl-meta', text: '초안 출처: 규칙 기반 · 원문 식별값 ' + String(item.draft.body_hash).slice(0, 12) + '…' }));
      var reviewer = input('reviewer', '');
      var notes = input('correction_notes', '');
      var corrected = input('text', item.draft.text, true);
      var adoptBtn = el('button', { type: 'button', class: 'cl-btn', text: '초안 그대로 채택' });
      var correctBtn = el('button', { type: 'button', class: 'cl-btn', text: '교정해서 저장' });
      adoptBtn.addEventListener('click', function () {
        if (!reviewer.value.trim()) { statusLine(status, '담당자를 입력해 주세요.', true); return; }
        request('POST', '/api/collect/materials/' + encodeURIComponent(item.id) + '/organized',
          { expected_version: item.version, action: 'adopt', reviewer: reviewer.value }).then(function (res) {
            if (res.ok) { onChanged(item.id); return; }
            statusLine(status, errorText(res), true);
          });
      });
      correctBtn.addEventListener('click', function () {
        request('POST', '/api/collect/materials/' + encodeURIComponent(item.id) + '/organized',
          { expected_version: item.version, action: 'correct', text: corrected.value,
            correction_notes: notes.value, reviewer: reviewer.value }).then(function (res) {
            if (res.ok) { onChanged(item.id); return; }
            statusLine(status, errorText(res), true);
          });
      });
      out.appendChild(el('div', { class: 'cl-form' }, [
        field('담당자', reviewer), field('교정 사유', notes), field('교정본', corrected),
        el('div', { class: 'cl-actions' }, [adoptBtn, correctBtn])
      ]));
    }
    if (item.organized) {
      var stale = item.organized_stale ? ' · 원문이 바뀌어 다시 확인이 필요합니다' : '';
      out.appendChild(el('h4', { text: '채택된 정리본 (원문과 별도 저장)' }));
      out.appendChild(el('pre', { class: 'cl-draft', text: item.organized.text }));
      out.appendChild(el('p', { class: 'cl-meta', text: '정리본 v' + item.organized.organized_version + ' · ' +
        (item.organized.origin === 'rule_draft_adopted' ? '초안 채택' : '사람 교정') + ' · 담당 ' + item.organized.reviewer + stale }));
    }
    out.appendChild(status);
    return out;
  }

  function historyPanel(data) {
    var events = (data.events || []).map(function (e) {
      return el('li', null, [el('span', { text: e.created_at + ' · ' + e.kind + ' · v' + (e.from_version || '?') + '→v' + (e.to_version || '?') +
        (e.reason ? ' · 사유: ' + e.reason : '') + (e.owner ? ' · 담당: ' + e.owner : '') })]);
    });
    var revs = (data.revisions || []).map(function (r) {
      return el('li', null, [el('span', { text: 'v' + r.version + ' · ' + r.created_at })]);
    });
    var works = (data.dependent_works || []).map(function (w) {
      return el('li', null, [el('span', { text: w.title + ' (v' + w.version + ')' })]);
    });
    return section('개정·사건 이력', [
      el('h4', { text: '사건' }), el('ul', { class: 'cl-plain' }, events.length ? events : [el('li', { text: '사건 없음' })]),
      el('h4', { text: '버전' }), el('ul', { class: 'cl-plain' }, revs),
      el('h4', { text: '이 원문을 쓰는 작업' }), el('ul', { class: 'cl-plain' }, works.length ? works : [el('li', { text: '연결된 작업 없음' })])
    ]);
  }

  window.FMScreens.s1 = function (container) {
    container.textContent = '';
    var detailHost = el('div', { class: 'cl-detail-host' });
    var list = listPanel(function (id) { openDetail(id); });

    function openDetail(id) {
      detailHost.textContent = '';
      detailHost.appendChild(el('button', { type: 'button', class: 'cl-btn cl-ghost', text: '목록으로' }, []));
      detailHost.firstChild.addEventListener('click', function () { detailHost.textContent = ''; });
      detailHost.appendChild(detailPanel(id, function (changedId) { openDetail(changedId); list.reload(); }));
      detailHost.scrollIntoView && detailHost.scrollIntoView({ block: 'start' });
    }

    function onCreated(id) { list.reload(); openDetail(id); }

    container.appendChild(el('div', { class: 'cl-screen' }, [
      el('header', { class: 'cl-head' }, [el('h2', { text: 'S1 자료 수집' }),
        el('p', { class: 'cl-hint', text: '합성 가공 사례·공개 연습자료만 등록합니다. 원문은 덮어쓰지 않습니다.' })]),
      fetchForm(container, onCreated),
      manualForm(onCreated),
      list.node,
      detailHost
    ]));
  };
})();
