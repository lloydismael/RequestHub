const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'notification-toasts.js'), 'utf8');

function start({ initial = 10, stored = null, storageUnavailable = false, notificationPage = false, immediatePagePoll = false } = {}) {
    const config = { dataset: { url: '/dashboard/toasts/', userId: '1', initialCursor: String(initial) } };
    const region = {
        children: [],
        appendChild(child) { this.children.push(child); child.parentNode = this; },
        removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; },
    };
    let value = stored;
    const requests = [];
    const responses = [];
    const listeners = {};
    function element() {
        const classes = new Set();
        return {
            dataset: {}, children: [], listeners: {}, attributes: {},
            classList: {
                add(name) { classes.add(name); },
                remove(name) { classes.delete(name); },
                contains(name) { return classes.has(name); },
            },
            appendChild(child) { this.children.push(child); child.parentNode = this; },
            removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
            setAttribute(name, value) { this.attributes[name] = value; },
            querySelector(selector) { return this.children.find(child => child.attributes[selector.slice(1, -1)] !== undefined) || null; },
            addEventListener(name, callback) { this.listeners[name] = callback; },
        };
    }
    const navLink = element();
    let page = notificationPage ? { content: 'old notifications', contains() { return false; }, replaceWith(next) { page = next; } } : null;
    const document = {
        hidden: false,
        activeElement: null,
        querySelector(selector) {
            if (selector.includes('config')) return config;
            if (selector.includes('toast-region')) return region;
            if (selector === '[data-notification-link]') return navLink;
            if (selector === '[data-notification-page]') return page;
            return null;
        },
        addEventListener(name, callback) { listeners[name] = callback; },
        createElement: element,
    };
    const window = {
        localStorage: {
            getItem() { if (storageUnavailable) throw new Error('Storage unavailable'); return value; },
            setItem(key, next) { if (storageUnavailable) throw new Error('Storage unavailable'); value = next; },
        },
        location: { origin: 'http://localhost', href: 'http://localhost/notifications/?category=mention&filter=unread' },
                setTimeout(callback, delay) { if (immediatePagePoll && delay === 0) callback(); return 0; },
                clearTimeout() {},
                setInterval() {},
                addEventListener() {},
            };
    vm.runInNewContext(source, {
        document, window, URL, Set,
        DOMParser: class {
            parseFromString(content) {
                return { querySelector: () => ({ content, contains() { return false; }, replaceWith(next) { page = next; } }) };
            }
        },
        fetch(url) {
            requests.push(new URL(url));
            if (new URL(url).pathname === '/notifications/') {
                return Promise.resolve({ ok: true, text: () => Promise.resolve('fresh notifications') });
            }
            return Promise.resolve({ ok: true, json: () => Promise.resolve(responses.shift() || { ok: true, events: [] }) });
        },
    });
    return {
        region, requests, navLink,
        get page() { return page; },
        async poll(payload) {
            await new Promise(setImmediate);
            responses.push(payload);
            listeners.visibilitychange();
            await new Promise(setImmediate);
        },
    };
}

test('stale saved cursor is advanced to the page baseline', () => {
    const app = start({ initial: 100, stored: '5' });
    assert.equal(app.requests[0].searchParams.get('cursor'), '100');
});

test('a cursor advanced in another tab is never lowered', () => {
    const app = start({ initial: 100, stored: '110' });
    assert.equal(app.requests[0].searchParams.get('cursor'), '110');
});

test('missing or unavailable storage still starts at the page baseline', () => {
    for (const storageUnavailable of [false, true]) {
        const app = start({ initial: 100, storageUnavailable });
        assert.equal(app.requests[0].searchParams.get('cursor'), '100');
    }
});

test('only new events toast, with no replay after dismissal or repeat polling', async () => {
    const app = start({ initial: 10, stored: '1' });
    const event = { id: 11, type: 'new_request', reference_code: 'REQ-00011', message: 'New request' };
    await app.poll({ ok: true, events: [{ id: 5 }, event, event], next_cursor: 11 });
    assert.equal(app.region.children.length, 1);
    assert.equal(app.region.children[0].dataset.notificationId, '11');
    const toast = app.region.children[0];
        toast.children[0].children[1].listeners.click();
        assert.equal(toast.classList.contains('notification-toast--leaving'), true);
        app.region.removeChild(toast);
        assert.equal(app.region.children.length, 0);
        await app.poll({ ok: true, events: [event], next_cursor: 11 });
    assert.equal(app.region.children.length, 0);
    assert.equal(app.requests.at(-1).searchParams.get('cursor'), '11');
});

test('mention events display a distinct attention toast', async () => {
    const app = start({ initial: 10 });
    await app.poll({ ok: true, events: [{ id: 11, type: 'mention', reference_code: 'REQ-00011', message: 'You were mentioned' }], next_cursor: 11 });
    assert.equal(app.region.children.length, 1);
    assert.equal(app.region.children[0].children[0].children[0].textContent, 'You were mentioned');
});

test('authoritative mention counts update the badge and clear it after reading', async () => {
    const app = start();
    await app.poll({ ok: true, events: [{ id: 11, type: 'mention' }], next_cursor: 11, unread_count: 2, total_count: 2, latest_id: 11 });
    assert.equal(app.navLink.classList.contains('nav-link-with-dot'), true);
    const badge = app.navLink.querySelector('[data-notification-badge]');
    assert.ok(badge);
    assert.equal(badge.children[0].textContent, '2');
    await app.poll({ ok: true, events: [], next_cursor: 11, unread_count: 0, total_count: 2, latest_id: 11 });
    assert.equal(app.navLink.classList.contains('nav-link-with-dot'), false);
    assert.equal(app.navLink.querySelector('[data-notification-badge]'), null);
});

test('notification page refreshes without losing category or read filters', async () => {
    const app = start({ notificationPage: true });
    const state = { ok: true, events: [{ id: 11, type: 'mention' }], next_cursor: 11, unread_count: 1, total_count: 1, latest_id: 11 };
    await app.poll(state);
    assert.equal(app.page.content, 'fresh notifications');
    const refreshes = app.requests.filter(url => url.pathname === '/notifications/');
    assert.equal(refreshes.length, 1);
    assert.equal(refreshes[0].searchParams.get('category'), 'mention');
    assert.equal(refreshes[0].searchParams.get('filter'), 'unread');
    await app.poll(state);
    assert.equal(app.requests.filter(url => url.pathname === '/notifications/').length, 1);
});

test('more toast events are polled after the notification page refresh completes', async () => {
    const app = start({ notificationPage: true, immediatePagePoll: true });
    await app.poll({ ok: true, events: [{ id: 11, type: 'mention' }], next_cursor: 11, has_more: true, unread_count: 1, total_count: 1, latest_id: 11 });
    assert.equal(app.requests.filter(url => url.pathname === '/dashboard/toasts/').length, 3);
});

test('memory cursor advances when local storage is unavailable', async () => {
    const app = start({ initial: 10, storageUnavailable: true });
    await app.poll({ ok: true, events: [{ id: 11 }], next_cursor: 11 });
    await app.poll({ ok: true, events: [{ id: 11 }], next_cursor: 11 });
    assert.equal(app.region.children.length, 1);
    assert.equal(app.requests.at(-1).searchParams.get('cursor'), '11');
});
