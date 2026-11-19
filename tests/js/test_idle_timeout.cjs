const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function idlePage(enabled = true, checkResponse = {status: 401}) {
    const script = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'idle-timeout.js'), 'utf8');
    let now = 0;
    let nextId = 0;
    const timers = new Map();
    const events = {};
    const storageEvents = {};
    const writes = [];
    const heartbeats = [];
    const navigations = [];
    let checkCount = 0;
    function element() {
        const classes = new Set();
        return {
            textContent: '',
            classList: {
                add: name => classes.add(name),
                remove: name => classes.delete(name),
                contains: name => classes.has(name),
            },
            addEventListener: () => {},
        };
    }
    const elements = Object.fromEntries(['aloOverlay', 'aloCountdown', 'aloStayBtn']
        .map(id => [id, element()]));
    elements.aloOverlay.dataset = {
        enabled: String(enabled), user: '42', remaining: '', csrfToken: 'fixture-token',
        heartbeatUrl: '/accounts/sign-in-heartbeat/', logoutUrl: '/accounts/logout/', loginUrl: '/',
    };
    const schedule = (callback, delay, repeat = false) => {
        const id = ++nextId;
        timers.set(id, {callback, at: now + delay, repeat: repeat ? delay : 0});
        return id;
    };
    vm.runInNewContext(script, {
        document: {
            getElementById: id => elements[id],
            addEventListener: (name, callback) => { events[name] = callback; },
        },
        window: {
            addEventListener: (name, callback) => { storageEvents[name] = callback; },
            location: {assign: url => navigations.push(url)},
        },
        Date: {now: () => now},
        fetch: async (url, options) => {
            if (url.includes('sign-in-heartbeat')) {
                heartbeats.push(options.headers);
                return {status: 204, headers: {get: () => null}};
            }
            assert.equal(url, '/accounts/logout/');
            assert.equal(options.body, 'reason=idle');
            assert.equal(options.headers['X-Request-Hub-Activity'], undefined);
            const index = checkCount++;
            const {status, remaining} = Array.isArray(checkResponse)
                ? checkResponse[Math.min(index, checkResponse.length - 1)] : checkResponse;
            return {status, headers: {get: () => remaining ?? null}};
        },
        localStorage: {setItem: (key, value) => writes.push({key, value})},
        console,
        setTimeout: (callback, delay) => schedule(callback, delay),
        setInterval: (callback, delay) => schedule(callback, delay, true),
        clearTimeout: id => timers.delete(id),
        clearInterval: id => timers.delete(id),
        requestAnimationFrame: callback => schedule(callback, 0),
    });
    return {
        elements, events, storageEvents, writes, heartbeats, navigations,
        checks: () => checkCount,
        advance(ms) {
            const end = now + ms;
            while (true) {
                const next = [...timers.entries()].filter(([, timer]) => timer.at <= end)
                    .sort((a, b) => a[1].at - b[1].at)[0];
                if (!next) break;
                const [id, timer] = next;
                now = timer.at;
                if (timer.repeat) timer.at += timer.repeat;
                else timers.delete(id);
                timer.callback();
            }
            now = end;
        },
    };
}

test('enabled timeout warns at five minutes and logs out after the server confirms expiry', async () => {
    const app = idlePage();
    app.advance(299999);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    app.advance(1);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), true);
    app.advance(60000);
    assert.equal(app.checks(), 1);
    await Promise.resolve();
    assert.deepEqual(app.navigations, ['/']);
});

test('saving disabled preference cancels a running warning and logout', () => {
    const app = idlePage();
    app.advance(300000);
    app.events['requesthub:idle-timeout-changed']({detail: {enabled: false}});
    app.advance(600000);
    assert.equal(app.checks(), 0);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    assert.deepEqual(app.writes, [{key: 'reqHubIdleTimeout:42', value: 'false'}]);
});

test('another tab disabling timeout cancels a pending warning', () => {
    const app = idlePage();
    app.events.mousemove({isTrusted: true});
    app.storageEvents.storage({key: 'reqHubIdleTimeout:42', newValue: 'false'});
    app.advance(600000);
    assert.equal(app.checks(), 0);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
});

