/*
 * ui-forms.js — 등록 폼·상세·상태 안내의 공통 틀 (STORY 1.3)
 *
 * window.FMForms
 *   openForm({title, fields:[{name,label,type,required}], onSubmit, submitLabel?})
 *     -> Promise<values | null>   values: 공백 제거한 문자열, 닫으면 null
 *   openDetail({title, rows:[{label,value}]})
 *     -> Promise<void>            읽기 전용 상세 패널, 닫으면 resolve
 *   showToast(message)            성공 안내 (aria-live polite)
 *
 * 공통 규칙
 *  - 모든 입력은 명시적 <label for>로 연결하고 '필수'/'선택' 표시를 글자로 함께 둔다.
 *  - 저장 중에는 제출 버튼을 disabled로 바꾸고 "저장 중…"을 안내한다. 중복 제출을 막는다.
 *  - 실패하면 오류를 aria-live 영역에 알리고 입력값은 지우지 않는다.
 *  - Esc로 닫는다(저장 중에는 닫지 않는다). 닫으면 호출한 요소로 포커스를 돌린다.
 *  - 실패 메시지는 err.userMessage만 사용한다. 원시 err.message·스택은 화면에 내보내지 않는다.
 *  - localStorage 등 브라우저 저장소는 사용하지 않는다.
 */
