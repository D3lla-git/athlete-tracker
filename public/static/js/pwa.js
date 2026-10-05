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
    // OFFLINE PAGE PRIVACY (shared devices)
    // ==========================================================
    // The service worker keeps a copy of the athlete dashboard for
    // offline use (cache 'dart-pages-v1'). Remove it when the user logs
    // out, or when a different user logs in on the same device.
    // Unsynced offline records live in IndexedDB, keyed by user, and are
    // NOT deleted here, so logging out never loses an athlete's records.

    var PAGES_CACHE = 'dart-pages-v1';
    var OWNER_STORAGE_KEY = 'dartOfflinePagesOwner';

    function clearOfflinePages() {
        try {
            window.localStorage.removeItem(OWNER_STORAGE_KEY);
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
        protectOfflinePages();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady);
    } else {
        onReady();
    }
})();
