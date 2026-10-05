/*
 * D.A.R.T. — Offline record saving and sync (athletes only)
 *
 * When an athlete has no internet or mobile data, "Submit Record" saves the
 * record on the device (IndexedDB) instead of losing it. Saved records are
 * uploaded to /api/records when the athlete is back online and logged in,
 * where they go through the same validation as the normal form and arrive as
 * 'pending' for coach/admin approval.
 *
 * Connectivity is never trusted from navigator.onLine alone: a phone that is
 * out of data often still reports itself as online. A real request is always
 * attempted first, and the record is saved on the device only if it fails.
 *
 * Saved records are keyed by athlete (userId), so on a shared device one
 * athlete's records are never shown to, or uploaded by, another account.
 */
(function () {
    'use strict';

    var contextElement = document.getElementById('dartAthleteContext');

    if (!contextElement || !('indexedDB' in window) || !('fetch' in window)) {
        return;
    }

    var context;

    try {
        context = JSON.parse(contextElement.textContent);
    } catch (e) {
        return;
    }

    var userId = context.user_id;

    var DB_NAME = 'dart-offline';
    var DB_VERSION = 1;
    var STORE_NAME = 'pendingRecords';

    // Slow 2G connections can take a while; after this the request is
    // treated as failed and the record is saved on the device instead.
    var REQUEST_TIMEOUT_MS = 20000;

    // Minimum gap between automatic sync attempts when the app regains focus.
    var FOCUS_SYNC_INTERVAL_MS = 30 * 1000;

    // ==========================================================
    // INDEXEDDB STORAGE
    // ==========================================================

    var dbPromise = null;

    function openDb() {
        if (!dbPromise) {
            dbPromise = new Promise(function (resolve, reject) {
                var request = window.indexedDB.open(DB_NAME, DB_VERSION);

                request.onupgradeneeded = function () {
                    var db = request.result;

                    if (!db.objectStoreNames.contains(STORE_NAME)) {
                        var store = db.createObjectStore(STORE_NAME, { keyPath: 'id' });
                        store.createIndex('userId', 'userId', { unique: false });
                    }
                };

                request.onsuccess = function () {
                    resolve(request.result);
                };

                request.onerror = function () {
                    dbPromise = null;
                    reject(request.error);
                };
            });
        }

        return dbPromise;
    }

    // Run one request in a transaction and resolve once it is committed.
    function runRequest(mode, makeRequest) {
        return openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                var transaction = db.transaction(STORE_NAME, mode);
                var request = makeRequest(transaction.objectStore(STORE_NAME));

                transaction.oncomplete = function () {
                    resolve(request.result);
                };

                transaction.onerror = transaction.onabort = function () {
                    reject(transaction.error);
                };
            });
        });
    }

    function getMyRecords() {
        return runRequest('readonly', function (store) {
            return store.index('userId').getAll(userId);
        }).then(function (items) {
            return (items || []).sort(function (a, b) {
                return a.savedAt - b.savedAt;
            });
        });
    }

    function putRecord(item) {
        return runRequest('readwrite', function (store) {
            return store.put(item);
        });
    }

    function deleteRecord(id) {
        return runRequest('readwrite', function (store) {
            return store.delete(id);
        });
    }

    function makeId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }

        return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    }

    // ==========================================================
    // FORM DATA
    // ==========================================================

    // Exactly what a normal form post would send (FormData skips disabled
    // fields the same way), minus the page's CSRF token, which will have
    // expired by the time the record is uploaded.
    function collectFields(form) {
        var fields = [];

        new FormData(form).forEach(function (value, name) {
            if (name === 'csrf_token' || typeof value !== 'string') {
                return;
            }

            fields.push([name, value]);
        });

        return fields;
    }

    function fieldValue(fields, name) {
        for (var i = 0; i < fields.length; i++) {
            if (fields[i][0] === name) {
                return fields[i][1];
            }
        }

        return '';
    }

    function summarize(fields) {
        return {
            sport: fieldValue(fields, 'sport'),
            team: fieldValue(fields, 'team'),
            opponent: fieldValue(fields, 'team_played_against'),
            gameDate: fieldValue(fields, 'game_date'),
            competition: fieldValue(fields, 'competition_category')
        };
    }

    // ==========================================================
    // NETWORK
    // ==========================================================

    function fetchWithTimeout(url, options) {
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timer = null;

        if (controller) {
            options.signal = controller.signal;
            timer = setTimeout(function () {
                controller.abort();
            }, REQUEST_TIMEOUT_MS);
        }

        options.credentials = 'same-origin';
        options.cache = 'no-store';

        return fetch(url, options).finally(function () {
            if (timer) {
                clearTimeout(timer);
            }
        });
    }

    // Only trust JSON from our own API. Anything else (a carrier "buy data"
    // page, a proxy error, an expired-CSRF error page) means "try again later".
    function readJson(response) {
        var contentType = response.headers.get('Content-Type') || '';

        if (contentType.indexOf('application/json') === -1) {
            return Promise.resolve(null);
        }

        return response.json().catch(function () {
            return null;
        });
    }

    // Resolves to the server's status (who is logged in + a fresh CSRF
    // token), or null if the server cannot be reached.
    function getSyncStatus() {
        return fetchWithTimeout('/api/records/sync-status', {
            method: 'GET',
            headers: { 'Accept': 'application/json' }
        })
            .then(function (response) {
                if (!response.ok || response.redirected) {
                    return null;
                }

                return readJson(response);
            })
            .catch(function () {
                return null;
            });
    }

    // Resolves to { outcome, error } where outcome is one of:
    // 'created', 'duplicate', 'invalid', 'login_required', 'not_allowed',
    // or 'unreachable' (network failure, timeout, non-JSON or server error).
    function uploadFields(fields, csrfToken, mode) {
        var body = new URLSearchParams();

        fields.forEach(function (field) {
            body.append(field[0], field[1]);
        });

        return fetchWithTimeout('/api/records', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-DART-Submit-Mode': mode
            },
            body: body
        })
            .then(function (response) {
                return readJson(response).then(function (data) {
                    if (response.status === 201 && data && data.ok) {
                        return { outcome: 'created' };
                    }

                    if (data && data.reason) {
                        return { outcome: data.reason, error: data.error };
                    }

                    return { outcome: 'unreachable' };
                });
            })
            .catch(function () {
                return { outcome: 'unreachable' };
            });
    }

    // ==========================================================
    // UI HELPERS
    // ==========================================================

    function showFormAlert(type, message) {
        var container = document.getElementById('recordFormAlert');

        if (!container) {
            return;
        }

        container.innerHTML = '';

        var alert = document.createElement('div');
        alert.className = 'alert alert-' + type + ' alert-dismissible fade show';

        var text = document.createElement('span');
        text.textContent = message;
        alert.appendChild(text);

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close';
        close.setAttribute('data-bs-dismiss', 'alert');
        close.setAttribute('aria-label', 'Close');
        alert.appendChild(close);

        container.appendChild(alert);
        container.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // Small notice for pages without the dashboard panel (e.g. Search).
    function showToast(message) {
        var toast = document.createElement('div');
        toast.className = 'alert alert-success shadow';
        toast.setAttribute('role', 'status');
        toast.textContent = message;
        toast.style.cssText =
            'position:fixed;left:16px;right:16px;z-index:1080;margin:0 auto;max-width:480px;' +
            'bottom:calc(74px + env(safe-area-inset-bottom));';

        document.body.appendChild(toast);

        setTimeout(function () {
            toast.remove();
        }, 6000);
    }

    function setButtonBusy(button, busy, busyLabel) {
        if (!button) {
            return;
        }

        if (!button.dataset.idleHtml) {
            button.dataset.idleHtml = button.innerHTML;
        }

        button.disabled = busy;
        button.innerHTML = busy
            ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span> ' +
              (busyLabel || 'Submitting…')
            : button.dataset.idleHtml;
    }

    function plural(count, word) {
        return count + ' ' + word + (count === 1 ? '' : 's');
    }

    // ==========================================================
    // SAVED RECORDS PANEL (athlete dashboard)
    // ==========================================================

    var lastSyncMessage = '';

    function renderItem(item) {
        var summary = item.summary || {};

        var li = document.createElement('li');
        li.className = 'list-group-item d-flex justify-content-between align-items-start gap-2';

        var details = document.createElement('div');
        details.style.minWidth = '0';

        var title = document.createElement('div');
        title.className = 'fw-semibold';
        title.textContent =
            (summary.sport || 'Record') + ' · ' +
            (summary.team || '?') + ' vs ' + (summary.opponent || '?');
        details.appendChild(title);

        var meta = document.createElement('small');
        meta.className = 'text-muted d-block';
        meta.textContent =
            [summary.gameDate, summary.competition].filter(Boolean).join(' · ') +
            ' · Saved ' + new Date(item.savedAt).toLocaleString();
        details.appendChild(meta);

        var badge = document.createElement('span');

        if (item.status === 'needs_attention') {
            badge.className = 'badge bg-danger mt-1';
            badge.textContent = 'Needs attention';
            details.appendChild(badge);

            var error = document.createElement('small');
            error.className = 'text-danger d-block';
            error.textContent = item.error || 'This record was not accepted.';
            details.appendChild(error);
        } else {
            badge.className = 'badge bg-secondary mt-1';
            badge.textContent = 'Waiting to upload';
            details.appendChild(badge);
        }

        var actions = document.createElement('div');
        actions.className = 'd-flex flex-column gap-1 flex-shrink-0';

        var editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.className = 'btn btn-sm btn-outline-primary';
        editButton.innerHTML = '<i class="fa-solid fa-pen"></i> Edit';
        editButton.addEventListener('click', function () {
            loadIntoForm(item);
        });
        actions.appendChild(editButton);

        var deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'btn btn-sm btn-outline-danger';
        deleteButton.innerHTML = '<i class="fa-solid fa-trash"></i> Delete';
        deleteButton.addEventListener('click', function () {
            if (!window.confirm('Delete this saved record? It has not been uploaded yet.')) {
                return;
            }

            deleteRecord(item.id).then(renderPanel);
        });
        actions.appendChild(deleteButton);

        li.appendChild(details);
        li.appendChild(actions);

        return li;
    }

    function renderPanel() {
        var panel = document.getElementById('offlineRecordsPanel');

        if (!panel) {
            return Promise.resolve();
        }

        return getMyRecords().then(function (items) {
            var list = document.getElementById('offlineRecordsList');
            var count = document.getElementById('offlineRecordsCount');
            var status = document.getElementById('offlineRecordsStatus');

            panel.hidden = items.length === 0;
            count.textContent = items.length;
            list.innerHTML = '';

            items.forEach(function (item) {
                list.appendChild(renderItem(item));
            });

            status.textContent = lastSyncMessage ||
                'These records are saved on this device and will be uploaded ' +
                'for approval automatically when you are online.';
        }).catch(function () {
            // IndexedDB unavailable (e.g. private browsing); leave the panel hidden.
        });
    }

    // ==========================================================
    // EDIT A SAVED RECORD (load it back into the form)
    // ==========================================================

    // Set while a saved record is loaded into the form; it is removed from
    // the device only after the edited version is submitted or saved again.
    var editingRecordId = null;

    function setFieldValue(element, value) {
        if (element.type === 'checkbox' || element.type === 'radio') {
            element.checked = element.value === value;
        } else {
            element.value = value;
        }
    }

    function fireChange(element) {
        if (element) {
            element.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    function loadIntoForm(item) {
        var form = document.querySelector('form[data-dart-offline-form]');

        if (!form) {
            return;
        }

        form.reset();

        // The form builds some fields from earlier choices, so set those in
        // order and let the page's own scripts react: sport → position list,
        // competition → trophy/division/special options, division → Club
        // League trophies, position → GK stats.
        var sequence = [
            ['sportSelect', 'sport'],
            ['competition', 'competition'],
            ['clubDivision', 'club_division'],
            ['positionSelect', 'position']
        ];

        sequence.forEach(function (step) {
            var element = document.getElementById(step[0]);

            if (element) {
                element.value = fieldValue(item.fields, step[1]);
                fireChange(element);
            }
        });

        // Then fill every enabled field in page order. Some names appear
        // more than once (e.g. 'assists'), so values are matched by position.
        var queues = {};

        item.fields.forEach(function (field) {
            (queues[field[0]] = queues[field[0]] || []).push(field[1]);
        });

        Array.prototype.forEach.call(form.elements, function (element) {
            if (!element.name || element.disabled || element.name === 'csrf_token') {
                return;
            }

            if (element.type === 'submit' || element.type === 'button') {
                return;
            }

            var queue = queues[element.name];

            if (queue && queue.length) {
                setFieldValue(element, queue.shift());
            }
        });

        editingRecordId = item.id;

        showFormAlert(
            'info',
            'Your saved record is in the form below. Check the details and press ' +
            'Submit Record. The saved copy stays on this device until you do.'
        );
    }

    function resetForm(form) {
        form.reset();
        fireChange(document.getElementById('sportSelect'));
        fireChange(document.getElementById('competition'));
    }

    // Once the edited version is safely submitted or saved, drop the old copy.
    function finishEditing() {
        if (!editingRecordId) {
            return Promise.resolve();
        }

        var id = editingRecordId;
        editingRecordId = null;

        return deleteRecord(id).catch(function () {});
    }

    // ==========================================================
    // SUBMIT: TRY ONLINE FIRST, SAVE ON DEVICE IF THAT FAILS
    // ==========================================================

    function saveOnDevice(form, fields, reason, needsLogin) {
        var item = {
            id: makeId(),
            userId: userId,
            fields: fields,
            summary: summarize(fields),
            savedAt: Date.now(),
            status: 'pending',
            error: null
        };

        return putRecord(item)
            .then(finishEditing)
            .then(function () {
                resetForm(form);
                lastSyncMessage = '';

                showFormAlert(
                    'success',
                    reason + ' Your record is saved on this device and will be uploaded ' +
                    'for approval ' +
                    (needsLogin ? 'after you log in again.' : 'when you are back online.')
                );

                return renderPanel();
            })
            .catch(function () {
                showFormAlert(
                    'danger',
                    'Your record could not be saved on this device. ' +
                    'Please write the details down and try again when you are online.'
                );
            });
    }

    function setUpForm() {
        var form = document.querySelector('form[data-dart-offline-form]');

        if (!form) {
            return;
        }

        var submitButton = document.getElementById('recordSubmitBtn');
        var busy = false;

        // The browser has already run its built-in "required" checks
        // before this event fires.
        form.addEventListener('submit', function (event) {
            event.preventDefault();

            if (busy) {
                return;
            }

            busy = true;
            setButtonBusy(submitButton, true);

            var fields = collectFields(form);

            getSyncStatus()
                .then(function (status) {
                    if (!status) {
                        return saveOnDevice(form, fields, 'You appear to be offline.', false);
                    }

                    if (!status.authenticated || status.user_id !== userId) {
                        return saveOnDevice(form, fields, 'Your session has expired.', true);
                    }

                    if (!status.can_submit_records) {
                        showFormAlert('danger', 'You are not allowed to submit records.');
                        return null;
                    }

                    return uploadFields(fields, status.csrf_token, 'online').then(function (result) {
                        if (result.outcome === 'created') {
                            return finishEditing().then(function () {
                                // The server has set the usual success message.
                                window.location.assign(form.dataset.successUrl || '/student');
                                return 'leaving';
                            });
                        }

                        if (result.outcome === 'unreachable') {
                            return saveOnDevice(form, fields, 'The connection dropped.', false);
                        }

                        if (result.outcome === 'login_required') {
                            return saveOnDevice(form, fields, 'Your session has expired.', true);
                        }

                        // Validation problem (invalid, duplicate, not allowed):
                        // keep the form filled in so the athlete can fix it.
                        showFormAlert('danger', result.error || 'This record could not be submitted.');
                        return null;
                    });
                })
                .catch(function () {
                    showFormAlert('danger', 'Something went wrong. Please try again.');
                    return null;
                })
                .then(function (state) {
                    if (state !== 'leaving') {
                        busy = false;
                        setButtonBusy(submitButton, false);
                    }
                });
        });
    }

    // ==========================================================
    // SYNC SAVED RECORDS
    // ==========================================================

    var syncing = false;

    function runSync(options) {
        var result = {
            uploaded: 0,
            alreadyUploaded: 0,
            needsAttention: 0,
            stoppedReason: null
        };

        syncing = true;

        return getMyRecords()
            .then(function (items) {
                var toUpload = items.filter(function (item) {
                    return item.status === 'pending' || options.includeNeedsAttention;
                });

                if (!toUpload.length) {
                    return result;
                }

                return getSyncStatus().then(function (status) {
                    if (!status) {
                        result.stoppedReason = 'offline';
                        return result;
                    }

                    if (!status.authenticated || status.user_id !== userId) {
                        result.stoppedReason = 'login';
                        return result;
                    }

                    if (!status.can_submit_records) {
                        result.stoppedReason = 'not_allowed';
                        return result;
                    }

                    // One at a time, oldest first. Stop at the first
                    // connection/login problem; everything left stays saved.
                    return toUpload.reduce(function (chain, item) {
                        return chain.then(function (stopped) {
                            if (stopped) {
                                return true;
                            }

                            return uploadFields(item.fields, status.csrf_token, 'sync')
                                .then(function (upload) {
                                    switch (upload.outcome) {
                                        case 'created':
                                            result.uploaded++;
                                            return deleteRecord(item.id).then(function () { return false; });

                                        case 'duplicate':
                                            // Already on the server (e.g. the connection dropped
                                            // after an earlier upload succeeded).
                                            result.alreadyUploaded++;
                                            return deleteRecord(item.id).then(function () { return false; });

                                        case 'invalid':
                                            result.needsAttention++;
                                            item.status = 'needs_attention';
                                            item.error = upload.error;
                                            return putRecord(item).then(function () { return false; });

                                        case 'login_required':
                                            result.stoppedReason = 'login';
                                            return true;

                                        case 'not_allowed':
                                            result.stoppedReason = 'not_allowed';
                                            return true;

                                        default:
                                            result.stoppedReason = 'offline';
                                            return true;
                                    }
                                });
                        });
                    }, Promise.resolve(false)).then(function () {
                        return result;
                    });
                });
            })
            .catch(function () {
                result.stoppedReason = 'error';
                return result;
            })
            .then(function (finalResult) {
                syncing = false;
                return finalResult;
            });
    }

    function describeSync(result) {
        var parts = [];

        if (result.uploaded) {
            parts.push(
                plural(result.uploaded, 'saved record') +
                ' uploaded for approval. Refresh the page to see ' +
                (result.uploaded === 1 ? 'it' : 'them') + ' in My Submitted Records.'
            );
        }

        if (result.alreadyUploaded) {
            parts.push(
                plural(result.alreadyUploaded, 'saved record') +
                (result.alreadyUploaded === 1 ? ' was' : ' were') +
                ' already uploaded, so the saved copy was removed.'
            );
        }

        if (result.needsAttention) {
            parts.push(
                plural(result.needsAttention, 'record') +
                ' need attention. Edit and submit again, or delete.'
            );
        }

        if (result.stoppedReason === 'offline') {
            parts.push('You are offline. Saved records will upload automatically when you are back online.');
        } else if (result.stoppedReason === 'login') {
            parts.push('Log in again to upload your saved records.');
        } else if (result.stoppedReason === 'not_allowed') {
            parts.push('Your account cannot submit records right now.');
        } else if (result.stoppedReason === 'error') {
            parts.push('Saved records could not be uploaded. They will be tried again later.');
        }

        return parts.join(' ');
    }

    function syncSavedRecords(options) {
        options = options || {};

        if (syncing) {
            return Promise.resolve();
        }

        var run = function () {
            return runSync(options).then(function (result) {
                lastSyncMessage = describeSync(result);

                var panel = document.getElementById('offlineRecordsPanel');

                if (!panel && result.uploaded) {
                    showToast(plural(result.uploaded, 'saved record') + ' uploaded for approval.');
                }

                return renderPanel();
            });
        };

        // Only one tab syncs at a time (prevents double uploads).
        if (navigator.locks && typeof navigator.locks.request === 'function') {
            return navigator.locks
                .request('dart-record-sync', { ifAvailable: true }, function (lock) {
                    return lock ? run() : null;
                })
                .catch(function () {});
        }

        return run();
    }

    // ==========================================================
    // START
    // ==========================================================

    function start() {
        setUpForm();

        var syncButton = document.getElementById('offlineSyncBtn');

        if (syncButton) {
            syncButton.addEventListener('click', function () {
                setButtonBusy(syncButton, true, 'Uploading…');

                syncSavedRecords({ includeNeedsAttention: true }).then(function () {
                    setButtonBusy(syncButton, false);
                });
            });
        }

        renderPanel().then(function () {
            return syncSavedRecords();
        });

        window.addEventListener('online', function () {
            syncSavedRecords();
        });

        var lastFocusSync = Date.now();

        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState !== 'visible') {
                return;
            }

            if (Date.now() - lastFocusSync < FOCUS_SYNC_INTERVAL_MS) {
                return;
            }

            lastFocusSync = Date.now();
            syncSavedRecords();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
