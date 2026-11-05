(function () {
    'use strict';

    var config = document.querySelector('[data-notification-toast-config]');
    var region = document.querySelector('[data-notification-toast-region]');
    if (!config || !region) return;

    var url = config.dataset.url;
    var userId = config.dataset.userId;
    if (!url || !userId) return;

    var cursorKey = 'reqHubToastCursor:v1:' + userId;
    var channelName = 'reqHubToasts:v1:' + userId;
    var intervalMs = 10000;
    var inFlight = false;
    var morePending = false;
    var queued = new Set();
    var channel = null;

    // Never replay the unread backlog from before this page was opened.
    writeCursor(config.dataset.initialCursor, false);

    function readCursor() {
        try {
            var value = window.localStorage.getItem(cursorKey);
            return value === null
                ? parseInt(config.dataset.memoryCursor, 10) || 0
                : Math.max(parseInt(value, 10) || 0, 0);
        } catch (_) {
            return config.dataset.memoryCursor ? parseInt(config.dataset.memoryCursor, 10) || 0 : null;
        }
    }

    function writeCursor(value, broadcast) {
        var cursor = Math.max(readCursor() || 0, parseInt(value, 10) || 0, 0);
        config.dataset.memoryCursor = String(cursor);
        try { window.localStorage.setItem(cursorKey, String(cursor)); } catch (_) {}
        if (broadcast && channel) channel.postMessage({ cursor: cursor });
    }

    var TOAST_LIFETIME_MS = 12000;
    var TOAST_EXIT_MS = 320;

    function removeToast(toast) {
        if (toast && toast.parentNode) toast.parentNode.removeChild(toast);
    }

    function dismissToast(toast) {
        if (!toast || !toast.parentNode) return;
        if (toast.classList && toast.classList.contains('notification-toast--leaving')) return;
        if (toast.classList) toast.classList.add('notification-toast--leaving');
        window.setTimeout(function () { removeToast(toast); }, TOAST_EXIT_MS);
    }

    function scheduleDismiss(toast) {
        var remaining = TOAST_LIFETIME_MS;
        var startedAt = Date.now();
        var timer = null;

        function start() {
            startedAt = Date.now();
            timer = window.setTimeout(function () { dismissToast(toast); }, remaining);
        }

        function pause() {
            if (timer === null) return;
            window.clearTimeout(timer);
            timer = null;
            remaining = Math.max(remaining - (Date.now() - startedAt), 0);
        }

        toast.addEventListener('mouseenter', pause);
        toast.addEventListener('focusin', pause);
        toast.addEventListener('mouseleave', start);
        toast.addEventListener('focusout', start);
        start();
    }

    function createElement(tag, className, text) {
        var element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function showToast(event) {
        var id = Number(event.id);
        if (!id || queued.has(id)) return;
        if ((readCursor() || 0) >= id) return;
        queued.add(id);

        var toast = createElement('article', 'notification-toast');
        toast.setAttribute('role', 'status');
        toast.dataset.notificationId = String(id);

        var header = createElement('div', 'notification-toast__header');
        var title = createElement(
            'h2',
            'notification-toast__title',
            event.type === 'mention' ? 'You were mentioned' : (event.type === 'assignment' ? 'New assignment' : 'New incoming request')
        );
        var close = createElement('button', 'notification-toast__close', '×');
        close.type = 'button';
        close.setAttribute('aria-label', 'Dismiss notification');
        close.addEventListener('click', function () { dismissToast(toast); });
        header.appendChild(title);
        header.appendChild(close);

        var ticket = createElement('strong', 'notification-toast__ticket', event.reference_code || 'Request');
        var message = createElement('p', 'notification-toast__message', event.message || 'A request needs your attention.');
        var actions = createElement('div', 'notification-toast__actions');
        var link = createElement('a', 'btn btn-sm btn-primary', 'Open Request');
        link.href = event.manage_url || '#';
        actions.appendChild(link);

        toast.appendChild(header);
        toast.appendChild(ticket);
        toast.appendChild(message);
        toast.appendChild(actions);
        region.appendChild(toast);
        scheduleDismiss(toast);
    }

    var lastPageState = null;

    function updateNotificationCount(count) {
        if (!Number.isInteger(count) || count < 0) return;
        var link = document.querySelector('[data-notification-link]');
        if (!link) return;
        var badge = link.querySelector('[data-notification-badge]');
        if (count === 0) {
            if (badge) link.removeChild(badge);
            link.classList.remove('nav-link-with-dot');
            return;
        }
        link.classList.add('nav-link-with-dot');
        if (!badge) {
            badge = createElement('span', 'notification-dot notification-dot--nav');
            badge.setAttribute('data-notification-badge', '');
            badge.setAttribute('role', 'status');
            var value = createElement('span', 'notification-dot__count');
            value.setAttribute('data-notification-count', '');
            badge.appendChild(value);
            link.appendChild(badge);
        }
        badge.setAttribute('title', 'Unread notifications');
        badge.setAttribute('aria-label', count + ' new items');
        badge.querySelector('[data-notification-count]').textContent = String(count);
    }

    function refreshNotificationPage(data) {
        var page = document.querySelector('[data-notification-page]');
        if (!page || !Number.isInteger(data.unread_count)) return;
        var state = [data.latest_id, data.total_count, data.unread_count].join(':');
        if (state === lastPageState) return;
        // Do not replace a notification action while someone is using the keyboard.
        if (document.activeElement && page.contains(document.activeElement)) return;
        return fetch(window.location.href, {
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { 'Accept': 'text/html', 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (response) {
            if (!response.ok) throw new Error('notification page refresh failed');
            return response.text();
        }).then(function (html) {
            var replacement = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-notification-page]');
            if (replacement && document.querySelector('[data-notification-page]') === page) {
                page.replaceWith(replacement);
                lastPageState = state;
            }
        });
    }

    function processPayload(data) {
        if (!data || !data.ok) return;
        var events = Array.isArray(data.events) ? data.events : [];
        events.forEach(showToast);
        updateNotificationCount(data.unread_count);
        if (data.next_cursor !== undefined) writeCursor(data.next_cursor, true);
        morePending = Boolean(data.has_more);
        return refreshNotificationPage(data);
    }

    function poll() {
        if (inFlight || document.hidden) return;
        inFlight = true;
        var cursor = readCursor();
        var requestUrl = new URL(url, window.location.origin);
        if (cursor !== null) requestUrl.searchParams.set('cursor', String(cursor));
        fetch(requestUrl.toString(), {
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (response) {
                if (!response.ok) throw new Error('toast polling failed');
                return response.json();
            })
            .then(processPayload)
            .catch(function () {})
            .finally(function () {
                inFlight = false;
                if (morePending) {
                    morePending = false;
                    window.setTimeout(poll, 0);
                }
            });
    }

    if ('BroadcastChannel' in window) {
        channel = new BroadcastChannel(channelName);
        channel.addEventListener('message', function (message) {
            if (message.data && message.data.cursor !== undefined) writeCursor(message.data.cursor, false);
        });
    }
    window.addEventListener('storage', function (event) {
        if (event.key === cursorKey && event.newValue !== null) writeCursor(event.newValue, false);
    });
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) poll();
    });

    poll();
    window.setInterval(poll, intervalMs);
}());
