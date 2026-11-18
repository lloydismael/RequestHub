const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function load(checked = true) {
    const file = path.join(__dirname, '..', 'static', 'js', 'sqr-classic-visibility.js');
    const checkbox = {checked, disabled: false, events: {}, addEventListener(name, callback) { this.events[name] = callback; }};
    const status = {textContent: ''};
    Object.defineProperty(status, 'innerHTML', {set() { assert.fail('Feedback must not parse HTML'); }});
    const root = {dataset: {settingsUrl: '/sqr/tracker-settings/'},
        setAttribute() {}, removeAttribute() {},
        querySelector(selector) { return selector === 'input[name="csrfmiddlewaretoken"]' ? {value: 'fixture-token'} : null; }};
    let finish;
    let request;
    const response = new Promise(resolve => { finish = resolve; });
    vm.runInNewContext(fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '', {
        document: {getElementById(id) { return {'sqr-classic-setting': root, 'sqr-classic-visibility': checkbox, 'sqr-classic-status': status}[id]; }},
        fetch(url, options) { request = {url, options}; return response; },
        AbortController, setTimeout, clearTimeout,
    });
    return {checkbox, status,
        change(value) {
            assert.equal(typeof checkbox.events.change, 'function', 'Admin visibility control must be wired');
            checkbox.checked = value;
            return checkbox.events.change();
        },
        finish(payload, ok = true) { finish({ok, async json() { return payload; }}); },
        request() { return request; },
    };
}

test('Admin visibility saves immediately with CSRF and disables duplicate changes while pending', async () => {
    const app = load();
    const pending = app.change(false);
    assert.equal(app.checkbox.disabled, true);
    assert.equal(app.request().url, '/sqr/tracker-settings/');
    assert.equal(app.request().options.method, 'POST');
    assert.equal(app.request().options.headers['X-CSRFToken'], 'fixture-token');
    assert.deepEqual(JSON.parse(app.request().options.body), {show_classic_sqr_tracker: false});
    app.finish({ok: true, show_classic_sqr_tracker: false});
    await pending;
    assert.equal(app.checkbox.disabled, false);
    assert.equal(app.checkbox.checked, false);
    assert.match(app.status.textContent, /saved/i);
});

test('failed save restores the last persisted visibility and gives fixed text-only feedback', async () => {
    const app = load(false);
    const pending = app.change(true);
    app.finish({ok: false, error: '<img src=x onerror=alert(1)>private database detail'}, false);
    await pending;
    assert.equal(app.checkbox.checked, false);
    assert.equal(app.checkbox.disabled, false);
    assert.match(app.status.textContent, /could not save/i);
    assert.doesNotMatch(app.status.textContent, /private|<img/);
});

test('malformed success is treated as failure instead of committing an unsaved choice', async () => {
    const app = load();
    const pending = app.change(false);
    app.finish({ok: true, show_classic_sqr_tracker: 'false'});
    await pending;
    assert.equal(app.checkbox.checked, true);
    assert.equal(app.checkbox.disabled, false);
});
