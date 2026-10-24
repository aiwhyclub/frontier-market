/* S0 dashboard + buyer follow-up opinions (EPIC 10, F8.1~F8.4, F2.4, F2.5).
 * window.FMScreens.dashboard(container) renders the S0 screen.
 * window.FMFeedback.open(kind) opens the opinion form (kind: sample_review | pilot | topic).
 * All server text goes through textContent. Failures keep the user's input.
 */
(function () {
  'use strict';

  window.FMScreens = window.FMScreens || {};

  var API = '/api/feedback';
  var KIND_LABELS = { sample_review: '샘플 검토', pilot: '유료 파일럿 조건', topic: '필요 주제' };
  var KIND_HINT = {
    sample_review: '샘플 검토 의견을 남깁니다. 저장은 메시지 발송이 아닙니다.',
    pilot: '유료 파일럿 조건에 대한 의견을 남깁니다. 실제 주문·결제가 아닙니다.',
    topic: '필요한 주제를 남깁니다. 카탈로그가 없어도 저장할 수 있습니다.'
  };
  var TYPE_LABELS = {
    materials: '자료', works: '작업(원시·정제)', catalog: '카탈로그',
    projects: 'FDE 프로젝트', companies: '가상회사'
  };
  var LINK_TYPES = ['materials', 'works', 'catalog', 'projects', 'companies'];
  var TYPE_PAGE = {
    materials: 's1', rights: 's2', experts: 's3', works: 's5', catalog: 's4',
    projects: 's6', companies: 's7', feedback: 'dashboard'
  };
  var STAGE_PAGE_LABEL = {
    materials: 'S1 자료 수집', rights: 'S2 DB', experts: 'S3 전문가', works: 'S5 정제·검증',
    catalog: 'S4 카탈로그', projects: 'S6 FDE', companies: 'S7 가상회사', feedback: 'S0 대시보드'
  };
  var DEFAULT_ERROR = '저장하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.';

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === 'text') node.textContent = attrs[key];
        else if (key === 'className') node.className = attrs[key];
        else if (typeof attrs[key] === 'function') node[key] = attrs[key];
        else if (attrs[key] !== null && attrs[key] !== undefined) node.setAttribute(key, attrs[key]);
      });
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function synthBadge(item) {
    return item && item.synthetic ? el('span', { className: 'fb-badge', text: '합성' }) : null;
  }

  function setState(container, kind, text) {
    container.textContent = '';
    container.appendChild(el('p', {
      className: 'fb-state fb-state-' + kind,
      text: text,
      role: kind === 'error' ? 'alert' : 'status'
    }));
  }

  function api(path, options) {
    options = options || {};
    var init = { method: options.method || 'GET', headers: {} };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    return fetch(API + path, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error('request failed');
          var e = (data && data.error) || {};
          err.userMessage = e.message || DEFAULT_ERROR;
          err.fieldErrors = e.field_errors || {};
          err.status = res.status;
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error('network');
      err.userMessage = '서버에 연결하지 못했습니다. 입력은 그대로 두었으니 다시 시도해 주세요.';
      throw err;
    });
  }

  function recordApi(type) {
    return fetch('/api/records/' + type + '?limit=100').then(function (res) {
      if (!res.ok) throw new Error('records');
      return res.json();
    }).then(function (data) { return data.items || []; });
  }

  /* ---------- opinion form (F8.1, F8.2) ---------- */

  function chooser() {
    // 호출한 버튼을 기억해 닫을 때 그 위치로 포커스를 돌린다(STORY 11.3).
    var opener = document.activeElement;
    var dialog = document.createElement('dialog');
    dialog.className = 'fm-dialog fb-dialog';
    dialog.setAttribute('aria-labelledby', 'fb-chooser-title');
    var buttons = Object.keys(KIND_LABELS).map(function (kind) {
      return el('button', {
        type: 'button', className: 'fm-btn fb-kind-btn', text: KIND_LABELS[kind]
      });
    });
    var wrap = el('div', { className: 'fb-chooser fm-form' }, [
      el('h2', { id: 'fb-chooser-title', className: 'fm-title', text: '구매자 의견 유형 선택' }),
      el('p', { className: 'fm-fields', text: '세 유형 중 하나를 고르면 입력 양식이 열립니다.' })
    ].concat(buttons.map(function (b) { return el('div', { className: 'fb-kind-row' }, [b]); })));
    var close = el('button', { type: 'button', className: 'fm-btn', text: '닫기' });
    wrap.appendChild(el('div', { className: 'fm-actions' }, [el('div', { className: 'fm-buttons' }, [close])]));
    dialog.appendChild(wrap);
    // remove only: 다른 양식을 바로 여는 경우에는 호출 위치로 포커스를 돌리지 않는다.
    function done(restore) {
      dialog.remove();
      if (restore && opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
    }
    buttons.forEach(function (b, i) {
      b.onclick = function () { done(false); open(Object.keys(KIND_LABELS)[i], opener); };
    });
    close.onclick = function () { done(true); };
    dialog.addEventListener('cancel', function (ev) { ev.preventDefault(); done(true); });
    dialog.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Tab') return;
      var items = [].slice.call(dialog.querySelectorAll('button:not([disabled])'));
      var first = items[0];
      var last = items[items.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    });
    (document.getElementById('dialog-root') || document.body).appendChild(dialog);
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    buttons[0].focus();
  }

  function open(kind, opener) {
    if (!KIND_LABELS[kind]) {
      chooser();
      return;
    }
    if (!window.FMForms) return;
    window.FMForms.openForm({
      opener: opener || document.activeElement,
      title: KIND_LABELS[kind] + ' 의견 남기기',
      submitLabel: '의견 저장',
      fields: [
        { name: 'title', label: kind === 'topic' ? '주제 이름' : '대상 이름', type: 'text', required: true },
        { name: 'note', label: '의견', type: 'textarea', required: true },
        { name: 'target_note', label: '대상·작성자 메모', type: 'textarea', required: false }
      ],
      onSubmit: function (values) {
        return api('', {
          method: 'POST',
          body: { kind: kind, title: values.title, note: values.note,
                  target_note: values.target_note || '' }
        }).then(function (data) {
          window.dispatchEvent(new CustomEvent('fm:feedback-saved', { detail: data.item }));
          return data;
        });
      }
    });
  }

  window.FMFeedback = window.FMFeedback || {};
  window.FMFeedback.open = open;
  window.FMFeedback.KIND_LABELS = KIND_LABELS;

  /* ---------- opinion detail editor (F8.3, F8.4) ---------- */

  function field(labelText, control, hint) {
    var id = 'fb-' + Math.random().toString(36).slice(2, 8);
    control.id = id;
    return el('div', { className: 'fb-field' }, [
      el('label', { 'for': id, className: 'fm-label', text: labelText }),
      control,
      hint ? el('p', { className: 'fb-hint', text: hint }) : null
    ]);
  }

  function textInput(value, max) {
    var input = el('input', { type: 'text', className: 'fm-input', maxlength: String(max) });
    input.value = value || '';
    return input;
  }

  function textArea(value, max) {
    var area = el('textarea', { className: 'fm-input', rows: '4', maxlength: String(max) });
    area.value = value || '';
    return area;
  }

  function renderDetail(container, feedbackId, onChange) {
    setState(container, 'loading', '의견을 불러오는 중입니다.');
    api('/' + encodeURIComponent(feedbackId)).then(function (data) {
      var item = data.item;
      var links = data.links || [];
      container.textContent = '';
      var msg = el('p', { className: 'fb-msg', role: 'alert' });
      var kindSelect = el('select', { className: 'fm-input' });
      Object.keys(KIND_LABELS).forEach(function (k) {
        var opt = el('option', { value: k, text: KIND_LABELS[k] });
        if (k === item.kind) opt.selected = true;
        kindSelect.appendChild(opt);
      });
      var title = textInput(item.title, 120);
      var note = textArea(item.note || item.body, 2000);
      var targetNote = textArea(item.target_note, 500);
      var status = textInput(item.status || '', 80);
      status.placeholder = '예: 샘플 검토 요청 확인 (비워 두면 미지정)';

      var linkSelects = {};
      var linkRow = el('div', { className: 'fb-links' });
      LINK_TYPES.forEach(function (type) {
        var current = links.filter(function (l) { return l.type === type; })[0];
        var select = el('select', { className: 'fm-input' });
        select.appendChild(el('option', { value: '', text: '연결 없음' }));
        if (current) {
          select.appendChild(el('option', { value: current.id, text: current.title || current.id }));
          select.value = current.id;
        }
        linkSelects[type] = select;
        var goBtn = current ? el('button', {
          type: 'button', className: 'fm-btn fb-go', text: '이동',
          'aria-label': (TYPE_LABELS[type] + ' 기록으로 이동'),
          onclick: function () { location.hash = '#' + (TYPE_PAGE[type] || 'dashboard'); }
        }) : null;
        linkRow.appendChild(field(TYPE_LABELS[type] + ' 보완 대상', select,
          current ? null : '연결하려면 목록에서 기록을 고르세요.'));
        if (goBtn) linkRow.appendChild(goBtn);
        // Load the record choices for this type (only ones not yet shown).
        recordApi(type).then(function (items) {
          items.forEach(function (rec) {
            if (rec.id === select.value) return;
            select.appendChild(el('option', { value: rec.id, text: (rec.title || rec.id) + ' · ' + rec.id }));
          });
        }).catch(function () {
          select.appendChild(el('option', { value: '', text: '목록을 불러오지 못했습니다' }));
        });
      });

      var save = el('button', { type: 'button', className: 'fm-btn fm-btn-primary', text: '저장' });
      var trailBox = el('div', { className: 'fb-trail' });
      var trailBtn = el('button', { type: 'button', className: 'fm-btn', text: '관련 기록 따라가기' });
      trailBtn.onclick = function () { showTrail(trailBox, feedbackId); };

      save.onclick = function () {
        msg.textContent = '';
        var statusValue = status.value.trim();
        if (statusValue.length > 80) {
          msg.textContent = '처리 상태는 80자 이하로 입력해 주세요.';
          return;
        }
        if (!title.value.trim() || !note.value.trim()) {
          msg.textContent = '주제 이름과 의견은 비워 둘 수 없습니다.';
          return;
        }
        var links = [];
        LINK_TYPES.forEach(function (type) {
          if (linkSelects[type].value) links.push({ type: type, id: linkSelects[type].value });
        });
        save.disabled = true;
        save.textContent = '저장 중';
        api('/' + encodeURIComponent(feedbackId), {
          method: 'PATCH',
          body: {
            expected_version: item.version, kind: kindSelect.value, title: title.value.trim(),
            note: note.value.trim(), target_note: targetNote.value.trim(),
            status: statusValue || null, links: links
          }
        }).then(function () {
          window.dispatchEvent(new CustomEvent('fm:feedback-saved', { detail: { id: feedbackId } }));
          renderDetail(container, feedbackId, onChange);
          if (onChange) onChange();
        }).catch(function (err) {
          save.disabled = false;
          save.textContent = '저장';
          msg.textContent = err.userMessage || DEFAULT_ERROR;
        });
      };

      container.appendChild(el('div', { className: 'fb-detail-head' }, [
        el('h3', { text: item.title }),
        synthBadge(item),
        el('span', { className: 'fb-kind', text: KIND_LABELS[item.kind] || '' })
      ]));
      container.appendChild(el('div', { className: 'fb-form' }, [
        field('유형', kindSelect),
        field('주제·대상 이름', title),
        field('의견', note),
        field('대상·작성자 메모', targetNote),
        field('처리 상태', status, '비워 두면 미지정입니다. 자동으로 다음 단계로 넘어가지 않습니다.'),
        linkRow,
        msg,
        el('div', { className: 'fb-actions' }, [save, trailBtn])
      ]));
      container.appendChild(trailBox);
    }).catch(function (err) {
      setState(container, 'error', err.userMessage || '의견을 불러오지 못했습니다.');
    });
  }

  function nodeLink(node) {
    var label = (TYPE_LABELS[node.type] || node.type) + ' · ' + (node.title || node.id);
    return el('li', null, [
      el('span', { className: 'fb-node-label', text: label }),
      synthBadge(node),
      el('button', {
        type: 'button', className: 'fm-btn fb-go', text: (STAGE_PAGE_LABEL[node.type] || '이동') + ' 열기',
        onclick: function () { location.hash = '#' + (TYPE_PAGE[node.type] || 'dashboard'); }
      })
    ]);
  }

  function showTrail(box, feedbackId) {
    box.textContent = '';
    box.appendChild(el('p', { className: 'fb-state', text: '관련 기록을 찾는 중입니다.', role: 'status' }));
    api('/trail/feedback/' + encodeURIComponent(feedbackId)).then(function (data) {
      box.textContent = '';
      box.appendChild(el('h4', { text: '보완 대상 (이 의견이 가리키는 기록)' }));
      box.appendChild(data.outgoing.length
        ? el('ul', { className: 'fb-list' }, data.outgoing.map(nodeLink))
        : el('p', { className: 'fb-state', text: '연결된 보완 대상이 없습니다.' }));
      box.appendChild(el('h4', { text: '이 의견을 가리키는 기록' }));
      box.appendChild(data.incoming.length
        ? el('ul', { className: 'fb-list' }, data.incoming.map(nodeLink))
        : el('p', { className: 'fb-state', text: '없습니다.' }));
    }).catch(function (err) {
      box.textContent = '';
      box.appendChild(el('p', { className: 'fb-state fb-state-error', text: err.userMessage || '불러오지 못했습니다.', role: 'alert' }));
    });
  }

  /* ---------- S0 dashboard (F2.5, F8.3) ---------- */

  function sectionEl(title, id) {
    var section = el('section', { className: 'fb-section', 'aria-labelledby': id });
    section.appendChild(el('h2', { id: id, text: title }));
    return section;
  }

  function listOrEmpty(items, emptyText, render) {
    if (!items || !items.length) return el('p', { className: 'fb-state', text: emptyText });
    return el('ul', { className: 'fb-list' }, items.map(render));
  }

  function recordRow(node) {
    var meta = [TYPE_LABELS[node.type] || node.type, node.status ? '처리: ' + node.status : null]
      .filter(Boolean).join(' · ');
    return el('li', null, [
      el('span', { className: 'fb-node-label', text: node.title || node.id }),
      synthBadge(node),
      el('span', { className: 'fb-meta', text: ' ' + meta }),
      el('button', {
        type: 'button', className: 'fm-btn fb-go', text: '열기',
        onclick: function () { location.hash = '#' + (TYPE_PAGE[node.type] || 'dashboard'); }
      })
    ]);
  }

  function renderDashboard(container, data, reload) {
    container.textContent = '';
    container.appendChild(el('header', { className: 'fb-page-head' }, [
      el('h1', { text: 'S0 대시보드' }),
      el('p', { text: '저장된 기록만 집계합니다. 합성 시연 자료이며 실제 모집·계약·주문이 아닙니다.' })
    ]));

    var stageSection = sectionEl('단계별 저장 현황', 'fb-stages-title');
    stageSection.appendChild(el('ul', { className: 'fb-stage-grid' }, data.stages.map(function (s) {
      return el('li', { className: 'fb-stage-card' }, [
        el('a', { href: '#' + s.page }, [
          el('span', { className: 'fb-stage-code', text: s.stage }),
          el('span', { className: 'fb-stage-name', text: s.label }),
          el('strong', { className: 'fb-stage-count', text: String(s.count) + '건' })
        ])
      ]);
    })));
    container.appendChild(stageSection);

    var feedbackSection = sectionEl('구매자 의견 (샘플 검토 · 유료 파일럿 · 필요 주제)', 'fb-feedback-title');
    var actions = el('div', { className: 'fb-actions' });
    Object.keys(KIND_LABELS).forEach(function (kind) {
      actions.appendChild(el('button', {
        type: 'button', className: 'fm-btn fm-btn-primary', text: KIND_LABELS[kind] + ' 남기기',
        title: KIND_HINT[kind],
        onclick: function () { open(kind); }
      }));
    });
    feedbackSection.appendChild(actions);
    var tabs = el('div', { className: 'fb-tabs', role: 'tablist' });
    var listBox = el('div', { className: 'fb-feedback-list' });
    var detailBox = el('div', { className: 'fb-detail', 'aria-live': 'polite' });
    var current = '';
    function showKind(kind) {
      current = kind;
      tabs.textContent = '';
      ['', 'sample_review', 'pilot', 'topic'].forEach(function (k) {
        var label = k ? KIND_LABELS[k] : '전체';
        var btn = el('button', {
          type: 'button', className: 'fm-btn fb-tab', role: 'tab',
          'aria-selected': String(current === k), text: label
        });
        btn.onclick = function () { showKind(k); };
        tabs.appendChild(btn);
      });
      loadList(listBox, detailBox, kind, reload);
    }
    feedbackSection.appendChild(tabs);
    feedbackSection.appendChild(listBox);
    feedbackSection.appendChild(detailBox);
    feedbackSection.appendChild(el('h3', { text: '의견별 다음 행동' }));
    feedbackSection.appendChild(listOrEmpty(data.feedback,
      '아직 저장된 의견이 없습니다. 위 버튼으로 첫 의견을 남겨 보세요.',
      function (f) {
        return el('li', null, [
          el('span', { className: 'fb-node-label', text: f.title }),
          el('span', { className: 'fb-kind', text: f.kind_label }),
          synthBadge(f),
          el('span', { className: 'fb-meta', text: ' ' + f.next_action }),
          el('button', {
            type: 'button', className: 'fm-btn fb-go', text: '상세',
            onclick: function () { detailBox.textContent = ''; renderDetail(detailBox, f.id, reload); detailBox.scrollIntoView && detailBox.scrollIntoView({ block: 'nearest' }); }
          })
        ]);
      }));
    container.appendChild(feedbackSection);
    showKind('');

    var pendingSection = sectionEl('검토 대기 (' + data.pending_total + '건)', 'fb-pending-title');
    pendingSection.appendChild(listOrEmpty(data.pending_reviews, '검토 대기 상태(초안·검토 대기)의 기록이 없습니다.', recordRow));
    container.appendChild(pendingSection);

    var holdSection = sectionEl('보류 (' + data.hold_total + '건)', 'fb-hold-title');
    holdSection.appendChild(listOrEmpty(data.holds, '보류 상태의 기록이 없습니다.', recordRow));
    container.appendChild(holdSection);

    var activitySection = sectionEl('최근 활동', 'fb-activity-title');
    activitySection.appendChild(listOrEmpty(data.recent_activity, '저장된 활동이 없습니다.', function (n) {
      return el('li', null, [
        el('span', { className: 'fb-node-label', text: (n.title || n.id) }),
        synthBadge(n),
        el('span', { className: 'fb-meta', text: ' ' + (TYPE_LABELS[n.type] || n.type) + ' · v' + n.version + ' · ' + (n.updated_at || '') })
      ]);
    }));
    container.appendChild(activitySection);
  }

  function loadList(listBox, detailBox, kind, reload) {
    setState(listBox, 'loading', '의견 목록을 불러오는 중입니다.');
    api(kind ? '?kind=' + encodeURIComponent(kind) : '').then(function (data) {
      listBox.textContent = '';
      if (!data.items.length) {
        listBox.appendChild(el('p', { className: 'fb-state', text: '이 유형의 의견이 아직 없습니다.' }));
        return;
      }
      listBox.appendChild(el('ul', { className: 'fb-list' }, data.items.map(function (item) {
        return el('li', null, [
          el('span', { className: 'fb-node-label', text: item.title }),
          el('span', { className: 'fb-kind', text: KIND_LABELS[item.kind] || '' }),
          synthBadge(item),
          el('span', { className: 'fb-meta', text: ' 처리: ' + (item.status || '미지정') + ' · 연결 ' + item.link_count + '건' }),
          el('button', {
            type: 'button', className: 'fm-btn fb-go', text: '상세 보기',
            onclick: function () { detailBox.textContent = ''; renderDetail(detailBox, item.id, reload); }
          })
        ]);
      })));
    }).catch(function (err) {
      setState(listBox, 'error', err.userMessage || '목록을 불러오지 못했습니다.');
    });
  }

  function loadDashboard(container) {
    setState(container, 'loading', '대시보드를 불러오는 중입니다.');
    api('/dashboard').then(function (data) {
      renderDashboard(container, data, function () { loadDashboard(container); });
    }).catch(function (err) {
      container.textContent = '';
      container.appendChild(el('p', { className: 'fb-state fb-state-error', role: 'alert', text: err.userMessage || '대시보드를 불러오지 못했습니다.' }));
      container.appendChild(el('button', {
        type: 'button', className: 'fm-btn', text: '다시 시도',
        onclick: function () { loadDashboard(container); }
      }));
    });
  }

  window.FMScreens.dashboard = function (container) {
    if (!container) return;
    loadDashboard(container);
  };
})();