(function () {
  'use strict';

  var TOAST_MS = 4000;
  var ALLOWED_TYPES = ['text', 'url', 'number', 'email', 'date', 'textarea'];
  var FOCUSABLE =
    'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
    'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  var MSG = {
    saving: '저장 중…',
    saved: '저장했어요. 입력한 내용이 반영됐어요.',
    failed: '저장하지 못했어요. 입력한 내용은 그대로 있어요. 같은 내용으로 다시 저장할 수 있어요.',
    closeBlocked: '저장이 끝난 뒤 닫을 수 있어요.',
    submit: '저장',
    cancel: '취소',
    close: '닫기',
    required: function (label) { return label + '을(를) 입력해 주세요.'; },
    url: function (label) { return label + '은(는) http 또는 https 주소로 입력해 주세요.'; },
    number: function (label) { return label + '은(는) 숫자로 입력해 주세요.'; }
  };

  var seq = 0;
  function uid(prefix) {
    seq += 1;
    return 'fm-' + prefix + '-' + seq;
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (key === 'text') node.textContent = value;
        else if (key === 'className') node.className = value;
        else node.setAttribute(key, value);
      });
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function getRoot() {
    var root = document.getElementById('dialog-root');
    if (!root) {
      root = el('div', { id: 'dialog-root' });
      document.body.appendChild(root);
    }
    return root;
  }

  function isHttpUrl(value) {
    try {
      var url = new URL(value);
      return url.protocol === 'http:' || url.protocol === 'https:';
    } catch (e) {
      return false;
    }
  }

  function userMessage(err) {
    if (err && typeof err.userMessage === 'string' && err.userMessage) return err.userMessage;
    return MSG.failed;
  }

  function visibleFocusables(container) {
    return Array.prototype.filter.call(container.querySelectorAll(FOCUSABLE), function (node) {
      return node.getClientRects().length > 0;
    });
  }

  // Tab/Shift+Tab이 대화상자 밖으로 나가지 않게 가둔다.
  function trapTab(dialog, ev) {
    if (ev.key !== 'Tab') return;
    var items = visibleFocusables(dialog);
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    var outside = !dialog.contains(active);
    if (ev.shiftKey && (active === first || outside)) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && (active === last || outside)) {
      ev.preventDefault();
      first.focus();
    }
  }

  function restoreFocus(opener) {
    if (opener && opener.isConnected && typeof opener.focus === 'function') {
      opener.focus();
    }
  }

  // 공통 열기: dialog를 루트에 붙이고 modal로 연다. 닫히면 제거하고 호출 위치로 포커스를 돌린다.
  function mountDialog(dialog, opener, onClosed) {
    var settled = false;
    function settle() {
      if (settled) return;
      settled = true;
      onClosed();
    }
    dialog.addEventListener('close', function () {
      dialog.remove();
      settle();
      restoreFocus(opener);
    });
    getRoot().appendChild(dialog);
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      // showModal을 지원하지 않는 낡은 브라우저: 비모달로라도 열어 내용을 보여준다.
      dialog.setAttribute('open', '');
    }
    return { settle: settle };
  }

  function ensureToastRegion() {
    var region = document.getElementById('fm-toast-region');
    if (!region) {
      region = el('div', {
        id: 'fm-toast-region',
        className: 'fm-toast-region',
        role: 'status',
        'aria-live': 'polite'
      });
      getRoot().appendChild(region);
    }
    return region;
  }

  function showToast(message) {
    var region = ensureToastRegion();
    region.textContent = '';
    var toast = el('p', { className: 'fm-toast', text: String(message || MSG.saved) });
    region.appendChild(toast);
    window.setTimeout(function () {
      toast.remove();
    }, TOAST_MS);
  }

  function openForm(options) {
    options = options || {};
    var title = String(options.title || '입력');
    var fields = Array.isArray(options.fields) ? options.fields : [];
    var onSubmit = typeof options.onSubmit === 'function' ? options.onSubmit : function () {};
    var submitLabel = String(options.submitLabel || MSG.submit);
    var opener = document.activeElement;
    var titleId = uid('title');
    var controls = {};
    var saving = false;

    return new Promise(function (resolve) {
      var dialog = el('dialog', { className: 'fm-dialog', 'aria-labelledby': titleId });
      var form = el('form', { className: 'fm-form', novalidate: '' });
      var heading = el('h2', { id: titleId, className: 'fm-title', text: title, tabindex: '-1' });
      var errorBox = el('div', {
        className: 'fm-error',
        'aria-live': 'assertive',
        tabindex: '-1',
        hidden: ''
      });
      var fieldWrap = el('div', { className: 'fm-fields' });

      fields.forEach(function (field) {
        var name = String(field.name);
        var label = String(field.label || name);
        var type = ALLOWED_TYPES.indexOf(field.type) >= 0 ? field.type : 'text';
        var inputId = uid('input');
        var errorId = uid('ferr');
        var labelEl = el('label', { className: 'fm-label', 'for': inputId }, [
          el('span', { className: 'fm-label-text', text: label }),
          el('span', {
            className: field.required ? 'fm-badge fm-badge-required' : 'fm-badge',
            text: field.required ? '필수' : '선택'
          })
        ]);
        var input = type === 'textarea'
          ? el('textarea', { id: inputId, name: name, rows: '4', className: 'fm-input' })
          : el('input', { id: inputId, name: name, type: type, className: 'fm-input', autocomplete: 'off' });
        if (field.required) input.setAttribute('aria-required', 'true');
        input.setAttribute('aria-describedby', errorId);
        var fieldError = el('p', { id: errorId, className: 'fm-field-error', hidden: '' });
        controls[name] = { input: input, error: fieldError, type: type, label: label, required: !!field.required };
        fieldWrap.appendChild(el('div', { className: 'fm-field' }, [labelEl, input, fieldError]));
      });

      var status = el('p', { className: 'fm-status', 'aria-live': 'polite', tabindex: '-1' });
      var cancel = el('button', { type: 'button', className: 'fm-btn', text: MSG.cancel });
      var submit = el('button', { type: 'submit', className: 'fm-btn fm-btn-primary', text: submitLabel });
      var actions = el('div', { className: 'fm-actions' }, [
        status,
        el('div', { className: 'fm-buttons' }, [cancel, submit])
      ]);

      form.appendChild(heading);
      form.appendChild(errorBox);
      form.appendChild(fieldWrap);
      form.appendChild(actions);
      dialog.appendChild(form);

      function showError(message) {
        errorBox.textContent = message;
        errorBox.hidden = false;
      }

      function clearError() {
        errorBox.textContent = '';
        errorBox.hidden = true;
      }

      function validate() {
        var messages = [];
        var first = null;
        fields.forEach(function (field) {
          var name = String(field.name);
          var c = controls[name];
          var value = c.input.value.trim();
          var message = '';
          if (c.required && value === '') {
            message = MSG.required(c.label);
          } else if (value !== '' && c.type === 'url' && !isHttpUrl(value)) {
            message = MSG.url(c.label);
          } else if (value !== '' && c.type === 'number' && !isFinite(Number(value))) {
            message = MSG.number(c.label);
          }
          c.input.setAttribute('aria-invalid', message ? 'true' : 'false');
          c.error.textContent = message;
          c.error.hidden = !message;
          if (message) {
            messages.push(message);
            if (!first) first = c.input;
          }
        });
        return { messages: messages, first: first };
      }

      function collect() {
        var out = {};
        fields.forEach(function (field) {
          var name = String(field.name);
          out[name] = controls[name].input.value.trim();
        });
        return out;
      }

      function setBusy(on) {
        saving = on;
        form.setAttribute('aria-busy', on ? 'true' : 'false');
        submit.textContent = on ? MSG.saving : submitLabel;
        status.textContent = on ? MSG.saving : '';
        if (on && document.activeElement === submit) {
          // disabled 전환 시 포커스가 사라지지 않도록 상태 영역으로 옮긴다.
          status.focus();
        }
        submit.disabled = on;
        cancel.disabled = on;
      }

      function finishSuccess(values) {
        // resolve를 먼저 해서 닫힘 이벤트의 resolve(null)보다 값이 우선하게 한다.
        resolve(values);
        dialog.close();
        showToast(MSG.saved);
      }

      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (saving) return;
        clearError();
        var result = validate();
        if (result.messages.length) {
          showError(result.messages.join(' '));
          if (result.first) result.first.focus();
          return;
        }
        var values = collect();
        setBusy(true);
        Promise.resolve()
          .then(function () { return onSubmit(values); })
          .then(function () {
            setBusy(false);
            finishSuccess(values);
          }, function (err) {
            // 입력값은 건드리지 않는다. 같은 내용으로 다시 저장할 수 있다.
            setBusy(false);
            showError(userMessage(err));
            errorBox.focus();
          });
      });

      cancel.addEventListener('click', function () {
        if (saving) return;
        dialog.close();
      });

      dialog.addEventListener('cancel', function (ev) {
        // Esc: 저장 중이면 닫지 않고 안내만 한다.
        if (saving) {
          ev.preventDefault();
          status.textContent = MSG.closeBlocked;
        }
      });

      dialog.addEventListener('keydown', function (ev) {
        trapTab(dialog, ev);
      });

      mountDialog(dialog, opener, function () {
        resolve(null);
      });

      var firstField = fields.length ? controls[String(fields[0].name)].input : null;
      if (firstField) firstField.focus();
      else cancel.focus();
    });
  }

  function openDetail(options) {
    options = options || {};
    var opener = document.activeElement;
    var titleId = uid('title');
    return new Promise(function (resolve) {
      var dialog = el('dialog', { className: 'fm-dialog fm-detail', 'aria-labelledby': titleId });
      var heading = el('h2', { id: titleId, className: 'fm-title', text: String(options.title || '상세') });
      var list = el('dl', { className: 'fm-rows' });
      (Array.isArray(options.rows) ? options.rows : []).forEach(function (row) {
        var value = row.value;
        var text = value === null || value === undefined || value === '' ? '없음' : String(value);
        list.appendChild(el('dt', { className: 'fm-row-label', text: String(row.label || '') }));
        list.appendChild(el('dd', { className: 'fm-row-value', text: text }));
      });
      var close = el('button', { type: 'button', className: 'fm-btn fm-btn-primary', text: MSG.close });
      close.addEventListener('click', function () { dialog.close(); });
      var wrap = el('div', { className: 'fm-form' }, [
        heading,
        list,
        el('div', { className: 'fm-actions' }, [el('div', { className: 'fm-buttons' }, [close])])
      ]);
      dialog.appendChild(wrap);
      dialog.addEventListener('keydown', function (ev) { trapTab(dialog, ev); });
      mountDialog(dialog, opener, function () { resolve(undefined); });
      close.focus();
    });
  }

  var FMForms = {
    openForm: openForm,
    openDetail: openDetail,
    showToast: showToast
  };
  window.FMForms = FMForms;

  // 데모: #forms-demo-button 을 누르면 열리는 등록 폼. 값이 정확히 "fail"이면 저장이 실패한다.
  function demoSubmit(values) {
    return new Promise(function (resolve, reject) {
      window.setTimeout(function () {
        var failed = Object.keys(values).some(function (key) { return values[key] === 'fail'; });
        if (failed) {
          reject({ userMessage: '데모 저장이 실패했어요. 입력한 내용은 그대로 있어요.' });
        } else {
          resolve();
        }
      }, 600);
    });
  }

  function init() {
    getRoot();
    ensureToastRegion();
    // 화면 셸(app.js)의 다음 행동 버튼이 보내는 요청을 같은 공통 폼으로 연다.
    document.addEventListener('fm:open-form', function () {
      var opener = document.activeElement;
      FMForms.openForm({
        title: (opener && opener.textContent.trim()) || '등록',
        fields: [
          { name: 'title', label: '제목', type: 'text', required: true },
          { name: 'note', label: '메모', type: 'textarea', required: false }
        ],
        onSubmit: demoSubmit
      });
    });
    var button = document.getElementById('forms-demo-button');
    if (button && !button.getAttribute('data-fm-wired')) {
      button.setAttribute('data-fm-wired', '1');
      button.addEventListener('click', function () {
        FMForms.openForm({
          title: '데모 등록',
          fields: [
            { name: 'title', label: '제목', type: 'text', required: true },
            { name: 'link', label: '출처 주소', type: 'url', required: false },
            { name: 'note', label: '메모', type: 'textarea', required: false }
          ],
          onSubmit: demoSubmit
        });
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
