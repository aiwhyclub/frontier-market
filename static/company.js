/* S7 가상회사: 생성 게이트, 고정 패키지, 학습/평가 샘플, 과제 실행, JSON 내보내기. */
(function () {
  "use strict";
  window.FMScreens = window.FMScreens || {};

  var PURPOSE_LABEL = { training: "학습(training)", evaluation: "평가(evaluation)" };
  var STATE_LABEL = {
    pending_review: "검사 대기", inspected: "검사 완료", held: "보류", released: "해제(종료)"
  };
  var ACTION_LABEL = { request_inspection: "검사 요청", hold: "보류", release: "해제" };

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
    return el("span", { className: "badge-synthetic", text: "합성" });
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
      return { status: 0, ok: false, data: { error: { message: "네트워크 연결을 확인하세요." } } };
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
        el("div", { text: "보류 이유: " + h.reason }),
        el("div", { text: "목적: " + (PURPOSE_LABEL[h.purpose] || h.purpose) })
      ]);
      if (h.action === "rights_supplement" && onSupplement) {
        item.appendChild(el("button", {
          type: "button", className: "btn", text: "이용 범위 보완",
          onclick: function () { onSupplement(h); }
        }));
      } else if (h.action === "approval_revoked" || h.action === "review_required") {
        item.appendChild(el("div", { text: "행동: 지정 운영책임자의 새 검토 후 다시 확인하세요. 과거 취소는 자동 복구되지 않습니다." }));
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
    var submit = el("button", { type: "submit", className: "btn primary", text: "범위 저장" });
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
        if (!res.ok) { msg.textContent = errMessage(res, "저장하지 못했습니다."); return; }
        notify("권리 범위를 새 기록으로 저장했습니다. 회사 조건을 다시 확인하세요.");
        onDone();
      });
    } }, [
      el("p", { text: "대상: " + (holdItem.title || holdItem.target_id) + " · 새 권리 기록으로 저장되며 기존 기록은 바뀌지 않습니다." }),
      el("fieldset", {}, [el("legend", { text: "허용 목적" })].concat(checks)),
      el("label", {}, ["확인 근거", evidence]),
      el("label", {}, ["확인자", confirmer]),
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
      wrap.appendChild(el("h4", { text: "학습 샘플 (training): 입력 / 검토 출력 / 근거" }));
      training.forEach(function (s) {
        wrap.appendChild(el("pre", { className: "sample", text: JSON.stringify(s, null, 2) }));
      });
    }
    if (evaluation.length) {
      wrap.appendChild(el("h4", { text: "평가 샘플 (evaluation): prompt / 허용 결과 / 기준·노출" }));
      evaluation.forEach(function (s) {
        wrap.appendChild(el("pre", { className: "sample", text: JSON.stringify(s, null, 2) }));
      });
    }
    if (!training.length && !evaluation.length) {
      wrap.appendChild(el("p", { className: "empty", text: "이 패키지에 샘플이 없습니다." }));
    }
    ((pkg && pkg.ai_drafts) || []).forEach(function (a) {
      wrap.appendChild(el("h4", { text: a.label + " — 사람이 검토·수정해 채택한 문서/학습 사례" }));
      wrap.appendChild(el("p", { text: (a.document_title || "") + "\n" + (a.document_body || "") }));
      wrap.appendChild(el("p", { text: "학습 사례: " + (a.learning_case || "없음") }));
      wrap.appendChild(el("p", { className: "muted", text: a.note }));
    });
    return wrap;
  }

  function runsPanel(detail, companyId, reload) {
    var wrap = el("div", { className: "runs" });
    wrap.appendChild(el("h4", { text: "과제 실행 (선택한 과제만 변경됩니다)" }));
    var tasks = (detail.package && detail.package.snapshot && detail.package.snapshot.tasks) || [];
    var runs = {};
    (detail.runs || []).forEach(function (r) { runs[r.task_id] = r; });
    if (!tasks.length) {
      wrap.appendChild(el("p", { className: "empty", text: "실행할 과제가 없습니다." }));
      return wrap;
    }
    tasks.forEach(function (t) {
      var run = runs[t.task_id];
      if (!run) return;
      var row = el("div", { className: "run-row" });
      var doAction = function (action) {
        request("POST", "/api/company/companies/" + companyId + "/tasks/" + t.task_id + "/action",
          { action: action, expected_run_version: run.run_version }).then(function (res) {
          if (!res.ok) notify(errMessage(res, "실행하지 못했습니다."));
          reload();
        });
      };
      row.appendChild(el("strong", { text: t.title }));
      row.appendChild(el("span", { text: " 시도 " + run.attempt + " · " + (STATE_LABEL[run.state] || run.state) +
        (run.score != null ? " · 규칙 점수 " + run.score + "/4" : "") }));
      var buttons = el("div", { className: "btn-row" });
      if (run.state !== "released") {
        buttons.appendChild(el("button", { type: "button", className: "btn", text: ACTION_LABEL.request_inspection,
          onclick: function () { doAction("request_inspection"); } }));
        buttons.appendChild(el("button", { type: "button", className: "btn", text: ACTION_LABEL.hold,
          onclick: function () { doAction("hold"); } }));
        buttons.appendChild(el("button", { type: "button", className: "btn", text: ACTION_LABEL.release,
          onclick: function () { doAction("release"); } }));
      }
      buttons.appendChild(el("button", { type: "button", className: "btn", text: "이 과제만 새 시도로 초기화",
        onclick: function () {
          request("POST", "/api/company/companies/" + companyId + "/tasks/" + t.task_id + "/reset",
            { expected_run_version: run.run_version }).then(function (res) {
            if (!res.ok) notify(errMessage(res, "초기화하지 못했습니다."));
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
        list.appendChild(el("li", { text: "시도 " + h.attempt + " · " + h.event +
          (h.to_state ? " → " + (STATE_LABEL[h.to_state] || h.to_state) : "") +
          (h.score != null ? " · 점수 " + h.score : "") + " · " + h.task_id }));
      });
      wrap.appendChild(el("h5", { text: "이력" }));
      wrap.appendChild(list);
    }
    return wrap;
  }

  function detailPanel(companyId, reload, exportHolds) {
    var box = el("section", { className: "company-detail", "aria-label": "회사 상세" });
    box.appendChild(el("p", { className: "empty", text: "불러오는 중입니다..." }));
    request("GET", "/api/company/companies/" + companyId).then(function (res) {
      box.textContent = "";
      if (!res.ok) { box.appendChild(el("p", { className: "error", text: errMessage(res, "회사를 불러오지 못했습니다.") })); return; }
      var d = res.data;
      var pkg = d.package;
      var head = el("h3", {}, [d.item.title, " ", d.item.synthetic ? badge() : null]);
      box.appendChild(head);
      if (!pkg) {
        box.appendChild(el("p", { className: "empty", text: "고정 패키지가 없는 기록입니다." }));
        return;
      }
      box.appendChild(el("h4", { text: "고정 패키지 (생성 당시 버전)" }));
      box.appendChild(el("dl", { className: "frozen" }, [
        el("dt", { text: "목적" }), el("dd", { text: PURPOSE_LABEL[pkg.snapshot.company.purpose] || "" }),
        el("dt", { text: "생성 시각" }), el("dd", { text: pkg.created_at }),
        el("dt", { text: "SHA-256" }), el("dd", { className: "hash", text: pkg.sha256 }),
        el("dt", { text: "정규화" }), el("dd", { text: "FM-CJSON-1" })
      ]));
      var cur = d.current_status || { allowed: false, holds: [] };
      box.appendChild(el("h4", { text: "현재 권리·검토 상태 (고정 패키지와 별도 표시)" }));
      box.appendChild(el("p", { text: cur.allowed
        ? "현재 조건 허용: 추가 내보내기가 가능합니다. 과거 본문은 변하지 않습니다."
        : "현재 보류: 추가 내보내기가 막혀 있습니다. 아래 대상을 보완하세요." }));
      if (!cur.allowed) box.appendChild(holdList(cur.holds, function (h) {
        box.appendChild(supplementForm(h, function () { reload(); }));
      }));
      box.appendChild(sampleList(pkg.snapshot));
      box.appendChild(runsPanel(d, companyId, reload));
      var exportBtn = el("button", { type: "button", className: "btn primary", text: "현재 허용 시 JSON 내보내기",
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
      if (!res.ok) { notify("내보내기에 실패했습니다."); return null; }
      return res.blob().then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "company-" + companyId + ".json";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
        notify("로컬 JSON을 내려받았습니다. 외부 전달 허용으로 표시되지 않습니다.");
      });
    }, function () { notify("네트워크 연결을 확인하세요."); });
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
    var project = el("select", { name: "project_id" }, [el("option", { value: "", text: "필수 프로젝트를 선택" })]
      .concat(cands.projects.map(function (p) { return el("option", { value: p.id, text: p.title }); })));
    var works = cands.works.map(function (w) {
      var cb = el("input", { type: "checkbox", value: w.id, name: "work" });
      if (!w.verified) cb.disabled = true;
      return el("label", { className: w.verified ? "" : "muted" }, [cb, " " + w.title +
        (w.verified ? "" : " (검증 전)"), w.synthetic ? badge() : null]);
    });
    var fieldset = el("fieldset", {}, [el("legend", { text: "작업 (검증된 작업 1개 이상)" })].concat(works));

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
        holdsBox.appendChild(el("p", { text: "보류 상태입니다. 아래 대상별 이용 범위를 보완한 뒤 다시 생성하세요. 회사는 저장되지 않았습니다." }));
        holdsBox.appendChild(holdList(holds, function (h) {
          holdsBox.appendChild(supplementForm(h, function () { holdsBox.textContent = ""; }));
        }));
      }
    }

    var checkBtn = el("button", { type: "button", className: "btn", text: "생성 전 최신 조건 확인",
      onclick: function () {
        request("POST", "/api/company/check", payload()).then(function (res) {
          msg.textContent = "";
          if (res.data.allowed) { msg.textContent = "현재 조건으로 생성 가능합니다."; holdsBox.textContent = ""; return; }
          if (res.data.errors && res.data.errors.length) msg.textContent = res.data.errors.map(function (e) { return e.message; }).join(" ");
          showHolds(res.data.holds);
        });
      } });

    /* F10.6: AI 문서·학습 사례 초안. 생성 게이트를 통과한 뒤에만 만들고, 사람이 고쳐 채택해야 스냅샷에 들어간다. */
    var aiBox = el("div", { className: "ai-draft", id: "company-ai-box" });
    var aiMsg = el("p", { className: "muted", role: "status", id: "company-ai-msg" });
    var aiBtn = el("button", { type: "button", className: "btn", id: "company-ai-make", disabled: "disabled",
      text: "AI 문서·학습 사례 초안 만들기" });
    request("GET", "/api/company/ai-status").then(function (res) {
      if (res.ok && res.data.adopted) {
        aiMsg.textContent = "AI 사용 가능: " + res.data.label + ". 권리·사람 검증 조건을 통과한 작업만 사용해요. 실제 모델 훈련은 수행하지 않아요.";
        aiBtn.removeAttribute("disabled");
      } else {
        aiMsg.textContent = "AI 초안은 채택되지 않았어요. 회사 만들기는 그대로 사용할 수 있어요.";
      }
    });
    aiBtn.addEventListener("click", function () {
      aiBox.textContent = "";
      request("POST", "/api/company/ai-draft", payload()).then(function (res) {
        if (res.status === 409) { aiMsg.textContent = "권리·검토 조건이 부족해 AI 초안을 만들지 않았습니다."; showHolds(res.data.holds); return; }
        if (!res.ok) {
          aiMsg.textContent = (res.data.errors || []).map(function (x) { return x.message; }).join(" ") || errMessage(res, "초안을 만들지 못했습니다.");
          return;
        }
        if (!res.data.draft) { aiMsg.textContent = "AI 초안을 받지 못했어요(" + (res.data.ai.error || "미채택") + "). 수동으로 만들 수 있어요."; return; }
        aiMsg.textContent = res.data.ai.label + " — 아직 저장되지 않았어요. " + res.data.note;
        var fields = {};
        [["document_title", "문서 제목", "input"], ["document_body", "문서 본문", "textarea"],
          ["learning_case", "학습 사례", "textarea"]].forEach(function (f) {
          var input = el(f[2], { id: "company-ai-" + f[0] });
          input.value = res.data.draft[f[0]] || "";
          fields[f[0]] = input;
          aiBox.appendChild(el("label", {}, [f[1], input]));
        });
        aiBox.appendChild(el("button", { type: "button", className: "btn primary", text: "수정 내용으로 채택해 회사 만들기",
          onclick: function () {
            var body = payload();
            body.draft = {};
            Object.keys(fields).forEach(function (k) { body.draft[k] = fields[k].value.trim(); });
            request("POST", "/api/company/ai-draft/" + encodeURIComponent(res.data.ai.run_id) + "/adopt", body).then(function (r) {
              if (r.status === 201) { notify("AI 초안을 포함한 패키지를 만들었습니다."); onCreated(r.data.id); return; }
              if (r.status === 409 && r.data.holds) { aiMsg.textContent = "채택 시점 확인에서 권리·검토 조건이 부족해 저장하지 않았습니다."; showHolds(r.data.holds); return; }
              aiMsg.textContent = (r.data.errors || []).map(function (x) { return x.message; }).join(" ") || errMessage(r, "채택하지 못했습니다.");
            });
          } }));
      });
    });

    var submit = el("button", { type: "submit", className: "btn primary", text: "회사 만들기" });
    var form = el("form", { className: "create-form", onsubmit: function (e) {
      e.preventDefault();
      submit.disabled = true;
      request("POST", "/api/company/companies", payload()).then(function (res) {
        submit.disabled = false;
        msg.textContent = "";
        if (res.status === 201) {
          notify("가상회사 패키지를 만들었습니다.");
          onCreated(res.data.id);
          return;
        }
        if (res.status === 422 && res.data.errors) {
          msg.textContent = res.data.errors.map(function (x) { return x.message; }).join(" ");
          return;
        }
        if (res.status === 409) {
          msg.textContent = "권리·검토 조건이 부족해 저장하지 않았습니다.";
          showHolds(res.data.holds);
          return;
        }
        msg.textContent = errMessage(res, "만들지 못했습니다.");
      });
    } }, [
      el("label", {}, ["회사 이름", name]),
      el("label", {}, ["목적", purpose]),
      el("label", {}, ["카탈로그", catalog]),
      el("label", {}, ["필수 프로젝트", project]),
      fieldset,
      el("div", { className: "btn-row" }, [checkBtn, submit]),
      msg, holdsBox, el("div", { className: "btn-row" }, [aiBtn]), aiMsg, aiBox
    ]);
    return form;
  }

  window.FMScreens.s7 = function (container) {
    container.textContent = "";
    var status = el("p", { className: "empty", role: "status", text: "불러오는 중입니다..." });
    var selected = null;
    var listBox = el("ul", { className: "company-list" });
    var detailBox = el("div", { className: "detail-wrap" });
    var formWrap = el("div", { className: "create-wrap" });

    function reloadDetail() {
      detailBox.textContent = "";
      if (!selected) { detailBox.appendChild(el("p", { className: "empty", text: "회사를 선택하세요." })); return; }
      detailBox.appendChild(detailPanel(selected, reloadAll, function (holds, message) {
        notify(message || "현재 조건으로 내보내기가 보류되었습니다.");
        detailBox.appendChild(holdList(holds));
      }));
    }

    function loadList() {
      return request("GET", "/api/company/companies").then(function (res) {
        listBox.textContent = "";
        if (!res.ok) { listBox.appendChild(el("li", { className: "error", text: errMessage(res, "목록을 불러오지 못했습니다.") })); return; }
        if (!res.data.items.length) listBox.appendChild(el("li", { className: "empty", text: "아직 가상회사가 없습니다." }));
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
        if (!res.ok) { formWrap.appendChild(el("p", { className: "error", text: "선택 목록을 불러오지 못했습니다." })); return; }
        formWrap.appendChild(el("h3", { text: "새 가상회사" }));
        formWrap.appendChild(creationForm(res.data, function (id) { selected = id; reloadAll(); }));
      });
    }

    container.appendChild(el("h2", { text: "S7 가상회사" }));
    container.appendChild(status);
    container.appendChild(el("section", { className: "company-list-wrap" }, [el("h3", { text: "회사 목록" }), listBox]));
    container.appendChild(formWrap);
    container.appendChild(detailBox);
    reloadAll();
    mountForm();
    status.remove();
  };
})();
