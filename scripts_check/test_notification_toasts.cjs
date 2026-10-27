const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'notification-toasts.js'), 'utf8');

function start({ initial = 10, stored = null, storageUnavailable = false } = {}) {
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
    const document = {
        hidden: false,
        querySelector(selector) { return selector.includes('config') ? config : region; },
        addEventListener(name, callback) { listeners[name] = callback; },
        createElement() {
            return {
                dataset: {}, children: [], listeners: {},
                appendChild(child) { this.children.push(child); },
                setAttribute() {},
                addEventListener(name, callback) { this.listeners[name] = callback; },
            };
        },
    };
    const window = {
        localStorage: {
            getItem() { if (storageUnavailable) throw new Error('Storage unavailable'); return value; },
            setItem(key, next) { if (storageUnavailable) throw new Error('Storage unavailable'); value = next; },
        },
        location: { origin: 'http://localhost' },
        setTimeout() {}, setInterval() {}, addEventListener() {},
    };
    vm.runInNewContext(source, {
        document, window, URL, Set,
        fetch(url) {
            requests.push(new URL(url));
            return Promise.resolve({ ok: true, json: () => Promise.resolve(responses.shift() || { ok: true, events: [] }) });
        },
    });
    return {
        region, requests,
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
    app.region.children[0].children[0].children[1].listeners.click();
    assert.equal(app.region.children.length, 0);
    await app.poll({ ok: true, events: [event], next_cursor: 11 });
    assert.equal(app.region.children.length, 0);
    assert.equal(app.requests.at(-1).searchParams.get('cursor'), '11');
});

test('memory cursor advances when local storage is unavailable', async () => {
    const app = start({ initial: 10, storageUnavailable: true });
    await app.poll({ ok: true, events: [{ id: 11 }], next_cursor: 11 });
    await app.poll({ ok: true, events: [{ id: 11 }], next_cursor: 11 });
    assert.equal(app.region.children.length, 1);
    assert.equal(app.requests.at(-1).searchParams.get('cursor'), '11');
});
