/* Frontier Lab Market shell: hash routing for S0 dashboard and S1~S7 stages.
 * Each screen renders a title and an empty state with one next action.
 * next: { href } opens another screen; { form } dispatches a
 * 'fm:open-form' event on document for the form layer (ui-forms.js). */
(function () {
  'use strict';

  var ORDER = ['dashboard', 's1', 's2', 's3', 's4', 's5', 's6', 's7'];

  var SCREENS = {
    dashboard: {
      code: 'S0',
      nav: t('app.dashboard'),
      title: t('app.dashboard'),
      lede: t('app.see_progress_and_next_steps'),
      emptyTitle: t('app.nothing_has_been_started_yet'),
      emptyBody: t('app.start_with_collecting_materials_the'),
      next: { label: t('app.start_s1_collect_materials'), href: '#s1' }
    },
    s1: {
      code: 'S1',
      nav: t('app.collect'),
      title: t('app.collect_and_organize_materials'),
      lede: t('app.save_a_public_url_or'),
      emptyTitle: t('app.no_materials_saved_yet'),
      emptyBody: t('app.collect_a_public_url_or'),
      next: { label: t('app.open_material_form'), form: 'source' }
    },
    s2: {
      code: 'S2',
      nav: 'DB',
      title: t('app.knowledge_db'),
      lede: t('app.find_saved_materials_people_and'),
      emptyTitle: t('app.the_db_is_still_empty'),
      emptyBody: t('app.this_is_not_an_empty'),
      next: { label: t('app.go_to_s1_collect'), href: '#s1' }
    },
    s3: {
      code: 'S3',
      nav: t('app.experts'),
      title: t('app.expert_recruiting_evaluation_and_upload'),
      lede: t('app.register_candidates_have_a_person'),
      emptyTitle: t('app.no_candidates_registered'),
      emptyBody: t('app.register_a_candidate_and_have'),
      next: { label: t('app.open_candidate_form'), form: 'expert' }
    },
    s4: {
      code: 'S4',
      nav: t('app.catalog'),
      title: t('app.build_the_skill_catalog_as'),
      lede: t('app.create_judgement_task_cards_to'),
      emptyTitle: t('app.no_task_cards_yet'),
      emptyBody: t('app.start_by_creating_one_task'),
      next: { label: t('app.create_task_card'), form: 'catalog' }
    },
    s5: {
      code: 'S5',
      nav: t('app.refine_verify'),
      title: t('app.refine_correct_and_verify_apprentice'),
      lede: t('app.compare_the_original_and_the'),
      emptyTitle: t('app.no_work_to_refine'),
      emptyBody: t('app.refining_needs_the_original_work'),
      next: { label: t('app.go_to_s3_experts'), href: '#s3' }
    },
    s6: {
      code: 'S6',
      nav: 'FDE',
      title: t('app.fde_system_build_and_consulting'),
      lede: t('app.manage_free_ax_goals_scope'),
      emptyTitle: t('app.no_projects_registered'),
      emptyBody: t('app.register_a_project_to_set'),
      next: { label: t('app.open_project_form'), form: 'project' }
    },
    s7: {
      code: 'S7',
      nav: t('app.virtual_company'),
      title: t('app.virtual_company_for_llm_training'),
      lede: t('app.build_and_run_virtual_company'),
      emptyTitle: t('app.no_virtual_company_created'),
      emptyBody: t('app.approved_work_and_a_project'),
      next: { label: t('app.check_s5_refine_verify'), href: '#s5' }
    }
  };

  function esc(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function hasScreen(key) {
    return !!(window.FMScreens && typeof window.FMScreens[key] === 'function');
  }

  function currentKey() {
    var key = (window.location.hash || '').replace(/^#/, '');
    return Object.prototype.hasOwnProperty.call(SCREENS, key) ? key : 'dashboard';
  }

  function nextActionHtml(next) {
    if (next.href) {
      return '<a class="btn btn-primary" href="' + esc(next.href) + '">' + esc(next.label) + '</a>';
    }
    return '<button type="button" class="btn btn-primary" data-form="' + esc(next.form) + '">' + esc(next.label) + '</button>';
  }

  function stageGridHtml(activeKey) {
    var cards = ORDER.slice(1).map(function (key) {
      var s = SCREENS[key];
      var current = key === activeKey ? ' aria-current="page"' : '';
      return '<li><a class="stage-card" href="#' + key + '"' + current + '>' +
        '<span class="stage-code">' + esc(s.code) + '</span>' +
        '<span class="stage-name">' + esc(s.nav) + '</span>' +
        '<span class="stage-desc">' + esc(s.title) + '</span>' +
        '</a></li>';
    }).join('');
    return '<section aria-labelledby="stage-heading"><h2 class="section-title" id="stage-heading">' + t('app.jump_to_a_stage') + '</h2>' +
      '<ul class="stage-grid">' + cards + '</ul></section>';
  }

  function render(key) {
    var s = SCREENS[key];
    var main = document.getElementById('main-content');
    var extra = key === 'dashboard' ? stageGridHtml(key) : '';

    // 기능 스크립트가 window.FMScreens[key]를 등록했으면 그 화면이 그린다. 없으면 아래 기본 빈 상태를 쓴다.
    var mounts = hasScreen(key);
    var body = mounts
      ? '<div class="' + key + '-root" id="screen-root"></div>'
      : '<section class="empty-state" aria-labelledby="empty-title">' +
          '<h2 id="empty-title">' + esc(s.emptyTitle) + '</h2>' +
          '<p>' + esc(s.emptyBody) + '</p>' +
          '<div class="next-action">' +
            '<span class="next-action-label">' + t('app.next_action') + '</span>' +
            nextActionHtml(s.next) +
          '</div>' +
        '</section>';

    main.innerHTML =
      '<article class="screen" aria-labelledby="screen-title">' +
        '<p class="screen-kicker">' + esc(s.code) + '</p>' +
        '<h1 id="screen-title" tabindex="-1">' + esc(s.title) + '</h1>' +
        '<p class="lede">' + esc(s.lede) + '</p>' +
        body +
        extra +
      '</article>';

    if (mounts) {
      window.FMScreens[key](document.getElementById('screen-root'));
    }

    document.title = s.code + ' ' + s.title + ' · Frontier Lab Market';
    document.getElementById('breadcrumb-current').textContent = s.code + ' ' + s.nav;

    var links = document.querySelectorAll('#primary-nav a[data-screen]');
    Array.prototype.forEach.call(links, function (link) {
      if (link.getAttribute('data-screen') === key) {
        link.setAttribute('aria-current', 'page');
      } else {
        link.removeAttribute('aria-current');
      }
    });

    setSidebarOpen(false);
    main.focus({ preventScroll: true });
    var heading = document.getElementById('screen-title');
    if (heading) {
      heading.focus({ preventScroll: false });
    }
  }

  function setSidebarOpen(open) {
    var sidebar = document.getElementById('sidebar');
    var toggle = document.getElementById('nav-toggle');
    if (!sidebar || !toggle) {
      return;
    }
    sidebar.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.textContent = open ? t('app.close_stage_menu') : t('app.open_stage_menu');
  }

  function onHashChange() {
    render(currentKey());
  }

  function onClick(event) {
    var formButton = event.target.closest ? event.target.closest('[data-form]') : null;
    if (formButton) {
      document.dispatchEvent(new CustomEvent('fm:open-form', {
        bubbles: true,
        detail: { form: formButton.getAttribute('data-form'), opener: formButton }
      }));
    }
  }

  function init() {
    var toggle = document.getElementById('nav-toggle');
    if (toggle) {
      toggle.addEventListener('click', function () {
        var open = document.getElementById('sidebar').classList.contains('is-open');
        setSidebarOpen(!open);
      });
    }
    document.getElementById('main-content').addEventListener('click', onClick);
    window.addEventListener('hashchange', onHashChange);
    render(currentKey());
  }

  // defer 스크립트 실행 중에는 readyState가 'interactive'이므로, 기능 스크립트가 화면을 등록한 뒤(DOMContentLoaded) 시작한다.
  // 공개 시연 모드 확인(demo.js의 window.FMBoot)이 끝난 뒤 시작한다. 로컬 모드에서는 즉시 끝나 동작이 같다.
  function start() {
    var boot = window.FMBoot;
    if (boot && typeof boot.then === 'function') {
      boot.then(init, init);
    } else {
      init();
    }
  }

  if (document.readyState === 'complete') {
    start();
  } else {
    document.addEventListener('DOMContentLoaded', start);
  }
})();
