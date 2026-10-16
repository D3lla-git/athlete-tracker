/* D.A.R.T. Highlights feed: like / repost / share / comment / save,
   videos that play (muted) while on screen, endless scrolling and the
   comments sheet. Everything the server sends is inserted as text, never
   as HTML, except the server-rendered post pages fetched while scrolling. */
(function () {
  'use strict';

  var root = document.getElementById('feedRoot');
  if (!root) return;

  var CSRF = root.dataset.csrf || '';
  var CAN_INTERACT = root.dataset.canInteract === '1';     // everyone except suspended accounts
  var IS_MEMBER = root.dataset.member === '1';
  var SIGNED_IN = root.dataset.signedIn === '1';
  var LOGIN_URL = root.dataset.loginUrl || '/login';
  var SINGLE = root.dataset.single === '1';
  var saveData = !!(navigator.connection && navigator.connection.saveData);
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- small helpers ----------
  var toastEl = null, toastTimer = null;
  function toast(message, linkText, linkHref) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'fp-toast';
      toastEl.setAttribute('role', 'status');
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = message;
    if (linkText && linkHref) {
      var a = document.createElement('a');
      a.href = linkHref;
      a.textContent = linkText;
      toastEl.appendChild(document.createTextNode(' '));
      toastEl.appendChild(a);
    }
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 3200);
  }

  function needAccount() {
    toast('Your account is suspended, so you cannot react.');
  }

  function send(url, payload, method) {
    return fetch(url, {
      method: method || 'POST',
      credentials: 'same-origin',
      headers: method === 'GET' ? { 'Accept': 'application/json' } : {
        'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': CSRF
      },
      body: method === 'GET' ? undefined : JSON.stringify(payload || {})
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Something went wrong. Please try again.' }; })
        .then(function (data) { data._status = r.status; return data; });
    });
  }

  function cardsFor(id) { return document.querySelectorAll('.fp[data-post="' + id + '"]'); }

  function setCount(id, act, label) {
    cardsFor(id).forEach(function (card) {
      var n = card.querySelector('.fp-act[data-act="' + act + '"] .fp-n');
      if (n) n.textContent = label || '';
    });
  }

  function setPressed(id, act, on) {
    cardsFor(id).forEach(function (card) {
      var btn = card.querySelector('.fp-act[data-act="' + act + '"]');
      if (!btn) return;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      var icon = btn.querySelector('i');
      if (icon && act === 'save') {
        icon.classList.toggle('fa-solid', on);
        icon.classList.toggle('fa-regular', !on);
      }
      if (on && !reduceMotion) {
        btn.classList.remove('pop'); void btn.offsetWidth; btn.classList.add('pop');
      }
    });
  }

  function compact(n) {
    n = n || 0;
    if (n < 1000) return n ? String(n) : '';
    if (n < 1e6) return (Math.round(n / 100) / 10).toString().replace(/\.0$/, '') + 'K';
    return (Math.round(n / 1e5) / 10).toString().replace(/\.0$/, '') + 'M';
  }

  // ---------- like / repost / save ----------
  var busy = {};
  function react(card, act, btn) {
    if (!CAN_INTERACT) { needAccount(); return; }
    var id = card.dataset.post, key = id + act;
    if (busy[key]) return;
    busy[key] = true;
    var on = btn.getAttribute('aria-pressed') !== 'true';
    var n = btn.querySelector('.fp-n');
    var before = n ? n.textContent : '';
    var guess = Math.max(0, (parseInt((before || '0').replace(/\D/g, ''), 10) || 0) + (on ? 1 : -1));
    setPressed(id, act, on);
    if (!/[KM]/.test(before)) setCount(id, act, compact(guess));      // instant feedback

    send('/feed/post/' + encodeURIComponent(id) + '/' + act, { on: on ? 1 : 0 })
      .then(function (data) {
        if (data.ok) {
          setPressed(id, act, data.on);
          setCount(id, act, data.label);
          if (act === 'save') {
            if (data.on && !IS_MEMBER) toast('Saved on this device.', 'Log in', LOGIN_URL);
            else toast(data.on ? 'Saved. Find it under Highlights › Saved.' : 'Removed from Saved.');
          }
          if (act === 'repost' && data.on) toast('Reposted.');
        } else {
          setPressed(id, act, !on);
          setCount(id, act, before);
          toast(data.error || 'Could not save that. Please try again.');
        }
      })
      .catch(function () {
        setPressed(id, act, !on);
        setCount(id, act, before);
        toast('You seem to be offline. Please try again.');
      })
      .then(function () { busy[key] = false; });
  }

  // ---------- share ----------
  function share(card) {
    var id = card.dataset.post;
    var url = new URL(card.dataset.postUrl, location.origin).href;
    var nameEl = card.querySelector('.fp-name');
    var title = (nameEl ? nameEl.textContent.trim() + ' · ' : '') + 'D.A.R.T. highlight';
    var counted = function () {
      send('/feed/post/' + encodeURIComponent(id) + '/share', {}).then(function (data) {
        if (data.ok) setCount(id, 'share', data.label);
      }).catch(function () {});
    };
    if (navigator.share) {
      navigator.share({ title: title, url: url }).then(counted).catch(function () {});
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { toast('Link copied.'); counted(); },
        function () { window.prompt('Copy this link:', url); counted(); });
    } else {
      window.prompt('Copy this link:', url);
      counted();
    }
  }

  // ---------- comments ----------
  var sheet = document.getElementById('fpSheet');

  function commentItem(c) {
    var li = document.createElement('li');
    li.className = 'fp-c';
    li.dataset.comment = c.id;
    var head = document.createElement('div');
    head.className = 'fp-c-head';
    var name = document.createElement(c.author_url ? 'a' : 'span');
    name.className = 'fp-c-name';
    if (c.author_url) name.href = c.author_url;
    name.textContent = c.author;
    head.appendChild(name);
    if (c.handle) {
      var h = document.createElement('span');
      h.className = 'fp-c-handle';
      h.setAttribute('translate', 'no');
      h.textContent = c.handle;
      head.appendChild(h);
    }
    if (c.is_athlete || ['Coach', 'Scout', 'Organization', 'Visitor', 'O-D.A.R.T user'].indexOf(c.role) >= 0) {
      var tag = document.createElement('span');
      tag.className = c.is_athlete ? 'fp-c-tag' : 'fp-c-role' + (c.member ? '' : ' fp-c-visitor');
      tag.textContent = c.is_athlete ? 'Athlete' : c.role + (c.device ? ' · ' + c.device : '');
      head.appendChild(tag);
    }
    var ago = document.createElement('span');
    ago.className = 'fp-c-ago';
    ago.textContent = '· ' + c.ago;
    head.appendChild(ago);
    if (c.edited) {
      var ed = document.createElement('span');
      ed.className = 'fp-c-edited';
      ed.textContent = '· edited';
      head.appendChild(ed);
    }
    if (c.can_edit) {
      var edb = document.createElement('button');
      edb.type = 'button';
      edb.className = 'fp-c-edit';
      edb.dataset.editComment = c.id;
      edb.setAttribute('aria-label', 'Edit comment (1 minute)');
      edb.title = 'Edit (1 minute)';
      edb.innerHTML = '<i class="fa-solid fa-pen"></i>';
      head.appendChild(edb);
      // Comments can be edited for 1 minute after posting: the pen goes away then.
      if (typeof c.edit_left === 'number') setTimeout(function () { edb.remove(); }, Math.max(0, c.edit_left) * 1000);
    }
    if (c.can_delete) {
      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'fp-c-del';
      del.dataset.deleteComment = c.id;
      del.setAttribute('aria-label', 'Delete comment');
      del.title = 'Delete';
      del.innerHTML = '<i class="fa-solid fa-trash"></i>';
      head.appendChild(del);
    }
    var body = document.createElement('p');
    body.className = 'fp-c-body';
    body.textContent = c.body;
    li.appendChild(head);
    li.appendChild(body);
    return li;
  }

  function threadFor(el) { return el.closest('.fp-thread'); }

  function syncEmpty(thread) {
    var empty = thread.querySelector('[data-comments-empty]');
    if (empty) empty.hidden = !!thread.querySelector('[data-comments] .fp-c');
  }

  function loadComments(thread, append) {
    var id = thread.dataset.post;
    var list = thread.querySelector('[data-comments]');
    var more = thread.querySelector('[data-more-comments]');
    var loading = thread.querySelector('[data-comments-loading]');
    var last = append ? list.querySelector('.fp-c:last-child') : null;
    var url = '/feed/post/' + encodeURIComponent(id) + '/comments' + (last ? '?before=' + encodeURIComponent(last.dataset.comment) : '');
    if (loading) loading.hidden = false;
    if (more) more.disabled = true;
    return send(url, null, 'GET').then(function (data) {
      if (thread.dataset.post !== id) return;            // the sheet moved on to another post
      if (!append) list.textContent = '';
      (data.comments || []).forEach(function (c) { list.appendChild(commentItem(c)); });
      if (more) { more.hidden = !data.has_more; more.disabled = false; }
      syncEmpty(thread);
    }).catch(function () {
      toast('Could not load comments. Please try again.');
    }).then(function () { if (loading) loading.hidden = true; });
  }

  var lastFocus = null;
  function openSheet(card) {
    if (!sheet) return;
    var thread = sheet.querySelector('.fp-thread');
    thread.dataset.post = card.dataset.post;
    var open = sheet.querySelector('[data-open-post]');
    if (open) open.href = card.dataset.postUrl;
    thread.querySelector('[data-comments]').textContent = '';
    var err = thread.querySelector('.fp-c-error');
    if (err) err.hidden = true;
    lastFocus = document.activeElement;
    sheet.hidden = false;
    document.documentElement.classList.add('fp-sheet-open');
    loadComments(thread, false);
    var field = sheet.querySelector('textarea');
    (field || sheet.querySelector('.fp-sheet-close')).focus({ preventScroll: true });
  }

  function closeSheet() {
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    document.documentElement.classList.remove('fp-sheet-open');
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  if (sheet) {
    sheet.querySelector('.fp-sheet-close').addEventListener('click', closeSheet);
    sheet.addEventListener('click', function (e) { if (e.target === sheet) closeSheet(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSheet(); });
  }

  document.addEventListener('submit', function (e) {
    var form = e.target.closest && e.target.closest('[data-comment-form]');
    if (!form) return;
    e.preventDefault();
    var thread = threadFor(form);
    var field = form.querySelector('textarea');
    var button = form.querySelector('button[type=submit]');
    var err = form.querySelector('.fp-c-error');
    var body = (field.value || '').trim();
    if (!body) { field.focus(); return; }
    var nameField = form.querySelector('input[name=name]');
    var payload = { body: body };
    if (nameField) {
      payload.name = nameField.value.trim();
      try { localStorage.setItem('dartVisitorName', payload.name); } catch (err) {}
    }
    button.disabled = true;
    send('/feed/post/' + encodeURIComponent(thread.dataset.post) + '/comments', payload)
      .then(function (data) {
        if (!data.ok) {
          err.textContent = data.error || 'Could not post your comment.';
          err.hidden = false;
          return;
        }
        err.hidden = true;
        field.value = '';
        field.style.height = '';
        var list = thread.querySelector('[data-comments]');
        list.insertBefore(commentItem(data.comment), list.firstChild);
        syncEmpty(thread);
        setCount(thread.dataset.post, 'comment', data.label);
      })
      .catch(function () { err.textContent = 'You seem to be offline. Please try again.'; err.hidden = false; })
      .then(function () { button.disabled = false; });
  });

  document.addEventListener('input', function (e) {
    if (e.target.matches && e.target.matches('[data-comment-form] textarea')) {
      e.target.style.height = 'auto';
      e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px';
    }
  });

  // A visitor's name is remembered on this device (convenience only).
  try {
    var savedName = localStorage.getItem('dartVisitorName');
    if (savedName) document.querySelectorAll('[data-comment-form] input[name=name]').forEach(function (i) { i.value = savedName; });
  } catch (err) {}

  // ---------- Add post (athletes): the uploader in a sheet ----------
  var composer = document.getElementById('fpComposer');
  var composerFocus = null;
  function openComposer() {
    if (!composer) return;
    composerFocus = document.activeElement;
    composer.hidden = false;
    document.documentElement.classList.add('fp-sheet-open');
    var first = composer.querySelector('.hl-drop') || composer.querySelector('[data-close-composer]');
    if (first) first.focus({ preventScroll: true });
  }
  function closeComposer() {
    if (!composer || composer.hidden) return;
    var uploading = composer.querySelector('.hl-upload-btn[disabled]') && !composer.querySelector('.hl-progress[hidden]');
    if (uploading && !window.confirm('Your upload is still running. Close anyway?')) return;
    composer.hidden = true;
    document.documentElement.classList.remove('fp-sheet-open');
    if (composerFocus && composerFocus.focus) composerFocus.focus({ preventScroll: true });
  }
  if (composer) {
    composer.addEventListener('click', function (e) { if (e.target === composer) closeComposer(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeComposer(); });
  }

  // ---------- clicks ----------
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-open-composer]')) { openComposer(); return; }
    if (e.target.closest('[data-close-composer]')) { closeComposer(); return; }

    // Edit own comment: the text becomes a small box with Save / Cancel.
    var edit = e.target.closest('[data-edit-comment]');
    if (edit) {
      var item = edit.closest('.fp-c');
      if (!item || item.querySelector('.fp-c-editor')) return;
      var bodyEl = item.querySelector('.fp-c-body');
      var form = document.createElement('form');
      form.className = 'fp-c-editor';
      var box = document.createElement('textarea');
      box.className = 'form-control form-control-sm';
      box.rows = 2;
      box.maxLength = 300;
      box.value = bodyEl.textContent;
      box.setAttribute('aria-label', 'Edit your comment');
      var row = document.createElement('div');
      row.className = 'fp-c-editor-actions';
      row.innerHTML = '<button type="button" class="btn btn-sm btn-link" data-edit-cancel>Cancel</button>' +
        '<button type="submit" class="btn btn-sm dart-btn">Save</button>';
      form.appendChild(box);
      form.appendChild(row);
      bodyEl.hidden = true;
      bodyEl.after(form);
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
      var closeEditor = function () { form.remove(); bodyEl.hidden = false; edit.focus(); };
      row.querySelector('[data-edit-cancel]').addEventListener('click', closeEditor);
      form.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { ev.stopPropagation(); closeEditor(); } });
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var text = box.value.trim();
        if (!text) { toast('Write a comment first, or delete it.'); return; }
        var save = form.querySelector('[type=submit]');
        save.disabled = true;
        send('/feed/comments/' + encodeURIComponent(edit.dataset.editComment) + '/edit', { body: text }).then(function (data) {
          save.disabled = false;
          if (!data.ok) { toast(data.error || 'Could not save your comment.'); return; }
          bodyEl.textContent = data.comment.body;
          if (data.comment.edited && !item.querySelector('.fp-c-edited')) {
            var mark = document.createElement('span');
            mark.className = 'fp-c-edited';
            mark.textContent = '· edited';
            item.querySelector('.fp-c-ago').after(mark);
          }
          closeEditor();
        }).catch(function () { save.disabled = false; toast('You seem to be offline. Please try again.'); });
      });
      return;
    }

    var del = e.target.closest('[data-delete-comment]');
    if (del) {
      if (!window.confirm('Delete this comment?')) return;
      var thread = threadFor(del);
      send('/feed/comments/' + encodeURIComponent(del.dataset.deleteComment) + '/delete', {}).then(function (data) {
        if (!data.ok) { toast(data.error || 'Could not delete the comment.'); return; }
        var li = del.closest('.fp-c');
        if (li) li.remove();
        syncEmpty(thread);
        setCount(thread.dataset.post, 'comment', data.label);
      }).catch(function () { toast('You seem to be offline. Please try again.'); });
      return;
    }

    var more = e.target.closest('[data-more-comments]');
    if (more) { loadComments(threadFor(more), true); return; }

    var btn = e.target.closest('.fp-act');
    if (btn) {
      var card = btn.closest('.fp');
      var act = btn.dataset.act;
      if (act === 'share') share(card);
      else if (act === 'comment') {
        if (SINGLE) {
          var field = document.querySelector('.fp-thread [data-comment-form] textarea');
          if (field) field.focus();
          else if (!CAN_INTERACT) needAccount();
          var t = document.querySelector('.fp-thread');
          if (t) t.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
        } else openSheet(card);
      } else react(card, act, btn);
      return;
    }

    var sound = e.target.closest('.fp-sound');
    if (sound) { toggleSound(sound.closest('[data-video-box]').querySelector('video')); return; }

    var nav = e.target.closest('.fp-nav');
    if (nav) { slideBy(nav.closest('[data-carousel]'), nav.classList.contains('fp-next') ? 1 : -1); return; }

    var media = e.target.closest('.fp-media-video');
    if (media) {
      var v = media.querySelector('video');
      if (v.paused) { v.dataset.userPaused = ''; playVideo(v); }
      else { v.pause(); v.dataset.userPaused = '1'; }
    }
  });

  // ---------- videos ----------
  var soundOn = false;
  function updateSoundButton(video) {
    var b = video.closest('[data-video-box]').querySelector('.fp-sound');
    if (!b) return;
    b.setAttribute('aria-pressed', video.muted ? 'false' : 'true');
    b.setAttribute('aria-label', video.muted ? 'Turn sound on' : 'Turn sound off');
    b.querySelector('i').className = 'fa-solid ' + (video.muted ? 'fa-volume-xmark' : 'fa-volume-high');
  }

  function pauseOthers(current) {
    document.querySelectorAll('.fp-video').forEach(function (v) { if (v !== current && !v.paused) v.pause(); });
  }

  function ensureSrc(video) {
    if (!video.getAttribute('src') && video.dataset.src) {
      video.preload = 'metadata';
      video.src = video.dataset.src;
    }
  }

  function playVideo(video) {
    ensureSrc(video);
    pauseOthers(video);
    video.muted = !soundOn;
    updateSoundButton(video);
    var p = video.play();
    if (p && p.catch) p.catch(function () { video.muted = true; updateSoundButton(video); video.play().catch(function () {}); });
  }

  function toggleSound(video) {
    if (!video) return;
    soundOn = video.muted;               // turning it on (or off) for every video from now on
    video.muted = !soundOn;
    updateSoundButton(video);
    if (video.paused) playVideo(video);
  }

  function watch(scope) {
    scope.querySelectorAll('.fp-video').forEach(function (v) {
      if (v.dataset.watched) return;
      v.dataset.watched = '1';
      var frame = v.closest('[data-video-box]');
      v.addEventListener('play', function () { frame.classList.remove('is-paused'); });
      v.addEventListener('pause', function () { frame.classList.add('is-paused'); });
      frame.classList.add('is-paused');
      if (nearObserver) nearObserver.observe(v); else ensureSrc(v);
      if (playObserver) playObserver.observe(v);
    });
    scope.querySelectorAll('.fp-more').forEach(function (m) { if (moreObserver) moreObserver.observe(m); });
    scope.querySelectorAll('[data-carousel]').forEach(setupCarousel);
    // Desktop: a blurred copy of each photo fills its frame (see dart-feed.css).
    scope.querySelectorAll('.fp-photo img').forEach(function (img) {
      var frame = img.closest('.fp-photo');
      function lit() { frame.classList.add('fp-has-bg'); }
      if (img.complete && img.naturalWidth) lit(); else img.addEventListener('load', lit, { once: true });
    });
    // Single videos take their own shape too (desktop).
    scope.querySelectorAll('.fp-media:not(.fp-carousel) > .fp-media-video').forEach(function (frame) {
      if (frame.dataset.fitted) return;
      frame.dataset.fitted = '1';
      fitToFirst(frame);
    });
  }

  // ---------- several photos / videos in one post: swipe left / right ----------
  function slideIndex(track) {
    return track.clientWidth ? Math.round(track.scrollLeft / track.clientWidth) : 0;
  }

  function slideBy(carousel, step) {
    if (!carousel) return;
    var track = carousel.querySelector('.fp-track');
    var last = track.children.length - 1;
    var to = Math.max(0, Math.min(last, slideIndex(track) + step));
    track.scrollTo({ left: to * track.clientWidth, behavior: reduceMotion ? 'auto' : 'smooth' });
  }

  function setupCarousel(carousel) {
    if (carousel.dataset.ready) return;
    carousel.dataset.ready = '1';
    var track = carousel.querySelector('.fp-track');
    var total = track.children.length;
    var count = carousel.querySelector('.fp-count');
    var dots = carousel.querySelectorAll('.fp-dots span');
    var prev = carousel.querySelector('.fp-prev'), next = carousel.querySelector('.fp-next');
    var shown = -1, ticking = false;

    function update() {
      ticking = false;
      var i = Math.max(0, Math.min(total - 1, slideIndex(track)));
      if (i === shown) return;
      shown = i;
      count.textContent = (i + 1) + '/' + total;
      dots.forEach(function (d, n) { d.classList.toggle('on', n === i); });
      prev.hidden = i === 0;
      next.hidden = i === total - 1;
      // The next slide's photo / video starts loading before it is swiped in.
      [i, i + 1].forEach(function (n) {
        var slide = track.children[n];
        if (!slide) return;
        var img = slide.querySelector('img[loading="lazy"]');
        if (img) img.loading = 'eager';
        var v = slide.querySelector('.fp-video');
        if (v) ensureSrc(v);
      });
    }
    track.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }, { passive: true });
    // At the first / last photo, pushing further out must not become the
    // browser's "swipe back / forward" (or switch Home | Highlights).
    var sx = 0, sy = 0;
    track.addEventListener('touchstart', function (e) {
      sx = e.touches[0].clientX; sy = e.touches[0].clientY;
    }, { passive: true });
    track.addEventListener('touchmove', function (e) {
      var mx = e.touches[0].clientX - sx, my = e.touches[0].clientY - sy;
      if (Math.abs(mx) <= Math.abs(my)) return;                     // scrolling the page: leave it
      var atStart = track.scrollLeft <= 0, atEnd = track.scrollLeft >= track.scrollWidth - track.clientWidth - 1;
      if ((mx > 0 && atStart) || (mx < 0 && atEnd)) e.preventDefault();
    }, { passive: false });
    track.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { e.preventDefault(); slideBy(carousel, 1); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); slideBy(carousel, -1); }
    });
    window.addEventListener('resize', function () { shown = -1; update(); });
    update();
    fitToFirst(carousel);
  }

  // The media box takes the shape of the post's first photo / video (used on
  // desktop): between tall 4:5 and wide 16:9, like X.
  function setRatio(box, w, h) {
    if (!w || !h) return;
    var r = Math.max(0.8, Math.min(16 / 9, w / h));
    box.style.setProperty('--fp-ratio', r.toFixed(3));
  }
  function fitToFirst(box) {
    var first = box.querySelector('.fp-slide img, .fp-slide video, .fp-media-video video');
    if (!first) return;
    if (first.tagName === 'IMG') {
      if (first.complete && first.naturalWidth) setRatio(box, first.naturalWidth, first.naturalHeight);
      else first.addEventListener('load', function () { setRatio(box, first.naturalWidth, first.naturalHeight); }, { once: true });
    } else {
      if (first.videoWidth) setRatio(box, first.videoWidth, first.videoHeight);
      else first.addEventListener('loadedmetadata', function () { setRatio(box, first.videoWidth, first.videoHeight); }, { once: true });
    }
  }

  var hasIO = 'IntersectionObserver' in window;
  // Start loading a video shortly before it scrolls into view.
  var nearObserver = hasIO ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) { ensureSrc(en.target); nearObserver.unobserve(en.target); }
    });
  }, { rootMargin: '600px 0px' }) : null;

  // Play (muted) while at least 60% is on screen; pause when it leaves.
  var autoplay = !saveData && !reduceMotion;
  var playObserver = hasIO ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      var v = en.target;
      if (en.isIntersecting && en.intersectionRatio >= 0.6) {
        if (autoplay && !v.dataset.userPaused) playVideo(v);
      } else if (!v.paused) {
        v.pause();
      }
    });
  }, { threshold: [0, 0.6] }) : null;

  // ---------- endless scrolling ----------
  var loadingMore = false;
  var moreObserver = hasIO ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) { if (en.isIntersecting) loadMore(en.target); });
  }, { rootMargin: '800px 0px' }) : null;

  function loadMore(box) {
    if (loadingMore || !box.isConnected) return;
    loadingMore = true;
    if (moreObserver) moreObserver.unobserve(box);
    box.classList.add('loading');
    fetch(box.dataset.next, { credentials: 'same-origin', headers: { 'Accept': 'text/html' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        var tpl = document.createElement('template');
        tpl.innerHTML = html;
        var frag = tpl.content;
        var holder = document.createElement('div');
        holder.appendChild(frag);
        var nodes = Array.prototype.slice.call(holder.childNodes);
        var parent = box.parentNode;
        nodes.forEach(function (n) { parent.insertBefore(n, box); });
        box.remove();
        watch(parent);
      })
      .catch(function () {
        box.classList.remove('loading');      // the "Show more" link still works
      })
      .then(function () { loadingMore = false; });
  }

  // Tapping "Show more" fetches in place instead of opening a new page.
  document.addEventListener('click', function (e) {
    var a = e.target.closest('.fp-more-btn');
    if (!a || !hasIO) return;
    e.preventDefault();
    loadMore(a.closest('.fp-more'));
  });

  watch(document);
})();
