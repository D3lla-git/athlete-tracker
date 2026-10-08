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

  // Back / forward (page restored from cache) or a download that didn't
  // leave the page: stop the bar.
  window.addEventListener('pageshow', stop);
  window.addEventListener('pagehide', function () { clearTimeout(timer); });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') setTimeout(stop, 1500);
  });
})();
