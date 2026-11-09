const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function idlePage(enabled = true) {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'base.html'), 'utf8');
    const script = [...template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)]
        .map(match => match[1]).find(source => source.includes('var IDLE_MS'));
    let now = 0;
    let nextId = 0;
    let submissions = 0;
    const timers = new Map();
    const events = {};
    const storageEvents = {};
    const writes = [];
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
    const elements = Object.fromEntries(['aloOverlay', 'aloCountdown', 'aloStayBtn', 'alo-logout-form']
        .map(id => [id, element()]));
    elements['alo-logout-form'].submit = () => { submissions++; };
    const schedule = (callback, delay, repeat = false) => {
        const id = ++nextId;
        timers.set(id, {callback, at: now + delay, repeat: repeat ? delay : 0});
        return id;
    };
    vm.runInNewContext(script
        .replace("{{ request.user.idle_timeout_enabled|yesno:'true,false' }}", String(enabled))
        .replace('{{ request.user.pk }}', '42'), {
        document: {
            getElementById: id => elements[id],
            addEventListener: (name, callback) => { events[name] = callback; },
        },
        window: {addEventListener: (name, callback) => { storageEvents[name] = callback; }},
        localStorage: {setItem: (key, value) => writes.push({key, value})},
        console,
        setTimeout: (callback, delay) => schedule(callback, delay),
        setInterval: (callback, delay) => schedule(callback, delay, true),
        clearTimeout: id => timers.delete(id),
        clearInterval: id => timers.delete(id),
        requestAnimationFrame: callback => schedule(callback, 0),
    });
    return {
        elements, events, storageEvents, writes,
        submissions: () => submissions,
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

test('enabled timeout warns at five minutes and logs out after the one-minute warning', () => {
    const app = idlePage();
    app.advance(299999);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    app.advance(1);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), true);
    app.advance(60000);
    assert.equal(app.submissions(), 1);
});

test('saving disabled preference cancels a running warning and logout', () => {
    const app = idlePage();
    app.advance(300000);
    app.events['requesthub:idle-timeout-changed']({detail: {enabled: false}});
    app.advance(600000);
    assert.equal(app.submissions(), 0);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    assert.deepEqual(app.writes, [{key: 'reqHubIdleTimeout:42', value: 'false'}]);
});

test('another tab disabling timeout cancels even an already queued warning callback', () => {
    const app = idlePage();
    app.events.mousemove();
    app.storageEvents.storage({key: 'reqHubIdleTimeout:42', newValue: 'false'});
    app.advance(600000);
    assert.equal(app.submissions(), 0);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
});

test('a tab loaded with timeout disabled starts timing when another tab enables it', () => {
    const app = idlePage(false);
    app.advance(600000);
    assert.equal(app.submissions(), 0);
    app.storageEvents.storage({key: 'reqHubIdleTimeout:42', newValue: 'true'});
    app.advance(360000);
    assert.equal(app.submissions(), 1);
});

test('other accounts storage events do not change the timeout', () => {
    const app = idlePage();
    app.storageEvents.storage({key: 'reqHubIdleTimeout:99', newValue: 'false'});
    app.advance(360000);
    assert.equal(app.submissions(), 1);
});

test('discrete activity resets the five-minute idle period', () => {
    const app = idlePage();
    app.advance(299999);
    app.events.click();
    app.advance(299999);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), false);
    app.advance(1);
    assert.equal(app.elements.aloOverlay.classList.contains('is-open'), true);
});

for (const succeeds of [true, false]) {
    test(`settings save ${succeeds ? 'broadcasts the saved preference' : 'does not broadcast on failure'}`, async () => {
        const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'accounts', 'profile_form.html'), 'utf8');
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
