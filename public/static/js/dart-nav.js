/*
 * D.A.R.T. instant feedback while the next page loads.
 * The moment a link or form is used, a slim progress bar starts at the top
 * and the tapped bottom-tab lights up, so a tap never feels ignored.
 */
(function () {
  'use strict';

  var bar = null;
  var timer = null;

  function ensureBar() {
    if (bar) return bar;
    bar = document.createElement('div');
    bar.className = 'dart-progress';
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-hidden', 'true');
    document.body.appendChild(bar);
    return bar;
  }

  function start() {
    var el = ensureBar();
    clearTimeout(timer);
    el.classList.remove('is-done');
    el.style.transition = 'none';
    el.style.transform = 'scaleX(0)';
    // Next frame: grow quickly to 30%, then creep towards 90%.
    requestAnimationFrame(function () {
      el.classList.add('is-active');
      el.style.transition = 'transform 0.25s ease-out';
      el.style.transform = 'scaleX(0.3)';
      timer = setTimeout(function () {
        el.style.transition = 'transform 8s cubic-bezier(0.1, 0.7, 0.2, 1)';
        el.style.transform = 'scaleX(0.9)';
      }, 260);
    });
  }

  function stop() {
    if (!bar) return;
    clearTimeout(timer);
    bar.style.transition = 'transform 0.15s ease-out, opacity 0.3s 0.15s';
    bar.style.transform = 'scaleX(1)';
    bar.classList.add('is-done');
    timer = setTimeout(function () {
      bar.classList.remove('is-active', 'is-done');
      bar.style.transition = 'none';
      bar.style.transform = 'scaleX(0)';
    }, 450);
  }

  function isPageNavigation(link, event) {
    if (!link || event.defaultPrevented) return false;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.target && link.target !== '_self') return false;
    if (link.hasAttribute('download') || link.dataset.bsToggle || link.dataset.noProgress !== undefined) return false;
    var href = link.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || /^(mailto|tel|javascript):/i.test(href)) return false;
    var url;
    try { url = new URL(link.href, location.href); } catch (e) { return false; }
    if (url.origin !== location.origin) return false;
    // Same page, different #section: no load.
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;
    // Downloads (PDF, CSV, card image).
    if (/\.(pdf|csv|png|jpe?g)$/i.test(url.pathname)) return false;
    return true;
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href]');
    if (!isPageNavigation(link, event)) return;

    // Bottom tab bar: show the new tab as active straight away.
    if (link.classList.contains('dart-tab')) {
      document.querySelectorAll('.dart-tab.active, .dart-tab[aria-current]').forEach(function (tab) {
        tab.classList.remove('active');
        tab.removeAttribute('aria-current');
      });
      link.classList.add('active');
    }
    start();
  });

  document.addEventListener('submit', function (event) {
    var form = event.target;
    // Only real page loads (forms handled by scripts call preventDefault).
    setTimeout(function () {
      if (!event.defaultPrevented && !form.hasAttribute('data-no-progress') && form.target !== '_blank') {
        start();
      }
    }, 0);
  });

  // Safety net for the menu button: Bootstrap normally opens the menu. If
  // its script could not load (bad connection, blocked CDN), open and
  // close the menu here so the navigation is never locked.
  document.addEventListener('click', function (event) {
    var toggler = event.target.closest && event.target.closest('.navbar-toggler');
    if (!toggler || (window.bootstrap && window.bootstrap.Collapse)) return;
    var menu = document.querySelector(toggler.getAttribute('data-bs-target') || '#mainNavbar');
    if (!menu) return;
    var open = !menu.classList.contains('show');
    menu.classList.toggle('show', open);
    toggler.classList.toggle('collapsed', !open);
    toggler.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // ---------- Sign out after inactivity ----------
  // The server signs people out after N minutes without activity. Using
  // the page (tap, type, scroll) counts: we tell the server now and then.
  // Two minutes before the end a "Stay signed in" prompt appears; at the
  // end the page reloads and the server shows the login page. All tabs
  // share one clock (localStorage), so working in one tab keeps all alive.
  (function idleSignOut() {
    var body = document.body;
    if (!body || body.dataset.dartAuth !== '1') return;

    var IDLE = (+body.dataset.idleSeconds || 1800) * 1000;
    var WARN = (+body.dataset.warningSeconds || 120) * 1000;
    var PING_EVERY = 4 * 60 * 1000;
    var KEY = 'dartLastActivity';
    var lastPing = Date.now();
    var prompt = null;

    function store(time) { try { localStorage.setItem(KEY, String(time)); } catch (e) { /* private mode */ } }
    function lastActivity() {
      var mine = +(body.dataset.lastActivity || 0);
      var shared = 0;
      try { shared = +(localStorage.getItem(KEY) || 0); } catch (e) { /* ignore */ }
      return Math.max(mine, shared);
    }
    function mark() {
      var now = Date.now();
      body.dataset.lastActivity = String(now);
      store(now);
      // Back from (almost) idle: always tell the server straight away.
      if (prompt) {
        hidePrompt();
        ping();
      } else if (now - lastPing > PING_EVERY) {
        ping();
      }
    }
    function ping() {
      lastPing = Date.now();
      return fetch(body.dataset.keepaliveUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': body.dataset.csrf, 'Accept': 'application/json' }
      }).then(function (r) {
        if (r.status === 401) location.reload();   // already signed out: show the login page
      }).catch(function () { /* offline: try again on the next activity */ });
    }
    function showPrompt(secondsLeft) {
      if (!prompt) {
        prompt = document.createElement('div');
        prompt.className = 'dart-idle-prompt';
        prompt.setAttribute('role', 'alertdialog');
        prompt.setAttribute('aria-live', 'assertive');
        prompt.innerHTML =
          '<div class="dart-idle-text"><i class="fa-solid fa-clock" aria-hidden="true"></i> ' +
          '<span>For your security you will be signed out in <strong class="dart-idle-left"></strong>.</span></div>' +
          '<button type="button" class="btn dart-btn dart-idle-stay">Stay signed in</button>';
        prompt.querySelector('.dart-idle-stay').addEventListener('click', function () {
          ping();
          mark();
        });
        document.body.appendChild(prompt);
      }
      var m = Math.floor(secondsLeft / 60), s = secondsLeft % 60;
      prompt.querySelector('.dart-idle-left').textContent = m + ':' + String(s).padStart(2, '0');
    }
    function hidePrompt() {
      if (prompt) { prompt.remove(); prompt = null; }
    }

    body.dataset.lastActivity = String(Date.now());   // the page just loaded = activity
    store(Date.now());
    ['pointerdown', 'keydown', 'input', 'touchstart'].forEach(function (type) {
      document.addEventListener(type, mark, { passive: true, capture: true });
    });
    var scrollTimer = null;
    window.addEventListener('scroll', function () {
      if (scrollTimer) return;
      scrollTimer = setTimeout(function () { scrollTimer = null; mark(); }, 1000);
    }, { passive: true });

    setInterval(function () {
      var idleFor = Date.now() - lastActivity();
      if (idleFor >= IDLE + 3000) {
        hidePrompt();
        location.reload();          // the server signs out and shows the login page
      } else if (idleFor >= IDLE - WARN) {
        showPrompt(Math.max(0, Math.ceil((IDLE - idleFor) / 1000)));
      } else if (prompt) {
        hidePrompt();               // another tab was used
      }
    }, 1000);
  })();

  // Back / forward (page restored from cache) or a download that didn't
  // leave the page: stop the bar.
  window.addEventListener('pageshow', stop);
  window.addEventListener('pagehide', function () { clearTimeout(timer); });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') setTimeout(stop, 1500);
  });
})();
