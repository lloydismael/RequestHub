const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '..', 'static', 'js', 'christmas-theme.js');

function start({ theme = 'christmas', reduced = false, effects = false, stored = null, storageBlocked = false, account = null, controlsPresent = true } = {}) {
    const keys = ['enabled', 'border', 'snow', 'lights', 'cursor', 'clicks', 'santa'];
    const preferences = account === null ? null : { ...Object.fromEntries(keys.map(key => [key, true])), ...account };
    const callbacks = {};
    const frames = new Map();
    const timers = new Map();
    let observer;
    let nextId = 1;
    let time = 0;
    let storage = stored;
    function element() {
        const classes = new Set();
        return {
            children: [], dataset: {}, attributes: {}, hidden: false, checked: true,
            classList: {
                contains: name => classes.has(name),
                toggle(name, value) { if (value) classes.add(name); else classes.delete(name); },
            },
            style: { setProperty() {} },
            setAttribute(name, value) { this.attributes[name] = value; },
            appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
            remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; },
            addEventListener(name, callback) { this[name] = callback; },
            getContext() { return { clearRect() {}, beginPath() {}, arc() {}, fill() {}, moveTo() {}, lineTo() {}, stroke() {}, setTransform() {} }; },
        };
    }
    const body = element();
    body.dataset.accountTheme = theme;
    body.classList.toggle('authenticated', true);
    body.classList.toggle('pref-reduce-motion', reduced);
    body.classList.toggle('pref-reduce-effects', effects);
    const nav = element();
    const panel = element();
    const toggle = element();
    const status = element();
    const controls = Object.fromEntries(keys.map(key => [key, key === 'enabled' ? toggle : element()]));
    if (preferences) {
        for (const [key, control] of Object.entries(controls)) {
            control.dataset.christmasEffect = key;
            control.checked = preferences[key];
        }
    }
    const settings = preferences ? { textContent: JSON.stringify(preferences) } : null;
    const media = { matches: false, addEventListener(name, callback) { this.change = callback; } };
    const document = {
        body, hidden: false,
        createElement: element,
        querySelector: selector => selector === '.navbar' ? nav : null,
        querySelectorAll: selector => selector === '[data-christmas-effect]' && preferences && controlsPresent ? Object.values(controls) : [],
        getElementById: id => id === 'christmas-effects-settings' ? settings : controlsPresent ? ({ 'christmas-controls': panel, 'christmas-effects-toggle': toggle, 'christmas-effects-status': status })[id] || null : null,
        addEventListener(name, callback) { callbacks[name] = callback; },
    };
    const window = {
        innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
        matchMedia: () => media,
        localStorage: {
            getItem() { if (storageBlocked) throw Error('unavailable'); return storage; },
            setItem(key, value) { if (storageBlocked) throw Error('unavailable'); storage = value; },
        },
        requestAnimationFrame(callback) { const id = nextId++; frames.set(id, callback); return id; },
        cancelAnimationFrame(id) { frames.delete(id); },
        setTimeout(callback, delay) { const id = nextId++; timers.set(id, { callback, due: time + delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(name, callback) { callbacks[name] = callback; },
    };
    vm.runInNewContext(fs.readFileSync(scriptPath, 'utf8'), {
        document, window, Math, performance: { now: () => 0 },
        MutationObserver: class { constructor(callback) { observer = callback; } observe() {} },
    });
    return {
        body, nav, panel, toggle, status, media, frames, timers, document, controls,
        effect(key, value) { const control = controls[key]; control.checked = value; callbacks.change({ target: control }); },
        preview(values) { if (callbacks['requesthub:christmas-effects-preview']) callbacks['requesthub:christmas-effects-preview']({ detail: values }); },
        storageEvent(value) { callbacks.storage({ key: 'reqHubChristmasEffects', newValue: value }); },
        changeTheme(value) { body.dataset.accountTheme = value; observer(); },
        preference(name, value) { body.classList.toggle(name, value); observer(); },
        switchMode(dark, id = 'theme-checkbox') { callbacks.change({ target: { id, checked: dark } }); },
        santas() { return body.children.filter(child => String(child.className).startsWith('christmas-santa')); },
        click(x = 100, y = 100, detail = 1) { callbacks.click({ clientX: x, clientY: y, detail, button: 0 }); },
        visibility(hidden) { document.hidden = hidden; callbacks.visibilitychange(); },
        frame() { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(16)); },
        expire(delay = Infinity) {
            time = delay === Infinity
                ? Math.max(time, ...[...timers.values()].map(timer => timer.due))
                : time + delay;
            for (const [id, timer] of [...timers]) {
                if (timer.due <= time) { timers.delete(id); timer.callback(); }
            }
        },
        get storage() { return storage; },
    };
}

test('other themes create no festive decorations or animation', () => {
    const app = start({ theme: 'blue' });
    assert.equal(app.body.children.length, 0);
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.frames.size, 0);
    assert.equal(app.panel.hidden, true);
});

