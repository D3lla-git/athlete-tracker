// Bump this version whenever PRECACHE_URLS or caching behaviour changes.
// Old 'dart-pwa-*' caches are deleted on activate.
const CACHE_NAME = 'dart-pwa-v4';

// Pages saved for offline use (the athlete dashboard). This name is
// deliberately NOT versioned, so a service-worker update does not wipe an
// athlete's offline dashboard. public/static/js/pwa.js deletes it on logout.
const PAGES_CACHE = 'dart-pages-v1';

// Versioned third-party CSS/JS/fonts (Bootstrap, Font Awesome).
const CDN_CACHE = 'dart-cdn-v1';

const OFFLINE_URL = '/offline';

// Everything the offline page needs to render without a network.
const PRECACHE_URLS = [
    OFFLINE_URL,
    '/static/images/dartlogo.png',
    '/static/images/icons/favicon-32.png'
];

// Pages that may be stored on the device. The server must also opt in
// per response with the X-DART-Offline-Cacheable header (only verified
// athletes get it), so other roles' pages are never stored.
const OFFLINE_CACHEABLE_PAGES = ['/student'];

// CDN hosts whose URLs include a version number, so cache-first is safe.
const CDN_HOSTS = ['cdn.jsdelivr.net', 'cdnjs.cloudflare.com'];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                // cache: 'reload' bypasses the HTTP cache so a fresh copy is stored.
                return cache.addAll(
                    PRECACHE_URLS.map(url => new Request(url, { cache: 'reload' }))
                );
            })
            .then(() => {
                return self.skipWaiting();
            })
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        (async () => {
            const cacheNames = await caches.keys();

            await Promise.all(
                cacheNames
                    .filter(name => name.startsWith('dart-pwa-') && name !== CACHE_NAME)
                    .map(name => caches.delete(name))
            );

            // Navigation preload lets the browser start the page request
            // while the service worker is still booting.
            if (self.registration.navigationPreload) {
                await self.registration.navigationPreload.enable();
            }

            await self.clients.claim();
        })()
    );
});

// Store an offline copy of an opted-in page, without the one-time flash
// messages (e.g. "Login successful") that were on it at the time.
async function storeOfflinePage(pathname, response) {
    const cache = await caches.open(PAGES_CACHE);

    // Redirected (e.g. session expired → login page): leave the saved copy alone.
    if (response.redirected || response.status !== 200) {
        return;
    }

    // The server no longer allows this page offline (e.g. different
    // user or role): remove any saved copy.
    if (response.headers.get('X-DART-Offline-Cacheable') !== '1') {
        await cache.delete(pathname);
        return;
    }

    const html = await response.text();
    const cleanedHtml = html.replace(
        /<!--dart-flash-start-->[\s\S]*?<!--dart-flash-end-->/,
        ''
    );

    await cache.put(
        pathname,
        new Response(cleanedHtml, {
            status: 200,
            headers: { 'Content-Type': 'text/html; charset=utf-8' }
        })
    );
}

self.addEventListener('fetch', event => {
    const request = event.request;

    // Only handle GET requests. Form posts (login, payments, uploads)
    // always go straight to the network.
    if (request.method !== 'GET') {
        return;
    }

    // Work around a Chrome DevTools quirk that throws on these requests.
    if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') {
        return;
    }

    const url = new URL(request.url);

    // Third-party CDN assets: cache-first (their URLs are versioned).
    if (url.origin !== self.location.origin) {
        if (
            CDN_HOSTS.includes(url.hostname) &&
            ['style', 'script', 'font'].includes(request.destination)
        ) {
            event.respondWith(
                (async () => {
                    const cache = await caches.open(CDN_CACHE);
                    const cachedResponse = await cache.match(request);

                    if (cachedResponse) {
                        return cachedResponse;
                    }

                    const response = await fetch(request);

                    // 'opaque' = no-CORS stylesheet/script; its status is hidden but valid.
                    if (response.status === 200 || response.type === 'opaque') {
                        event.waitUntil(
                            cache.put(request, response.clone()).catch(() => {})
                        );
                    }

                    return response;
                })()
            );
        }

        return;
    }

    // Navigation requests:
    // Always go to the live server first. Only the athlete dashboard is
    // ever stored (see OFFLINE_CACHEABLE_PAGES); every other page shows
    // the offline page when the network is unavailable.
    if (request.mode === 'navigate') {
        const isOfflineCacheable = OFFLINE_CACHEABLE_PAGES.includes(url.pathname);

        event.respondWith(
            (async () => {
                try {
                    const preloadResponse = await event.preloadResponse;
                    const response = preloadResponse || await fetch(request);

                    if (isOfflineCacheable) {
                        event.waitUntil(
                            storeOfflinePage(url.pathname, response.clone())
                                .catch(() => {})
                        );
                    }

                    return response;
                } catch (error) {
                    if (isOfflineCacheable) {
                        const pagesCache = await caches.open(PAGES_CACHE);
                        const savedPage = await pagesCache.match(url.pathname);

                        if (savedPage) {
                            return savedPage;
                        }
                    }

                    const cache = await caches.open(CACHE_NAME);
                    const offlineResponse = await cache.match(OFFLINE_URL);

                    return offlineResponse || Response.error();
                }
            })()
        );

        return;
    }

    // Static files:
    // Network first, then cached copy if offline.
    // Static URLs are not fingerprinted, so network-first avoids serving
    // stale CSS/JS after a deploy.
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            fetch(request)
                .then(response => {
                    // Only cache complete, same-origin responses (not 206 partials).
                    if (response.status === 200 && response.type === 'basic') {
                        const responseClone = response.clone();

                        event.waitUntil(
                            caches.open(CACHE_NAME)
                                .then(cache => cache.put(request, responseClone))
                                .catch(() => {
                                    // Ignore cache write failures (e.g. quota).
                                })
                        );
                    }

                    return response;
                })
                .catch(async () => {
                    const cachedResponse = await caches.match(request);

                    return cachedResponse || Response.error();
                })
        );
    }

    // Everything else (API calls, profile pictures, ID documents, etc.)
    // is not intercepted and goes straight to the network.
});
