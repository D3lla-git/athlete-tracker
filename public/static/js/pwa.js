(function () {
    'use strict';

    // ==========================================================
    // ENVIRONMENT DETECTION
    // ==========================================================

    // True when D.A.R.T. is already running as an installed app.
    function isStandalone() {
        return (
            window.matchMedia('(display-mode: standalone)').matches ||
            window.navigator.standalone === true
        );
    }

    // iPhone/iPod/iPad. iPadOS reports itself as a Mac, so it is
    // detected by touch support.
    function isIos() {
        return (
            /iphone|ipad|ipod/i.test(window.navigator.userAgent) ||
            (window.navigator.platform === 'MacIntel' &&
                window.navigator.maxTouchPoints > 1)
        );
    }

    // ==========================================================
    // SERVICE WORKER REGISTRATION + UPDATE CHECKS
    // ==========================================================

    // Installed apps can stay open for days, so check for a new
    // service worker whenever the app returns to the foreground
    // (at most once an hour).
    var UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000;

    function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            console.log('D.A.R.T. service workers are not supported by this browser.');
            return;
        }

        window.addEventListener('load', function () {
            navigator.serviceWorker
                .register('/service-worker.js', {
                    scope: '/',
                    // Always check the server for a new service worker,
                    // never an HTTP-cached copy.
                    updateViaCache: 'none'
                })
                .then(function (registration) {
                    console.log(
                        'D.A.R.T. service worker registered:',
                        registration.scope
                    );

                    saveOfflinePagesSoon();

                    var lastUpdateCheck = Date.now();

                    document.addEventListener('visibilitychange', function () {
                        if (document.visibilityState !== 'visible') {
                            return;
                        }

                        if (Date.now() - lastUpdateCheck < UPDATE_CHECK_INTERVAL_MS) {
                            return;
                        }

                        lastUpdateCheck = Date.now();

                        registration.update().catch(function () {
                            // Offline or server unavailable; try again later.
                        });
                    });
                })
                .catch(function (error) {
                    console.error(
                        'D.A.R.T. service worker registration failed:',
                        error
                    );
                });
        });
    }

    // ==========================================================
    // OFFLINE RECORD FORM (verified athletes)
    // ==========================================================
    // Ask the service worker to keep the record form and dashboard on the
    // device, so athletes can record games even if they have never opened
    // the form online. At most every 10 minutes per tab session.
    var OFFLINE_PAGES_INTERVAL_MS = 10 * 60 * 1000;
    var OFFLINE_PAGES_KEY = 'dartOfflinePagesSavedAt';

    function saveOfflinePagesSoon() {
        if (!document.querySelector('meta[name="dart-offline-athlete"]') || !navigator.onLine) {
            return;
        }

        var last = 0;

        try {
            last = parseInt(window.sessionStorage.getItem(OFFLINE_PAGES_KEY) || '0', 10);
        } catch (e) {}

        if (Date.now() - last < OFFLINE_PAGES_INTERVAL_MS) {
            return;
        }

        navigator.serviceWorker.ready.then(function (registration) {
            if (!registration.active) {
                return;
            }

            registration.active.postMessage({ type: 'dart-save-offline-pages' });

            try {
                window.sessionStorage.setItem(OFFLINE_PAGES_KEY, String(Date.now()));
            } catch (e) {}
        });
    }

    // ==========================================================
    // INSTALL EXPERIENCE
    // ==========================================================
    // Android / desktop Chrome & Edge: use the browser's install prompt
    //   (captured early in base.html as window.dartDeferredInstallPrompt).
    // iOS: no install prompt API, so show Share → Add to Home Screen steps.
    // Already installed / unsupported browser: the button stays hidden.

    function setUpInstallButton() {
        var installButton = document.getElementById('pwaInstallBtn');

        if (!installButton) {
            return;
        }

        function showButton() {
            installButton.hidden = false;
        }

        function hideButton() {
            installButton.hidden = true;
        }

        if (isStandalone()) {
            hideButton();
            return;
        }

        // iOS: open the instructions modal.
        if (isIos()) {
            var iosModal = document.getElementById('pwaIosInstallModal');

            if (!iosModal || typeof bootstrap === 'undefined') {
                return;
            }

            installButton.addEventListener('click', function () {
                bootstrap.Modal.getOrCreateInstance(iosModal).show();
            });

            showButton();
            return;
        }

        // Android / desktop: show the button once the browser says
        // the app can be installed.
        if (window.dartDeferredInstallPrompt) {
            showButton();
        }

        window.addEventListener('beforeinstallprompt', function (event) {
            event.preventDefault();
            window.dartDeferredInstallPrompt = event;
            showButton();
        });

        installButton.addEventListener('click', function () {
            var promptEvent = window.dartDeferredInstallPrompt;

            if (!promptEvent) {
                hideButton();
                return;
            }

            // A prompt event can only be used once.
            window.dartDeferredInstallPrompt = null;
            hideButton();

            promptEvent.prompt();

            promptEvent.userChoice
                .then(function (choice) {
                    console.log('D.A.R.T. install prompt outcome:', choice.outcome);
                })
                .catch(function () {
                    // Ignore: the browser closed the prompt.
                });
        });

        window.addEventListener('appinstalled', function () {
            window.dartDeferredInstallPrompt = null;
            hideButton();
        });
    }

    // ==========================================================
    // "DOWNLOAD THE APP" SHEET (every visit in a browser)
    // ==========================================================
    // Each time D.A.R.T. is opened in a browser (not the installed app), a
    // sheet offers the app. One tap on "Download App to phone":
    //   Android / desktop Chrome, Edge, Samsung Internet: the browser's own
    //     install dialog opens (browsers always ask the user to confirm it;
    //     no website can install an app silently).
    //   iPhone / iPad: Apple has no install button for websites, so the
    //     Share -> Add to Home Screen steps are shown.
    //   Android in-app browsers (Facebook, Instagram, ...) that can't install:
    //     D.A.R.T. reopens in Chrome, where the install dialog works.
    // "Not now" hides it until the next visit (browser session).
    var SHEET_SEEN_KEY = 'dartInstallSheetClosed';

    function isAndroid() {
        return /android/i.test(window.navigator.userAgent);
    }

    function isMobile() {
        return isAndroid() || isIos() || /mobi/i.test(window.navigator.userAgent);
    }

    // Real Chrome on Android (not a WebView / in-app browser / other browser).
    function isAndroidChrome() {
        var ua = window.navigator.userAgent;
        return isAndroid() && /Chrome\//.test(ua) &&
            !/; wv\)|SamsungBrowser|Firefox|EdgA|OPR|FBAN|FBAV|Instagram|Line\/|Snapchat|TikTok|MiuiBrowser|UCBrowser/i.test(ua);
    }

    function openInChrome() {
        var url = window.location;
        window.location.href = 'intent://' + url.host + url.pathname + url.search +
            '#Intent;scheme=' + url.protocol.replace(':', '') + ';package=com.android.chrome;end';
    }

    function setUpInstallSheet() {
        var sheet = document.getElementById('pwaInstallSheet');

        if (!sheet || isStandalone()) {
            return;
        }

        try {
            if (window.sessionStorage.getItem(SHEET_SEEN_KEY)) {
                return;
            }
        } catch (e) {}

        var go = document.getElementById('pwaSheetInstall');
        var label = document.getElementById('pwaSheetInstallLabel');
        var text = document.getElementById('pwaSheetText');
        var mode = null;

        if (!isMobile()) {
            label.textContent = 'Install App on this computer';
        }

        function close() {
            sheet.hidden = true;
            try {
                window.sessionStorage.setItem(SHEET_SEEN_KEY, '1');
            } catch (e) {}
        }

        function show(newMode) {
            mode = newMode;
            if (mode === 'chrome') {
                label.textContent = 'Download App to phone';
                text.textContent = 'This app browser can\'t install apps. One tap opens D.A.R.T. in Chrome to install it.';
            }
            sheet.hidden = false;
        }

        document.getElementById('pwaSheetLater').addEventListener('click', close);

        go.addEventListener('click', function () {
            if (mode === 'ios') {
                close();
                var iosModal = document.getElementById('pwaIosInstallModal');
                if (iosModal && typeof bootstrap !== 'undefined') {
                    bootstrap.Modal.getOrCreateInstance(iosModal).show();
                }
                return;
            }
            if (mode === 'chrome') {
                close();
                openInChrome();
                return;
            }
            var promptEvent = window.dartDeferredInstallPrompt;
            if (!promptEvent) {
                close();
                return;
            }
            // A prompt event can only be used once.
            window.dartDeferredInstallPrompt = null;
            close();
            promptEvent.prompt();
            promptEvent.userChoice.catch(function () {});
        });

        window.addEventListener('appinstalled', close);

        if (isIos()) {
            show('ios');
            return;
        }

        if (window.dartDeferredInstallPrompt) {
            show('prompt');
            return;
        }

        window.addEventListener('beforeinstallprompt', function (event) {
            event.preventDefault();
            window.dartDeferredInstallPrompt = event;
            if (sheet.hidden) {
                show('prompt');
            }
        });

        // No install dialog after a few seconds on Android: in-app / other
        // browsers get "open in Chrome". In Chrome itself this means the app
        // is already installed, so nothing is shown.
        window.setTimeout(function () {
            if (mode || !isAndroid() || isAndroidChrome()) {
                return;
            }
            show('chrome');
        }, 3500);
    }

    // ==========================================================
    // OFFLINE PAGE PRIVACY (shared devices)
    // ==========================================================
    // The service worker keeps a copy of the athlete dashboard for
    // offline use (cache 'dart-pages-v1'). Remove it when the user logs
    // out, or when a different user logs in on the same device.
    // Unsynced offline records live in IndexedDB, keyed by user, and are
    // NOT deleted here, so logging out never loses an athlete's records.

    var PAGES_CACHE = 'dart-pages-v1';
    var OWNER_STORAGE_KEY = 'dartOfflinePagesOwner';
    // The verified athlete whose record form the offline page may show
    // (offline.html reads it). Same lifetime as the saved pages.
    var OFFLINE_ATHLETE_KEY = 'dartOfflineAthlete';

    function rememberOfflineAthlete() {
        var element = document.getElementById('dartOfflineAthlete');
        var currentUser = (document.querySelector('meta[name="dart-user"]') || {}).content || '';

        try {
            if (element) {
                window.localStorage.setItem(OFFLINE_ATHLETE_KEY, element.textContent.trim());
            } else if (currentUser) {
                // Signed in as someone who can't record games: forget it.
                window.localStorage.removeItem(OFFLINE_ATHLETE_KEY);
            }
        } catch (e) {}
    }

    function clearOfflinePages() {
        try {
            window.localStorage.removeItem(OWNER_STORAGE_KEY);
            window.localStorage.removeItem(OFFLINE_ATHLETE_KEY);
        } catch (e) {}

        if (!('caches' in window)) {
            return Promise.resolve();
        }

        return window.caches.delete(PAGES_CACHE).catch(function () {});
    }

    function protectOfflinePages() {
        var userMeta = document.querySelector('meta[name="dart-user"]');
        var currentUser = userMeta ? userMeta.content : '';

        if (currentUser) {
            var savedOwner = null;

            try {
                savedOwner = window.localStorage.getItem(OWNER_STORAGE_KEY);
            } catch (e) {}

            if (savedOwner && savedOwner !== currentUser) {
                clearOfflinePages();
            }

            try {
                window.localStorage.setItem(OWNER_STORAGE_KEY, currentUser);
            } catch (e) {}
        }

        document.querySelectorAll('form[data-dart-logout]').forEach(function (form) {
            form.addEventListener('submit', function (event) {
                event.preventDefault();

                // form.submit() does not re-fire this handler.
                clearOfflinePages().then(function () {
                    form.submit();
                });
            });
        });
    }

    registerServiceWorker();

    function onReady() {
        setUpInstallButton();
        setUpInstallSheet();
        protectOfflinePages();
        rememberOfflineAthlete();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady);
    } else {
        onReady();
    }
})();