test('Christmas creates decorations and starts snow; cancelling preview cleans up', () => {
    const app = start({ theme: 'default' });
    app.changeTheme('christmas');
    assert.ok(app.body.children.some(child => child.className === 'christmas-snow'));
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.frames.size, 1);
    assert.equal(app.panel.hidden, false);
    app.click();
    assert.ok(app.body.children.some(child => child.className === 'christmas-burst'));
    app.changeTheme('default');
    assert.equal(app.body.children.length, 0);
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.frames.size, 0);
    assert.equal(app.timers.size, 0);
});

test('Christmas border is a single image frame without the old garland baubles', () => {
    const app = start();
    const frame = app.body.children.find(child => child.className === 'christmas-frame');
    assert.ok(frame);
    assert.equal(frame.attributes['aria-hidden'], 'true');
    assert.equal(frame.children.length, 0);
});

test('effects toggle persists on this device and suspends snow and click bursts', () => {
    const app = start();
    app.toggle.checked = false;
    app.toggle.change();
    assert.equal(app.storage, 'off');
    assert.equal(app.frames.size, 0);
    assert.equal(app.body.classList.contains('christmas-effects-on'), false);
    app.click();
    assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    app.toggle.checked = true;
    app.toggle.change();
    assert.equal(app.frames.size, 1);
    assert.equal(app.storage, 'on');
});

test('motion and effects preferences override festive animation including OS changes', () => {
    for (const options of [{ reduced: true }, { effects: true }, { stored: 'off' }]) {
        const app = start(options);
        assert.equal(app.frames.size, 0);
        app.click();
        assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    }
    const app = start();
    app.media.matches = true;
    app.media.change();
    assert.equal(app.frames.size, 0);
    app.media.matches = false;
    app.media.change();
    assert.equal(app.frames.size, 1);
    app.preference('pref-reduce-motion', true);
    assert.equal(app.frames.size, 0);
});

test('hidden pages pause and resume a single animation loop', () => {
    const app = start();
    app.visibility(true);
    assert.equal(app.frames.size, 0);
    app.visibility(false);
    app.frame();
    assert.equal(app.frames.size, 1);
    app.changeTheme('blue');
    app.visibility(false);
    assert.equal(app.frames.size, 0);
});

test('the snow loop suspends while the theme is switching and resumes after', () => {
    const app = start();
    app.frame();
    assert.equal(app.frames.size, 1);
    app.preference('theme-switching', true);
    assert.equal(app.frames.size, 0);
    app.frame();
    assert.equal(app.frames.size, 0);
    app.preference('theme-switching', false);
    assert.equal(app.frames.size, 1);
});
test('click bursts are bounded, expire, and ignore keyboard clicks', () => {
    const app = start();
    app.click(0, 0, 0);
    assert.equal(app.timers.size, 0);
    for (let index = 0; index < 50; index++) app.click();
    assert.ok(app.body.children.filter(child => child.className === 'christmas-burst').length <= 6);
    assert.ok(app.timers.size <= 6);
    app.expire();
    assert.equal(app.body.children.filter(child => child.className === 'christmas-burst').length, 0);
});

test('light/dark toggle sends one Santa sleigh across in the matching direction', () => {
    const app = start();
    app.switchMode(true);
    let santas = app.santas();
    assert.equal(santas.length, 1);
    assert.equal(santas[0].className, 'christmas-santa christmas-santa--to-dark');
    assert.equal(santas[0].attributes['aria-hidden'], 'true');
    assert.ok(santas[0].children.some(child => child.className === 'christmas-santa__art'));
    app.switchMode(false);
    santas = app.santas();
    assert.equal(santas.length, 1);
    assert.equal(santas[0].className, 'christmas-santa christmas-santa--to-light');
    app.expire();
    assert.equal(app.santas().length, 0);
    app.switchMode(true, 'christmas-effects-toggle-other');
    assert.equal(app.santas().length, 0);
});

test('Santa safety cleanup waits for the faster flight then removes it without animationend', () => {
    for (const dark of [true, false]) {
        const app = start();
        app.switchMode(dark);
        app.expire(1900);
        assert.equal(app.santas().length, 1);
        app.expire(400);
        assert.equal(app.santas().length, 0);
        assert.equal(app.timers.size, 0);
    }
});

