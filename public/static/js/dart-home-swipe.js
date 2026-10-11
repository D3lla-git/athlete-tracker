/* Home | Highlights: swipe between the two top tabs (like X).
   On Home, swipe left -> Highlights; on Highlights, swipe right -> Home.
   The page follows the finger; a short or slow, mostly vertical, or edge
   swipe does nothing. Swipes that start on something that scrolls sideways
   itself (photo carousels, sport chips, sliders, the home card carousel),
   in a form field, or while a sheet / pop-up is open are left alone. */
(function () {
  'use strict';

  var nav = document.querySelector('.home-tabs[data-swipe-prev], .home-tabs[data-swipe-next]');
  if (!nav || !('ontouchstart' in window)) return;

  var PREV = nav.dataset.swipePrev || '';      // swipe right goes here
  var NEXT = nav.dataset.swipeNext || '';      // swipe left goes here
  var EDGE = 24;                               // leave the screen edges to the browser's own back gesture
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var page = nav.closest('.container') || nav.parentElement;     // the page content (not the menu or tab bar)
  var indicator = nav.querySelector('.home-tab.active span');

  var startX = 0, startY = 0, startT = 0, dx = 0, tracking = false, decided = false, horizontal = false, finger = null;

  // The touch of the finger that started the swipe (not just the first one listed).
  function ownTouch(list) {
    for (var i = 0; i < list.length; i++) if (list[i].identifier === finger) return list[i];
    return null;
  }

  function scrollsSideways(el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      if (node.matches && node.matches(
        'input, textarea, select, [contenteditable], .carousel, [data-carousel], .fp-track, .dart-slider, ' +
        '.feed-sports, .search-tabs, .fa-chips, .modal, .fp-sheet, .approval-prompt, .dart-celebration, [data-no-swipe]')) {
        return true;
      }
      if (node.scrollWidth > node.clientWidth + 2) {
        var ox = getComputedStyle(node).overflowX;
        if (ox === 'auto' || ox === 'scroll') return true;
      }
    }
    return false;
  }

  function blocked() {
    return document.documentElement.classList.contains('fp-sheet-open')
      || document.querySelector('.modal.show, #approvalPrompt:not([hidden])');
  }

  function setOffset(x, animate) {
    // While the page follows the finger it must not stick out past the
    // screen edge (phones would zoom out to fit it and the swipe jumps).
    document.documentElement.style.overflowX = x ? 'clip' : '';
    document.body.style.overflowX = x ? 'clip' : '';
    page.style.transition = animate ? 'transform 0.22s ease-out, opacity 0.22s ease-out' : 'none';
    page.style.transform = x ? 'translateX(' + x + 'px)' : '';
    page.style.opacity = x ? String(1 - Math.min(0.35, Math.abs(x) / window.innerWidth)) : '';
    if (indicator) {
      indicator.style.transition = page.style.transition;
      indicator.style.transform = x ? 'translateX(' + (-x * 0.25) + 'px)' : '';
    }
  }

  document.addEventListener('touchstart', function (e) {
    tracking = false;
    if (e.touches.length !== 1 || blocked()) return;
    var t = e.touches[0];
    if (t.clientX < EDGE || t.clientX > window.innerWidth - EDGE) return;
    if (scrollsSideways(e.target)) return;
    startX = t.clientX; startY = t.clientY; startT = Date.now(); finger = t.identifier;
    dx = 0; tracking = true; decided = false; horizontal = false;
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (!tracking) return;
    if (e.touches.length > 1) { tracking = false; setOffset(0, true); return; }     // a pinch: not a tab swipe
    var t = ownTouch(e.touches);
    if (!t) return;
    var mx = t.clientX - startX, my = t.clientY - startY;
    if (!decided) {
      if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
      decided = true;
      horizontal = Math.abs(mx) > Math.abs(my) * 1.5;    // clearly sideways, not scrolling the page
      if (!horizontal) { tracking = false; return; }
    }
    // Only toward a tab that exists (no rubber band past the last tab).
    if ((mx < 0 && !NEXT) || (mx > 0 && !PREV)) { dx = 0; setOffset(0); return; }
    dx = mx;
    if (!reduceMotion) setOffset(dx * 0.5);
  }, { passive: true });

  function finish() {
    if (!tracking) return;
    tracking = false;
    if (!horizontal) return;
    var fast = Math.abs(dx) / Math.max(1, Date.now() - startT) > 0.45;      // a flick
    var far = Math.abs(dx) > window.innerWidth * 0.28;
    var target = dx < 0 ? NEXT : PREV;
    if (target && (far || (fast && Math.abs(dx) > 40))) {
      if (!reduceMotion) setOffset((dx < 0 ? -1 : 1) * window.innerWidth * 0.35, true);
      window.location.href = target;
      // Coming back with the browser's back button shows the page as it was.
      setTimeout(function () { setOffset(0); }, 600);
    } else {
      setOffset(0, !reduceMotion);
    }
  }
  document.addEventListener('touchend', finish, { passive: true });
  document.addEventListener('touchcancel', function () { tracking = false; setOffset(0, true); }, { passive: true });
  window.addEventListener('pageshow', function () { setOffset(0); });
})();
