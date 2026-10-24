/* S7 가상회사: 생성 게이트, 고정 패키지, 학습/평가 샘플, 과제 실행, JSON 내보내기. */
(function () {
  "use strict";
  window.FMScreens = window.FMScreens || {};

  var PURPOSE_LABEL = { training: t('company.training'), evaluation: t('company.evaluation') };
  var STATE_LABEL = {
    pending_review: t('company.awaiting_inspection'), inspected: t('company.inspected'), held: t('catalog.on_hold'), released: t('company.released_closed')
  };
  var ACTION_LABEL = { request_inspection: t('company.request_inspection'), hold: t('catalog.on_hold'), release: t('company.release') };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "className") node.className = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else if (k.indexOf("on") === 0) node.addEventListener(k.slice(2), attrs[k]);
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c == null) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function badge() {
    return el("span", { className: "badge-synthetic", text: t('catalog.synthetic') });
  }

  function notify(msg) {
    if (window.showToast) window.showToast(msg);
    else window.alert(msg);
  }

  function request(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        return { status: res.status, ok: res.ok, data: data };
      });
    }, function () {
      return { status: 0, ok: false, data: { error: { message: t('company.please_check_your_network_connection') } } };
    });
  }

  function errMessage(res, fallback) {
    return (res.data && res.data.error && res.data.error.message) || fallback;
  }

  function holdList(holds, onSupplement) {
    var box = el("ul", { className: "hold-list" });
    (holds || []).forEach(function (h) {
      var item = el("li", {}, [
        el("strong", { text: (h.title || h.target_id) + " (" + h.target_type + ")" }),
        el("div", { text: t('company.hold_reason') + " " + h.reason }),
        el("div", { text: t('company.purpose') + " " + (PURPOSE_LABEL[h.purpose] || h.purpose) })
      ]);
      if (h.action === "rights_supplement" && onSupplement) {
        item.appendChild(el("button", {
          type: "button", className: "btn", text: t('company.complete_usage_scope'),
          onclick: function () { onSupplement(h); }
        }));
      } else if (h.action === "approval_revoked" || h.action === "review_required") {
        item.appendChild(el("div", { text: t('company.action_re_check_after_a') }));
      }
      box.appendChild(item);
    });
    return box;
  }

  function supplementForm(holdItem, onDone) {
    var purposes = ["training", "evaluation"];
    var checks = purposes.map(function (p) {
      var cb = el("input", { type: "checkbox", value: p, name: "purpose" });
      if (holdItem.purpose === p) cb.checked = true;
      return el("label", {}, [cb, " " + PURPOSE_LABEL[p]]);
    });
    var evidence = el("input", { type: "text", name: "evidence", required: "required", maxlength: "200" });
    var confirmer = el("input", { type: "text", name: "confirmed_by", required: "required", maxlength: "60" });
    var msg = el("div", { className: "form-msg", role: "status" });
    var submit = el("button", { type: "submit", className: "btn primary", text: t('company.save_scope') });
    var form = el("form", { className: "supplement-form", onsubmit: function (e) {
      e.preventDefault();
      var picked = checks.map(function (l) { return l.querySelector("input"); })
        .filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });
      submit.disabled = true;
      request("POST", "/api/company/rights", {
        subject_type: holdItem.target_type, subject_id: holdItem.target_id, purposes: picked,
        evidence: evidence.value.trim(), confirmed_by: confirmer.value.trim()
      }).then(function (res) {
        submit.disabled = false;
        if (!res.ok) { msg.textContent = errMessage(res, t('company.could_not_save')); return; }
        notify(t('company.the_rights_scope_was_saved'));
        onDone();
      });
    } }, [
      el("p", { text: t('company.target') + " " + (holdItem.title || holdItem.target_id) + " " + t('company.saved_as_a_new_rights') }),
      el("fieldset", {}, [el("legend", { text: t('company.allowed_purposes') })].concat(checks)),
      el("label", {}, [t('company.confirmation_basis'), evidence]),
      el("label", {}, [t('company.confirmed_by'), confirmer]),
      submit, msg
    ]);
    return form;
  }

  function sampleList(pkg) {
    var wrap = el("div", { className: "samples" });
    var samples = (pkg && pkg.samples) || {};
    var training = samples.training || [];
    var evaluation = samples.evaluation || [];
    if (training.length) {
      wrap.appendChild(el("h4", { text: t('company.training_samples_input_reviewed_output') }));
      training.forEach(function (s) {
        wrap.appendChild(el("pre", { className: "sample", text: JSON.stringify(s, null, 2) }));
      });
    }
    if (evaluation.length) {
      wrap.appendChild(el("h4", { text: t('company.evaluation_samples_prompt_allowed_result') }));
      evaluation.forEach(function (s) {
        wrap.appendChild(el("pre", { className: "sample", text: JSON.stringify(s, null, 2) }));
      });
    }
    if (!training.length && !evaluation.length) {
      wrap.appendChild(el("p", { className: "empty", text: t('company.this_package_has_no_samples') }));
    }
    ((pkg && pkg.ai_drafts) || []).forEach(function (a) {
      wrap.appendChild(el("h4", { text: a.label + " " + t('company.a_document_learning_case_adopted') }));
      wrap.appendChild(el("p", { text: (a.document_title || "") + "\n" + (a.document_body || "") }));
      wrap.appendChild(el("p", { text: t('company.learning_case') + " " + (a.learning_case || t('collect.none')) }));
      wrap.appendChild(el("p", { className: "muted", text: a.note }));
    });
    return wrap;
  }

  function runsPanel(detail, companyId, reload) {
    var wrap = el("div", { className: "runs" });
    wrap.appendChild(el("h4", { text: t('company.run_tasks_only_the_selected') }));
    var tasks = (detail.package && detail.package.snapshot && detail.package.snapshot.tasks) || [];
    var runs = {};
    (detail.runs || []).forEach(function (r) { runs[r.task_id] = r; });
    if (!tasks.length) {
      wrap.appendChild(el("p", { className: "empty", text: t('company.there_are_no_tasks_to') }));
      return wrap;
    }
    tasks.forEach(function (t) {
      var run = runs[t.task_id];
      if (!run) return;
      var row = el("div", { className: "run-row" });
      var doAction = function (action) {
        request("POST", "/api/company/companies/" + companyId + "/tasks/" + t.task_id + "/action",
          { action: action, expected_run_version: run.run_version }).then(function (res) {
          if (!res.ok) notify(errMessage(res, t('company.could_not_run_it')));
          reload();
        });
      };
      row.appendChild(el("strong", { text: t.title }));
      row.appendChild(el("span", { text: " " + t('company.attempt') + " " + run.attempt + " · " + (STATE_LABEL[run.state] || run.state) +
        (run.score != null ? " " + t('company.rule_score') + " " + run.score + "/4" : "") }));
      var buttons = el("div", { className: "btn-row" });
      if (run.state !== "released") {
        buttons.appendChild(el("button", { type: "button", className: "btn", text: ACTION_LABEL.request_inspection,
          onclick: function () { doAction("request_inspection"); } }));
        buttons.appendChild(el("button", { type: "button", className: "btn", text: ACTION_LABEL.hold,
          onclick: function () { doAction("hold"); } }));
        buttons.appendChild(el("button", { type: "button", className: "btn", text: ACTION_LABEL.release,
          onclick: function () { doAction("release"); } }));
      }
      buttons.appendChild(el("button", { type: "button", className: "btn", text: t('company.reset_only_this_task_to'),
        onclick: function () {
          request("POST", "/api/company/companies/" + companyId + "/tasks/" + t.task_id + "/reset",
            { expected_run_version: run.run_version }).then(function (res) {
            if (!res.ok) notify(errMessage(res, t('company.could_not_reset')));
            reload();
          });
        } }));
      row.appendChild(buttons);
      wrap.appendChild(row);
    });
    var history = detail.history || [];
    if (history.length) {
      var list = el("ol", { className: "history" });
      history.slice(-30).forEach(function (h) {
        list.appendChild(el("li", { text: t('company.attempt') + " " + h.attempt + " · " + h.event +
          (h.to_state ? " → " + (STATE_LABEL[h.to_state] || h.to_state) : "") +
          (h.score != null ? " " + t('company.score') + " " + h.score : "") + " · " + h.task_id }));
      });
      wrap.appendChild(el("h5", { text: t('company.history') }));
      wrap.appendChild(list);
    }
    return wrap;
  }

  function detailPanel(companyId, reload, exportHolds) {
    var box = el("section", { className: "company-detail", "aria-label": t('company.company_details') });
    box.appendChild(el("p", { className: "empty", text: t('company.loading') }));
    request("GET", "/api/company/companies/" + companyId).then(function (res) {
      box.textContent = "";
      if (!res.ok) { box.appendChild(el("p", { className: "error", text: errMessage(res, t('company.could_not_load_the_company')) })); return; }
      var d = res.data;
      var pkg = d.package;
      var head = el("h3", {}, [d.item.title, " ", d.item.synthetic ? badge() : null]);
      box.appendChild(head);
      if (!pkg) {
        box.appendChild(el("p", { className: "empty", text: t('company.this_record_has_no_frozen') }));
        return;
      }
      box.appendChild(el("h4", { text: t('company.frozen_package_version_at_creation') }));
      box.appendChild(el("dl", { className: "frozen" }, [
        el("dt", { text: t('company.purpose_2') }), el("dd", { text: PURPOSE_LABEL[pkg.snapshot.company.purpose] || "" }),
        el("dt", { text: t('company.created_at') }), el("dd", { text: pkg.created_at }),
        el("dt", { text: "SHA-256" }), el("dd", { className: "hash", text: pkg.sha256 }),
        el("dt", { text: t('company.normalized') }), el("dd", { text: "FM-CJSON-1" })
      ]));
      var cur = d.current_status || { allowed: false, holds: [] };
      box.appendChild(el("h4", { text: t('company.current_rights_and_review_status') }));
      box.appendChild(el("p", { text: cur.allowed
        ? t('company.currently_allowed_further_export_is')
        : t('company.currently_on_hold_further_export') }));
      if (!cur.allowed) box.appendChild(holdList(cur.holds, function (h) {
        box.appendChild(supplementForm(h, function () { reload(); }));
      }));
      box.appendChild(sampleList(pkg.snapshot));
      box.appendChild(runsPanel(d, companyId, reload));
      var exportBtn = el("button", { type: "button", className: "btn primary", text: t('company.export_json_when_currently_allowed'),
        onclick: function () { exportCompany(companyId, exportHolds); } });
      box.appendChild(el("div", { className: "btn-row" }, [exportBtn]));
    });
    return box;
  }

  function exportCompany(companyId, showHolds) {
    fetch("/api/company/companies/" + companyId + "/export", { cache: "no-store" }).then(function (res) {
      if (res.status === 409) {
        return res.json().then(function (data) { showHolds(data.holds || [], data.error && data.error.message); });
      }
      if (!res.ok) { notify(t('company.export_failed')); return null; }
      return res.blob().then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "company-" + companyId + ".json";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
        notify(t('company.local_json_downloaded_it_is'));
      });
    }, function () { notify(t('company.please_check_your_network_connection')); });
  }

  function creationForm(cands, onCreated) {
    var msg = el("div", { className: "form-msg", role: "status" });
    var holdsBox = el("div", { className: "holds" });
    var name = el("input", { type: "text", name: "name", required: "required", maxlength: "120" });
    var purpose = el("select", { name: "purpose" }, ["training", "evaluation"].map(function (p) {
      return el("option", { value: p, text: PURPOSE_LABEL[p] });
    }));
    var catalog = el("select", { name: "catalog_id" }, cands.catalogs.map(function (c) {
      return el("option", { value: c.id, text: c.title });
    }));
    var project = el("select", { name: "project_id" }, [el("option", { value: "", text: t('company.select_a_required_project') })]
      .concat(cands.projects.map(function (p) { return el("option", { value: p.id, text: p.title }); })));
    var works = cands.works.map(function (w) {
      var cb = el("input", { type: "checkbox", value: w.id, name: "work" });
      if (!w.verified) cb.disabled = true;
      return el("label", { className: w.verified ? "" : "muted" }, [cb, " " + w.title +
        (w.verified ? "" : " " + t('company.not_yet_verified')), w.synthetic ? badge() : null]);
    });
    var fieldset = el("fieldset", {}, [el("legend", { text: t('company.work_at_least_one_verified') })].concat(works));

    function payload() {
      var ids = Array.prototype.slice.call(document.querySelectorAll("input[name=work]:checked"))
        .map(function (cb) { return cb.value; });
      return {
        name: name.value.trim(), purpose: purpose.value, catalog_id: catalog.value,
        project_id: project.value || null, work_ids: ids
      };
    }

    function showHolds(holds) {
      holdsBox.textContent = "";
      if (holds && holds.length) {
        holdsBox.appendChild(el("p", { text: t('company.on_hold_complete_the_usage') }));
        holdsBox.appendChild(holdList(holds, function (h) {
          holdsBox.appendChild(supplementForm(h, function () { holdsBox.textContent = ""; }));
        }));
      }
    }

    var checkBtn = el("button", { type: "button", className: "btn", text: t('company.check_latest_conditions_before_creating'),
      onclick: function () {
        request("POST", "/api/company/check", payload()).then(function (res) {
          msg.textContent = "";
          if (res.data.allowed) { msg.textContent = t('company.can_be_created_under_current'); holdsBox.textContent = ""; return; }
          if (res.data.errors && res.data.errors.length) msg.textContent = res.data.errors.map(function (e) { return e.message; }).join(" ");
          showHolds(res.data.holds);
        });
      } });

    /* F10.6: AI 문서·학습 사례 초안. 생성 게이트를 통과한 뒤에만 만들고, 사람이 고쳐 채택해야 스냅샷에 들어간다. */
    var aiBox = el("div", { className: "ai-draft", id: "company-ai-box" });
    var aiMsg = el("p", { className: "muted", role: "status", id: "company-ai-msg" });
    var aiBtn = el("button", { type: "button", className: "btn", id: "company-ai-make", disabled: "disabled",
      text: t('company.create_ai_document_learning_case') });
    request("GET", "/api/company/ai-status").then(function (res) {
      if (res.ok && res.data.adopted) {
        aiMsg.textContent = t('catalog.ai_available') + " " + res.data.label + t('company.only_work_that_passes_rights');
        aiBtn.removeAttribute("disabled");
      } else {
        aiMsg.textContent = t('company.the_ai_draft_was_not');
      }
    });
    aiBtn.addEventListener("click", function () {
      aiBox.textContent = "";
      request("POST", "/api/company/ai-draft", payload()).then(function (res) {
        if (res.status === 409) { aiMsg.textContent = t('company.rights_or_review_conditions_are'); showHolds(res.data.holds); return; }
        if (!res.ok) {
          aiMsg.textContent = (res.data.errors || []).map(function (x) { return x.message; }).join(" ") || errMessage(res, t('company.could_not_create_the_draft'));
          return;
        }
        if (!res.data.draft) { aiMsg.textContent = t('catalog.could_not_get_an_ai') + (res.data.ai.error || t('catalog.not_adopted')) + t('company.you_can_create_it_manually'); return; }
        aiMsg.textContent = res.data.ai.label + " " + t('company.not_saved_yet') + " " + res.data.note;
        var fields = {};
        [["document_title", t('company.document_title'), "input"], ["document_body", t('company.document_body'), "textarea"],
          ["learning_case", t('company.learning_case_2'), "textarea"]].forEach(function (f) {
          var input = el(f[2], { id: "company-ai-" + f[0] });
          input.value = res.data.draft[f[0]] || "";
          fields[f[0]] = input;
          aiBox.appendChild(el("label", {}, [f[1], input]));
        });
        aiBox.appendChild(el("button", { type: "button", className: "btn primary", text: t('company.adopt_with_edits_and_create'),
          onclick: function () {
            var body = payload();
            body.draft = {};
            Object.keys(fields).forEach(function (k) { body.draft[k] = fields[k].value.trim(); });
            request("POST", "/api/company/ai-draft/" + encodeURIComponent(res.data.ai.run_id) + "/adopt", body).then(function (r) {
              if (r.status === 201) { notify(t('company.created_a_package_that_includes')); onCreated(r.data.id); return; }
              if (r.status === 409 && r.data.holds) { aiMsg.textContent = t('company.rights_or_review_conditions_were'); showHolds(r.data.holds); return; }
              aiMsg.textContent = (r.data.errors || []).map(function (x) { return x.message; }).join(" ") || errMessage(r, t('company.could_not_adopt_it'));
            });
          } }));
      });
    });

    var submit = el("button", { type: "submit", className: "btn primary", text: t('company.create_company') });
    var form = el("form", { className: "create-form", onsubmit: function (e) {
      e.preventDefault();
      submit.disabled = true;
      request("POST", "/api/company/companies", payload()).then(function (res) {
        submit.disabled = false;
        msg.textContent = "";
        if (res.status === 201) {
          notify(t('company.virtual_company_package_created'));
          onCreated(res.data.id);
          return;
        }
        if (res.status === 422 && res.data.errors) {
          msg.textContent = res.data.errors.map(function (x) { return x.message; }).join(" ");
          return;
        }
        if (res.status === 409) {
          msg.textContent = t('company.rights_or_review_conditions_are_2');
          showHolds(res.data.holds);
          return;
        }
        msg.textContent = errMessage(res, t('company.could_not_create_it'));
      });
    } }, [
      el("label", {}, [t('company.company_name'), name]),
      el("label", {}, [t('company.purpose_2'), purpose]),
      el("label", {}, [t('app.catalog'), catalog]),
      el("label", {}, [t('company.required_project'), project]),
      fieldset,
      el("div", { className: "btn-row" }, [checkBtn, submit]),
      msg, holdsBox, el("div", { className: "btn-row" }, [aiBtn]), aiMsg, aiBox
    ]);
    return form;
  }

  window.FMScreens.s7 = function (container) {
    container.textContent = "";
    var status = el("p", { className: "empty", role: "status", text: t('company.loading') });
    var selected = null;
    var listBox = el("ul", { className: "company-list" });
    var detailBox = el("div", { className: "detail-wrap" });
    var formWrap = el("div", { className: "create-wrap" });

    function reloadDetail() {
      detailBox.textContent = "";
      if (!selected) { detailBox.appendChild(el("p", { className: "empty", text: t('company.select_a_company') })); return; }
      detailBox.appendChild(detailPanel(selected, reloadAll, function (holds, message) {
        notify(message || t('company.export_is_on_hold_under'));
        detailBox.appendChild(holdList(holds));
      }));
    }

    function loadList() {
      return request("GET", "/api/company/companies").then(function (res) {
        listBox.textContent = "";
        if (!res.ok) { listBox.appendChild(el("li", { className: "error", text: errMessage(res, t('catalog.could_not_load_the_list')) })); return; }
        if (!res.data.items.length) listBox.appendChild(el("li", { className: "empty", text: t('company.no_virtual_company_yet') }));
        res.data.items.forEach(function (c) {
          listBox.appendChild(el("li", {}, [el("button", {
            type: "button", className: "btn link" + (c.id === selected ? " active" : ""),
            "aria-pressed": c.id === selected ? "true" : "false",
            onclick: function () { selected = c.id; loadList(); reloadDetail(); }
          }, [c.title, " ", c.synthetic ? badge() : null])]));
        });
      });
    }

    function reloadAll() {
      loadList().then(reloadDetail);
    }

    function mountForm() {
      request("GET", "/api/company/candidates").then(function (res) {
        formWrap.textContent = "";
        if (!res.ok) { formWrap.appendChild(el("p", { className: "error", text: t('company.could_not_load_the_selection') })); return; }
        formWrap.appendChild(el("h3", { text: t('company.new_virtual_company') }));
        formWrap.appendChild(creationForm(res.data, function (id) { selected = id; reloadAll(); }));
      });
    }

    container.appendChild(el("h2", { text: t('company.s7_virtual_company') }));
    container.appendChild(status);
    container.appendChild(el("section", { className: "company-list-wrap" }, [el("h3", { text: t('company.company_list') }), listBox]));
    container.appendChild(formWrap);
    container.appendChild(detailBox);
    reloadAll();
    mountForm();
    status.remove();
  };
})();