test('a tab loaded with timeout disabled starts timing when another tab enables it', () => {
    const app = idlePage(false);
    app.advance(600000);
    assert.equal(app.checks(), 0);
    app.storageEvents.storage({key: 'reqHubIdleTimeout:42', newValue: 'true'});
    app.advance(360000);
    assert.equal(app.checks(), 1);
});

test('other accounts storage events do not change the timeout', () => {
    const app = idlePage();
    app.storageEvents.storage({key: 'reqHubIdleTimeout:99', newValue: 'false'});
    app.advance(360000);
    assert.equal(app.checks(), 1);
});

test('a stale deadline checks without navigating and uses the backend deadline', async () => {
    const app = idlePage(true, [{status: 204, remaining: '300000'}, {status: 401}]);
    app.advance(360000);
    await Promise.resolve();
    assert.equal(app.checks(), 1);
    assert.deepEqual(app.navigations, []);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    app.advance(239999);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    app.advance(1);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), true);
    app.advance(60000);
    await Promise.resolve();
    assert.equal(app.navigations.length, 1);
});

test('a server-side opt-out clears a stale warning without leaving the page', async () => {
    const app = idlePage(true, {status: 204});
    app.advance(360000);
    await Promise.resolve();
    app.advance(360000);
    assert.equal(app.checks(), 1);
    assert.deepEqual(app.navigations, []);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
});

test('only deliberate activity and warning confirmation request an idle renewal', () => {
    const app = idlePage();
    assert.equal(app.heartbeats[0]?.['X-Request-Hub-Activity'], undefined);
    app.advance(300000);
    app.events.mousemove({isTrusted: true});
    assert.equal(app.heartbeats.length, 1);
    app.events.keydown({key: 'Escape', isTrusted: true});
    assert.equal(app.heartbeats[1]?.['X-Request-Hub-Activity'], 'interactive');
});

for (const succeeds of [true, false]) {
    test(`settings save ${succeeds ? 'broadcasts the saved preference' : 'does not broadcast on failure'}`, async () => {
        const template = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'accounts', 'profile_form.html'), 'utf8');
        const script = template.slice(template.indexOf('// Settings changes are staged'),
            template.indexOf('// \u2500\u2500 Profile page: inline view-mode toggle'));
        const listeners = {};
        const dispatched = [];
        const input = {
            checked: true, disabled: false,
            addEventListener: (name, callback) => { listeners[name] = callback; },
        };
        const controls = Object.fromEntries(['settings-save-btn', 'settings-discard-btn',
            'settings-save-status', 'settings-themes-btn'].map(id => [id, {
            addEventListener: () => {},
            classList: {add: () => {}, remove: () => {}},
        }]));
        const form = {
            action: '/accounts/profile/',
            querySelectorAll: () => [input],
            addEventListener: (name, callback) => { listeners[name] = callback; },
            setAttribute: () => {},
            removeAttribute: () => {},
        };
        controls['profile-settings-form'] = form;
        const location = {hash: '', reload: () => {}};
        vm.runInNewContext(script, {
            document: {
                getElementById: id => controls[id],
                dispatchEvent: event => dispatched.push(event),
            },
            window: {addEventListener: () => {}, location},
            FormData: class {},
            CustomEvent: class {
                constructor(type, options) { this.type = type; this.detail = options.detail; }
            },
            fetch: async () => ({
                ok: succeeds,
                headers: {get: () => 'application/json'},
                json: async () => ({message: 'Settings saved.', idle_timeout_enabled: false}),
            }),
        });
        input.checked = false;
        await listeners.submit({preventDefault: () => {}});
        assert.equal(dispatched.length, succeeds ? 1 : 0);
        if (succeeds) {
            assert.equal(dispatched[0].type, 'requesthub:idle-timeout-changed');
            assert.equal(dispatched[0].detail.enabled, false);
        } else {
            assert.equal(input.disabled, false);
            assert.equal(controls['settings-save-status'].textContent,
                'Could not save changes. Your selections are kept; try again.');
        }
    });
}
