/* Frontier Market shell: hash routing for S0 dashboard and S1~S7 stages.
 * Each screen renders a title and an empty state with one next action.
 * next: { href } opens another screen; { form } dispatches a
 * 'fm:open-form' event on document for the form layer (ui-forms.js). */
(function () {
  'use strict';

  var ORDER = ['dashboard', 's1', 's2', 's3', 's4', 's5', 's6', 's7'];

  var SCREENS = {
    dashboard: {
      code: 'S0',
      nav: '대시보드',
      title: '대시보드',
      lede: '단계별 현황과 다음 작업을 한눈에 봅니다. 지금은 합성 예시 기준의 빈 워크스페이스입니다.',
      emptyTitle: '아직 시작한 작업이 없습니다',
      emptyBody: '자료 수집부터 시작하면 전문가 → 과제 → 검증 → 가상회사 순서로 이어집니다. 아래 단계를 선택해 현재 위치를 옮길 수 있습니다.',
      next: { label: 'S1 자료 수집 시작하기', href: '#s1' }
    },
    s1: {
      code: 'S1',
      nav: '자료 수집',
      title: '자료 수집·체계화',
      lede: '공개 URL이나 직접 입력한 본문을 원문·출처와 함께 저장하고 구조화 초안을 만듭니다.',
      emptyTitle: '저장된 자료가 없습니다',
      emptyBody: '공개 URL을 수집하거나 본문을 직접 입력해 첫 자료를 저장하세요. URL 수집이 실패해도 입력한 내용은 보존되고 본문 직접 입력으로 이어갈 수 있습니다.',
      next: { label: '자료 등록 열기', form: 'source' }
    },
    s2: {
      code: 'S2',
      nav: 'DB',
      title: '지식 DB',
      lede: '저장된 자료·사람·작업을 유형별로 찾고 서로 연결된 항목을 따라갑니다.',
      emptyTitle: 'DB가 아직 비어 있습니다',
      emptyBody: '검색 결과가 없는 것이 아니라 저장된 항목이 아직 없습니다. S1에서 자료를 먼저 등록하면 여기에서 찾을 수 있습니다.',
      next: { label: 'S1 자료 수집으로 이동', href: '#s1' }
    },
    s3: {
      code: 'S3',
      nav: '전문가',
      title: '전문가 모집·평가·승인 후 업로드',
      lede: '후보를 등록하고 사람이 평가한 뒤, 승인된 전문가의 작업만 업로드합니다.',
      emptyTitle: '등록된 후보가 없습니다',
      emptyBody: '후보를 등록하고 평가 기준에 따라 사람이 판정하세요. 승인 전에는 작업을 업로드할 수 없습니다.',
      next: { label: '후보 등록 열기', form: 'expert' }
    },
    s4: {
      code: 'S4',
      nav: '카탈로그',
      title: '기술 카탈로그 제작·DB화',
      lede: '구매할 판단 과제 카드를 만들고 자료·전문가·작업과 연결합니다.',
      emptyTitle: '아직 과제 카드가 없습니다',
      emptyBody: '과제 카드를 하나 만들어 시작하세요. 연결할 자료가 아직 없어도 카드는 저장할 수 있습니다.',
      next: { label: '과제 카드 만들기', form: 'catalog' }
    },
    s5: {
      code: 'S5',
      nav: '정제·검증',
      title: '도제 작업 정제·교정·검증',
      lede: '원문과 정제 초안을 나란히 비교하고 사람이 승인 또는 반려합니다.',
      emptyTitle: '정제할 작업이 없습니다',
      emptyBody: '정제는 승인된 전문가의 작업 원문이 있어야 시작할 수 있습니다. 원문이 없다면 S3에서 먼저 후보를 승인하고 작업을 업로드하세요.',
      next: { label: 'S3 전문가로 이동', href: '#s3' }
    },
    s6: {
      code: 'S6',
      nav: 'FDE',
      title: 'FDE 시스템 구축·컨설팅 프로젝트',
      lede: '무료 AX 목표·범위·단계·산출물을 프로젝트 단위로 관리합니다.',
      emptyTitle: '등록된 프로젝트가 없습니다',
      emptyBody: '프로젝트를 등록해 목표와 범위를 정하세요. 현장 수행 사실은 단계별 근거와 함께 따로 기록합니다.',
      next: { label: '프로젝트 등록 열기', form: 'project' }
    },
    s7: {
      code: 'S7',
      nav: '가상회사',
      title: 'LLM 학습·평가용 가상회사',
      lede: '검증된 작업 데이터로 가상회사 과제 환경을 만들고 실행합니다.',
      emptyTitle: '생성된 가상회사가 없습니다',
      emptyBody: '승인된 작업과 프로젝트가 연결되어야 생성할 수 있습니다. 조건이 부족하면 어떤 항목을 보완해야 하는지 안내합니다.',
      next: { label: 'S5 정제·검증 확인하기', href: '#s5' }
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
    return '<section aria-labelledby="stage-heading"><h2 class="section-title" id="stage-heading">단계 바로가기</h2>' +
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
            '<span class="next-action-label">다음 행동</span>' +
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

    document.title = s.code + ' ' + s.title + ' · Frontier Market';
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
    toggle.textContent = open ? '단계 메뉴 닫기' : '단계 메뉴 열기';
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
