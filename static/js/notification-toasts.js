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

    function removeToast(toast) {
        if (toast && toast.parentNode) toast.parentNode.removeChild(toast);
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
            event.type === 'assignment' ? 'New assignment' : 'New incoming request'
        );
        var close = createElement('button', 'notification-toast__close', '×');
        close.type = 'button';
        close.setAttribute('aria-label', 'Dismiss notification');
        close.addEventListener('click', function () { removeToast(toast); });
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
        window.setTimeout(function () { removeToast(toast); }, 12000);
    }

    function processPayload(data) {
        if (!data || !data.ok) return;
        var events = Array.isArray(data.events) ? data.events : [];
        events.forEach(showToast);
        if (data.next_cursor !== undefined) writeCursor(data.next_cursor, true);
        if (data.has_more) window.setTimeout(poll, 0);
    }

    function poll() {
        if (inFlight || document.hidden) return;
        inFlight = true;
        var cursor = readCursor();
        var requestUrl = new URL(url, window.location.origin);
        if (cursor !== null) requestUrl.searchParams.set('cursor', String(cursor));
        fetch(requestUrl.toString(), {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (response) {
                if (!response.ok) throw new Error('toast polling failed');
                return response.json();
            })
            .then(processPayload)
            .catch(function () {})
            .finally(function () { inFlight = false; });
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