test('Santa flyover respects theme, effects toggle, and motion preferences', () => {
    for (const options of [{ theme: 'blue' }, { reduced: true }, { effects: true }, { stored: 'off' }]) {
        const app = start(options);
        app.switchMode(true);
        assert.equal(app.santas().length, 0);
    }
    const app = start();
    app.switchMode(true);
    app.changeTheme('default');
    assert.equal(app.santas().length, 0);
    assert.equal(app.timers.size, 0);
});

test('saved account choices override the legacy device switch on every page', () => {
    const app = start({ account: {}, stored: 'off', controlsPresent: false });
    assert.equal(app.frames.size, 1);
    app.storageEvent('off');
    assert.equal(app.frames.size, 1);
    const paused = start({ account: { enabled: false }, stored: 'on' });
    assert.equal(paused.body.children.length, 0);
    assert.equal(paused.frames.size, 0);
});

test('border and snowfall can be switched independently of click and Santa animation', () => {
    const app = start({ account: { border: false } });
    assert.equal(app.body.children.some(child => child.className === 'christmas-frame'), false);
    assert.equal(app.frames.size, 1);
    app.effect('snow', false);
    assert.equal(app.frames.size, 0);
    assert.equal(app.body.children.some(child => child.className === 'christmas-snow'), false);
    app.click();
    assert.ok(app.body.children.some(child => child.className === 'christmas-burst'));
    app.switchMode(true);
    assert.equal(app.santas().length, 1);
    app.effect('border', true);
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
});

test('cursor and navbar light choices do not disable the other festive effects', () => {
    const app = start({ account: { cursor: false, lights: false } });
    assert.ok(app.body.classList.contains('christmas-cursor-off'));
    assert.ok(app.body.classList.contains('christmas-lights-off'));
    assert.equal(app.frames.size, 1);
    app.effect('cursor', true);
    app.effect('lights', true);
    assert.equal(app.body.classList.contains('christmas-cursor-off'), false);
    assert.equal(app.body.classList.contains('christmas-lights-off'), false);
    app.changeTheme('blue');
    assert.equal(app.body.classList.contains('christmas-lights-off'), false);
});

test('disabling clicks or Santa cleans up a running effect without stopping snowfall', () => {
    const app = start({ account: {} });
    app.click();
    app.switchMode(true);
    app.effect('clicks', false);
    assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    assert.equal(app.santas().length, 1);
    app.effect('santa', false);
    assert.equal(app.santas().length, 0);
    assert.equal(app.timers.size, 0);
    assert.equal(app.frames.size, 1);
    app.click();
    app.switchMode(false);
    assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    assert.equal(app.santas().length, 0);
});

test('master off removes all effects but restores the individual selections when enabled', () => {
    const app = start({ account: { snow: false, santa: false } });
    app.click();
    app.effect('enabled', false);
    assert.equal(app.body.children.length, 0);
    assert.equal(app.timers.size, 0);
    assert.ok(app.body.classList.contains('christmas-lights-off'));
    assert.equal(app.controls.border.checked, true);
    assert.equal(app.controls.snow.checked, false);
    assert.equal(app.controls.santa.checked, false);
    app.effect('enabled', true);
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
    assert.equal(app.frames.size, 0);
    app.switchMode(true);
    assert.equal(app.santas().length, 0);
});

test('preview changes and cancellation reconcile all effects without changing device storage', () => {
    const app = start({ account: {} });
    app.preview({ border: false, snow: false, santa: false });
    assert.equal(app.body.children.length, 0);
    app.switchMode(true);
    assert.equal(app.santas().length, 0);
    app.preview({ border: true, snow: true, santa: true });
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
    assert.equal(app.frames.size, 1);
    app.switchMode(false);
    assert.equal(app.santas().length, 1);
    assert.equal(app.storage, null);
});

test('account effect changes still respect accessibility and hidden-page preferences', () => {
    const app = start({ account: { santa: false }, reduced: true });
    app.effect('snow', true);
    app.click();
    app.switchMode(true);
    assert.equal(app.frames.size, 0);
    assert.equal(app.timers.size, 0);
    app.preference('pref-reduce-motion', false);
    assert.equal(app.frames.size, 1);
    app.switchMode(true);
    assert.equal(app.santas().length, 0);
    app.visibility(true);
    app.click();
    app.switchMode(false);
    assert.equal(app.frames.size, 0);
    assert.equal(app.timers.size, 0);
});

test('blocked local storage does not prevent preview or effects toggling', () => {
    const app = start({ storageBlocked: true });
    assert.equal(app.frames.size, 1);
    app.toggle.checked = false;
    assert.doesNotThrow(() => app.toggle.change());
    assert.equal(app.frames.size, 0);
});
