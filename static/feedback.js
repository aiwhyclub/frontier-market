/* S0 dashboard + buyer follow-up opinions (EPIC 10, F8.1~F8.4, F2.4, F2.5).
 * window.FMScreens.dashboard(container) renders the S0 screen.
 * window.FMFeedback.open(kind) opens the opinion form (kind: sample_review | pilot | topic).
 * All server text goes through textContent. Failures keep the user's input.
 */
(function () {
  'use strict';

  window.FMScreens = window.FMScreens || {};

  var API = '/api/feedback';
  var KIND_LABELS = FMI18n.lazy(function () { return { sample_review: t('feedback.sample_review'), pilot: t('feedback.paid_pilot_terms'), topic: t('feedback.topic_needed') }; });
  var KIND_HINT = FMI18n.lazy(function () { return {
    sample_review: t('feedback.leave_sample_review_feedback_saving'),
    pilot: t('feedback.leave_feedback_on_paid_pilot'),
    topic: t('feedback.leave_a_topic_you_need')
  }; });
  var TYPE_LABELS = FMI18n.lazy(function () { return {
    materials: t('collect.materials'), works: t('feedback.work_raw_refined'), catalog: t('app.catalog'),
    projects: t('feedback.fde_project'), companies: t('app.virtual_company')
  }; });
  var LINK_TYPES = ['materials', 'works', 'catalog', 'projects', 'companies'];
  var TYPE_PAGE = {
    materials: 's1', rights: 's2', experts: 's3', works: 's5', catalog: 's4',
    projects: 's6', companies: 's7', feedback: 'dashboard'
  };
  var STAGE_PAGE_LABEL = FMI18n.lazy(function () { return {
    materials: t('collect.s1_collect'), rights: 'S2 DB', experts: t('feedback.s3_experts'), works: t('feedback.s5_refine_verify'),
    catalog: t('feedback.s4_catalog'), projects: 'S6 FDE', companies: t('company.s7_virtual_company'), feedback: t('feedback.s0_dashboard')
  }; });
  function defaultError() { return t('feedback.could_not_save_your_input'); }

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
    return item && item.synthetic ? el('span', { className: 'fb-badge', text: t('catalog.synthetic') }) : null;
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
          err.userMessage = e.message || defaultError();
          err.fieldErrors = e.field_errors || {};
          err.status = res.status;
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error('network');
      err.userMessage = t('collect.could_not_reach_the_server');
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
      el('h2', { id: 'fb-chooser-title', className: 'fm-title', text: t('feedback.choose_buyer_feedback_type') }),
      el('p', { className: 'fm-fields', text: t('feedback.pick_one_of_the_three') })
    ].concat(buttons.map(function (b) { return el('div', { className: 'fb-kind-row' }, [b]); })));
    var close = el('button', { type: 'button', className: 'fm-btn', text: t('feedback.close') });
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
      title: KIND_LABELS[kind] + ' ' + t('feedback.feedback'),
      submitLabel: t('feedback.save_feedback'),
      fields: [
        { name: 'title', label: kind === 'topic' ? t('feedback.topic_name') : t('feedback.target_name'), type: 'text', required: true },
        { name: 'note', label: t('feedback.feedback_2'), type: 'textarea', required: true },
        { name: 'target_note', label: t('feedback.notes_on_target_and_author'), type: 'textarea', required: false }
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
    setState(container, 'loading', t('feedback.loading_feedback'));
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
      status.placeholder = t('feedback.e_g_confirm_sample_review');

      var linkSelects = {};
      var linkRow = el('div', { className: 'fb-links' });
      LINK_TYPES.forEach(function (type) {
        var current = links.filter(function (l) { return l.type === type; })[0];
        var select = el('select', { className: 'fm-input' });
        select.appendChild(el('option', { value: '', text: t('feedback.no_link') }));
        if (current) {
          select.appendChild(el('option', { value: current.id, text: current.title || current.id }));
          select.value = current.id;
        }
        linkSelects[type] = select;
        var goBtn = current ? el('button', {
          type: 'button', className: 'fm-btn fb-go', text: t('feedback.go'),
          'aria-label': t('feedback.go_to_record', { type: TYPE_LABELS[type] }),
          onclick: function () { location.hash = '#' + (TYPE_PAGE[type] || 'dashboard'); }
        }) : null;
        linkRow.appendChild(field(TYPE_LABELS[type] + ' ' + t('feedback.supplement_target'), select,
          current ? null : t('feedback.to_link_one_choose_a')));
        if (goBtn) linkRow.appendChild(goBtn);
        // Load the record choices for this type (only ones not yet shown).
        recordApi(type).then(function (items) {
          items.forEach(function (rec) {
            if (rec.id === select.value) return;
            select.appendChild(el('option', { value: rec.id, text: (rec.title || rec.id) + ' · ' + rec.id }));
          });
        }).catch(function () {
          select.appendChild(el('option', { value: '', text: t('feedback.could_not_load_the_list') }));
        });
      });

      var save = el('button', { type: 'button', className: 'fm-btn fm-btn-primary', text: t('experts.save') });
      var trailBox = el('div', { className: 'fb-trail' });
      var trailBtn = el('button', { type: 'button', className: 'fm-btn', text: t('feedback.follow_related_records') });
      trailBtn.onclick = function () { showTrail(trailBox, feedbackId); };

      save.onclick = function () {
        msg.textContent = '';
        var statusValue = status.value.trim();
        if (statusValue.length > 80) {
          msg.textContent = t('feedback.enter_the_processing_status_in');
          return;
        }
        if (!title.value.trim() || !note.value.trim()) {
          msg.textContent = t('feedback.topic_name_and_feedback_cannot');
          return;
        }
        var links = [];
        LINK_TYPES.forEach(function (type) {
          if (linkSelects[type].value) links.push({ type: type, id: linkSelects[type].value });
        });
        save.disabled = true;
        save.textContent = t('feedback.saving');
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
          save.textContent = t('experts.save');
          msg.textContent = err.userMessage || defaultError();
        });
      };

      container.appendChild(el('div', { className: 'fb-detail-head' }, [
        el('h3', { text: item.title }),
        synthBadge(item),
        el('span', { className: 'fb-kind', text: KIND_LABELS[item.kind] || '' })
      ]));
      container.appendChild(el('div', { className: 'fb-form' }, [
        field(t('collect.type'), kindSelect),
        field(t('feedback.topic_target_name'), title),
        field(t('feedback.feedback_2'), note),
        field(t('feedback.notes_on_target_and_author'), targetNote),
        field(t('feedback.processing_status'), status, t('feedback.blank_means_unspecified_it_never')),
        linkRow,
        msg,
        el('div', { className: 'fb-actions' }, [save, trailBtn])
      ]));
      container.appendChild(trailBox);
    }).catch(function (err) {
      setState(container, 'error', err.userMessage || t('feedback.could_not_load_the_feedback'));
    });
  }

  function nodeLink(node) {
    var label = (TYPE_LABELS[node.type] || node.type) + ' · ' + (node.title || node.id);
    return el('li', null, [
      el('span', { className: 'fb-node-label', text: label }),
      synthBadge(node),
      el('button', {
        type: 'button', className: 'fm-btn fb-go', text: t('feedback.open_stage', { stage: STAGE_PAGE_LABEL[node.type] || t('feedback.go') }),
        onclick: function () { location.hash = '#' + (TYPE_PAGE[node.type] || 'dashboard'); }
      })
    ]);
  }

  function showTrail(box, feedbackId) {
    box.textContent = '';
    box.appendChild(el('p', { className: 'fb-state', text: t('feedback.finding_related_records'), role: 'status' }));
    api('/trail/feedback/' + encodeURIComponent(feedbackId)).then(function (data) {
      box.textContent = '';
      box.appendChild(el('h4', { text: t('feedback.supplement_target_the_record_this') }));
      box.appendChild(data.outgoing.length
        ? el('ul', { className: 'fb-list' }, data.outgoing.map(nodeLink))
        : el('p', { className: 'fb-state', text: t('feedback.no_linked_supplement_target') }));
      box.appendChild(el('h4', { text: t('feedback.records_pointing_to_this_feedback') }));
      box.appendChild(data.incoming.length
        ? el('ul', { className: 'fb-list' }, data.incoming.map(nodeLink))
        : el('p', { className: 'fb-state', text: t('feedback.none') }));
    }).catch(function (err) {
      box.textContent = '';
      box.appendChild(el('p', { className: 'fb-state fb-state-error', text: err.userMessage || t('feedback.could_not_load_it'), role: 'alert' }));
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
    var meta = [TYPE_LABELS[node.type] || node.type, node.status ? t('feedback.status') + ' ' + node.status : null]
      .filter(Boolean).join(' · ');
    return el('li', null, [
      el('span', { className: 'fb-node-label', text: node.title || node.id }),
      synthBadge(node),
      el('span', { className: 'fb-meta', text: ' ' + meta }),
      el('button', {
        type: 'button', className: 'fm-btn fb-go', text: t('feedback.open'),
        onclick: function () { location.hash = '#' + (TYPE_PAGE[node.type] || 'dashboard'); }
      })
    ]);
  }

  function renderDashboard(container, data, reload) {
    container.textContent = '';
    container.appendChild(el('header', { className: 'fb-page-head' }, [
      el('h1', { text: t('feedback.s0_dashboard') }),
      el('p', { text: t('feedback.only_saved_records_are_counted') })
    ]));

    var stageSection = sectionEl(t('feedback.saved_records_by_stage'), 'fb-stages-title');
    stageSection.appendChild(el('ul', { className: 'fb-stage-grid' }, data.stages.map(function (s) {
      return el('li', { className: 'fb-stage-card' }, [
        el('a', { href: '#' + s.page }, [
          el('span', { className: 'fb-stage-code', text: s.stage }),
          el('span', { className: 'fb-stage-name', text: s.label }),
          el('strong', { className: 'fb-stage-count', text: String(s.count) + t('collect.items') })
        ])
      ]);
    })));
    container.appendChild(stageSection);

    var feedbackSection = sectionEl(t('feedback.buyer_feedback_sample_review_paid'), 'fb-feedback-title');
    var actions = el('div', { className: 'fb-actions' });
    Object.keys(KIND_LABELS).forEach(function (kind) {
      actions.appendChild(el('button', {
        type: 'button', className: 'fm-btn fm-btn-primary', text: t('feedback.add_kind', { kind: KIND_LABELS[kind] }),
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
        var label = k ? KIND_LABELS[k] : t('feedback.all');
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
    feedbackSection.appendChild(el('h3', { text: t('feedback.next_action_per_feedback') }));
    feedbackSection.appendChild(listOrEmpty(data.feedback,
      t('feedback.no_feedback_saved_yet_use'),
      function (f) {
        return el('li', null, [
          el('span', { className: 'fb-node-label', text: f.title }),
          el('span', { className: 'fb-kind', text: f.kind_label }),
          synthBadge(f),
          el('span', { className: 'fb-meta', text: ' ' + f.next_action }),
          el('button', {
            type: 'button', className: 'fm-btn fb-go', text: t('feedback.details'),
            onclick: function () { detailBox.textContent = ''; renderDetail(detailBox, f.id, reload); detailBox.scrollIntoView && detailBox.scrollIntoView({ block: 'nearest' }); }
          })
        ]);
      }));
    container.appendChild(feedbackSection);
    showKind('');

    var pendingSection = sectionEl(t('feedback.awaiting_review') + data.pending_total + t('feedback.text'), 'fb-pending-title');
    pendingSection.appendChild(listOrEmpty(data.pending_reviews, t('feedback.no_records_in_the_awaiting'), recordRow));
    container.appendChild(pendingSection);

    var holdSection = sectionEl(t('feedback.on_hold') + data.hold_total + t('feedback.text'), 'fb-hold-title');
    holdSection.appendChild(listOrEmpty(data.holds, t('feedback.no_records_on_hold'), recordRow));
    container.appendChild(holdSection);

    var activitySection = sectionEl(t('feedback.recent_activity'), 'fb-activity-title');
    activitySection.appendChild(listOrEmpty(data.recent_activity, t('feedback.no_saved_activity'), function (n) {
      return el('li', null, [
        el('span', { className: 'fb-node-label', text: (n.title || n.id) }),
        synthBadge(n),
        el('span', { className: 'fb-meta', text: ' ' + (TYPE_LABELS[n.type] || n.type) + ' · v' + n.version + ' · ' + (n.updated_at || '') })
      ]);
    }));
    container.appendChild(activitySection);
  }

  function loadList(listBox, detailBox, kind, reload) {
    setState(listBox, 'loading', t('feedback.loading_the_feedback_list'));
    api(kind ? '?kind=' + encodeURIComponent(kind) : '').then(function (data) {
      listBox.textContent = '';
      if (!data.items.length) {
        listBox.appendChild(el('p', { className: 'fb-state', text: t('feedback.no_feedback_of_this_type') }));
        return;
      }
      listBox.appendChild(el('ul', { className: 'fb-list' }, data.items.map(function (item) {
        return el('li', null, [
          el('span', { className: 'fb-node-label', text: item.title }),
          el('span', { className: 'fb-kind', text: KIND_LABELS[item.kind] || '' }),
          synthBadge(item),
          el('span', { className: 'fb-meta', text: ' ' + t('feedback.status') + ' ' + (item.status || t('feedback.unspecified')) + ' ' + t('feedback.links') + ' ' + item.link_count + t('collect.items') }),
          el('button', {
            type: 'button', className: 'fm-btn fb-go', text: t('demo.view_details'),
            onclick: function () { detailBox.textContent = ''; renderDetail(detailBox, item.id, reload); }
          })
        ]);
      })));
    }).catch(function (err) {
      setState(listBox, 'error', err.userMessage || t('catalog.could_not_load_the_list'));
    });
  }

  function loadDashboard(container) {
    setState(container, 'loading', t('feedback.loading_the_dashboard'));
    api('/dashboard').then(function (data) {
      renderDashboard(container, data, function () { loadDashboard(container); });
    }).catch(function (err) {
      container.textContent = '';
      container.appendChild(el('p', { className: 'fb-state fb-state-error', role: 'alert', text: err.userMessage || t('feedback.could_not_load_the_dashboard') }));
      container.appendChild(el('button', {
        type: 'button', className: 'fm-btn', text: t('catalog.try_again'),
        onclick: function () { loadDashboard(container); }
      }));
    });
  }

  window.FMScreens.dashboard = function (container) {
    if (!container) return;
    loadDashboard(container);
  };
})();
