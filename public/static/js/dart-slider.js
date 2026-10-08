/*
 * D.A.R.T. card slider (record cards on phones).
 *
 * Native swiping: the cards sit in a horizontally scrolling row with
 * scroll-snap, so they follow the finger with momentum and settle on a
 * card. The next card peeks in so people see they can swipe. This script
 * only adds the controls: previous / next buttons, dots (or "3 / 12" for
 * long lists) and keyboard arrows, kept in sync with the scroll position.
 *
 * Markup:
 *   <div class="dart-slider" data-dart-slider aria-label="...">
 *     <div class="dart-slider-track">
 *       <div class="dart-slide">...</div> ...
 *     </div>
 *   </div>
 */
(function () {
  'use strict';

  var MAX_DOTS = 8;

  function initSlider(slider) {
    if (slider.dataset.dartSliderReady) return;
    slider.dataset.dartSliderReady = '1';

    var track = slider.querySelector('.dart-slider-track');
    if (!track) return;
    var slides = Array.prototype.slice.call(track.children).filter(function (el) {
      return el.classList.contains('dart-slide');
    });
    var count = slides.length;

    slider.setAttribute('role', 'region');
    slider.setAttribute('aria-roledescription', 'carousel');
    slides.forEach(function (slide, i) {
      slide.setAttribute('role', 'group');
      slide.setAttribute('aria-roledescription', 'slide');
      slide.setAttribute('aria-label', (i + 1) + ' of ' + count);
    });

    if (count <= 1) {
      slider.classList.add('is-single');
      return;
    }

    // ---------- controls ----------
    var nav = document.createElement('div');
    nav.className = 'dart-slider-nav';

    var prev = document.createElement('button');
    prev.type = 'button';
    prev.className = 'dart-slider-arrow';
    prev.setAttribute('aria-label', 'Previous card');
    prev.innerHTML = '<i class="fa-solid fa-chevron-left" aria-hidden="true"></i>';

    var next = document.createElement('button');
    next.type = 'button';
    next.className = 'dart-slider-arrow';
    next.setAttribute('aria-label', 'Next card');
    next.innerHTML = '<i class="fa-solid fa-chevron-right" aria-hidden="true"></i>';

    var middle = document.createElement('div');
    middle.className = 'dart-slider-middle';
    var dots = [];
    if (count <= MAX_DOTS) {
      var dotBox = document.createElement('div');
      dotBox.className = 'dart-slider-dots';
      slides.forEach(function (_, i) {
        var dot = document.createElement('button');
        dot.type = 'button';
        dot.className = 'dart-slider-dot';
        dot.setAttribute('aria-label', 'Go to card ' + (i + 1));
        dot.addEventListener('click', function () { goTo(i); });
        dotBox.appendChild(dot);
        dots.push(dot);
      });
      middle.appendChild(dotBox);
    }
    var counter = document.createElement('span');
    counter.className = 'dart-slider-count';
    counter.setAttribute('aria-live', 'polite');
    middle.appendChild(counter);

    nav.appendChild(prev);
    nav.appendChild(middle);
    nav.appendChild(next);
    slider.appendChild(nav);

    // ---------- behaviour ----------
    var active = -1;

    function slideOffset(i) {
      var slide = slides[i];
      return slide.offsetLeft - (track.clientWidth - slide.offsetWidth) / 2;
    }

    function goTo(i, instant) {
      i = Math.max(0, Math.min(count - 1, i));
      track.scrollTo({ left: slideOffset(i), behavior: instant ? 'auto' : 'smooth' });
      setActive(i);
    }

    function setActive(i) {
      if (i === active) return;
      active = i;
      dots.forEach(function (dot, n) {
        dot.classList.toggle('is-active', n === i);
        dot.setAttribute('aria-current', n === i ? 'true' : 'false');
      });
      slides.forEach(function (slide, n) { slide.classList.toggle('is-active', n === i); });
      counter.textContent = (i + 1) + ' / ' + count;
      prev.disabled = i === 0;
      next.disabled = i === count - 1;
    }

    function nearest() {
      var centre = track.scrollLeft + track.clientWidth / 2;
      var best = 0, bestDistance = Infinity;
      slides.forEach(function (slide, i) {
        var distance = Math.abs(slide.offsetLeft + slide.offsetWidth / 2 - centre);
        if (distance < bestDistance) { bestDistance = distance; best = i; }
      });
      return best;
    }

    var ticking = false;
    track.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        setActive(nearest());
      });
    }, { passive: true });

    prev.addEventListener('click', function () { goTo(active - 1); });
    next.addEventListener('click', function () { goTo(active + 1); });

    track.setAttribute('tabindex', '0');
    track.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { e.preventDefault(); goTo(active + 1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); goTo(active - 1); }
    });

    window.addEventListener('resize', function () { goTo(active, true); });

    setActive(0);
  }

  function initAll(root) {
    (root || document).querySelectorAll('[data-dart-slider]').forEach(initSlider);
  }

  window.dartSliders = { init: initAll };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { initAll(); });
  } else {
    initAll();
  }
})();
