const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'accounts', 'profile_form.html'), 'utf8');
const source = template.slice(template.indexOf("    const themeForm = document.getElementById('profile-theme-form');"), template.indexOf('    // Live avatar preview'));
const defaults = { enabled: true, border: true, snow: true, lights: true, cursor: true, clicks: true, santa: true };

function start({ preferences = {}, submitted = null, theme = 'christmas' } = {}) {
    const saved = { ...defaults, ...preferences };
    const values = { ...saved, ...submitted };
    const sent = [];
    const windowEvents = {};
    let chosen = theme;
    function element() {
        const classes = new Set();
        return {
            dataset: {}, hidden: false, callbacks: {},
            style: { setProperty() {}, removeProperty() {} },
            classList: { toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); } },
            addEventListener(name, callback) { this.callbacks[name] = callback; },
        };
    }
    const body = element();
    body.dataset.accountTheme = theme;
    const controls = Object.fromEntries(Object.entries(values).map(([key, checked]) => {
        const control = element();
        control.name = `pref_christmas_${key}`;
        control.dataset.christmasEffect = key;
        control.checked = checked;
        return [key, control];
    }));
    const radios = Object.fromEntries(['default', 'christmas', 'blue', 'custom'].map(value => [value, {
        name: 'banner_gradient', value,
        get checked() { return chosen === value; },
        set checked(enabled) { if (enabled) chosen = value; },
    }]));
    const form = element();
    form.querySelector = selector => selector.endsWith(':checked')
        ? radios[chosen] : radios[selector.match(/value="([^"]+)"/)[1]];
    form.querySelectorAll = selector => selector === '[data-christmas-effect]' ? Object.values(controls) : [];
    const cancel = element();
    const status = element();
    const customPanel = element();
    const colors = ['#2563eb', '#7c3aed', '#06b6d4'];
    customPanel.dataset.savedColors = colors.join(',');
    const hexInputs = colors.map((value, index) => ({ value, type: 'text', dataset: { colorSlot: String(index) } }));
    const pickers = colors.map((value, index) => ({ value, type: 'color', dataset: { colorSlot: String(index) } }));
    customPanel.querySelectorAll = selector => selector.includes('type="text"') ? hexInputs
        : selector.includes('type="color"') ? pickers : [];
    const nodes = {
        'profile-theme-form': form, 'theme-cancel-btn': cancel, 'theme-status': status,
        'theme-custom-panel': customPanel, 'theme-card-custom': element(),
        'christmas-effects-settings': { textContent: JSON.stringify(saved) },
    };
    const document = {
        body, getElementById: id => nodes[id],
        dispatchEvent(event) { sent.push(event); },
    };
    vm.runInNewContext(source, {
        document,
        window: { location: { hash: '' }, addEventListener(name, callback) { windowEvents[name] = callback; } },
        CustomEvent: class { constructor(type, { detail }) { this.type = type; this.detail = detail; } },
    });
    return {
        body, controls, cancel, status,
        effect(key, enabled) { controls[key].checked = enabled; form.callbacks.change({ target: controls[key] }); },
        chooseTheme(value) { radios[value].checked = true; form.callbacks.change({ target: radios[value] }); },
        restore() { cancel.callbacks.click(); },
        cachedReturn() { windowEvents.pageshow({ persisted: true }); },
        get preview() { const event = sent.filter(item => item.type === 'requesthub:christmas-effects-preview').at(-1); return event && JSON.parse(JSON.stringify(event.detail)); },
    };
}

test('an effect-only change previews immediately and Cancel restores the saved choices', () => {
    const app = start();
    app.effect('border', false);
    assert.equal(app.cancel.hidden, false);
    assert.equal(app.preview.border, false);
    assert.match(app.status.textContent, /Preview only/);
    app.restore();
    assert.equal(app.controls.border.checked, true);
    assert.equal(app.cancel.hidden, true);
    assert.deepEqual(app.preview, defaults);
});

test('a bound unsaved form remains a preview until Cancel restores account values', () => {
    const app = start({ preferences: { snow: false, santa: false }, submitted: { border: false, snow: true, santa: true } });
    assert.equal(app.cancel.hidden, false);
    assert.equal(app.preview.border, false);
    app.restore();
    assert.equal(app.controls.border.checked, true);
    assert.equal(app.controls.snow.checked, false);
    assert.equal(app.controls.santa.checked, false);
    assert.equal(app.cancel.hidden, true);
    assert.deepEqual(app.preview, { ...defaults, snow: false, santa: false });
});

test('switching preview themes retains effect choices without treating them as saved', () => {
    const app = start();
    app.effect('cursor', false);
    app.effect('lights', false);
    app.chooseTheme('blue');
    assert.equal(app.body.dataset.accountTheme, 'blue');
    app.chooseTheme('christmas');
    assert.equal(app.cancel.hidden, false);
    assert.deepEqual(app.preview, { ...defaults, cursor: false, lights: false });
    app.restore();
    assert.deepEqual(app.preview, defaults);
});

test('cached page restoration discards effect previews just like theme previews', () => {
    const app = start();
    app.effect('santa', false);
    app.cachedReturn();
    assert.equal(app.controls.santa.checked, true);
    assert.equal(app.cancel.hidden, true);
    assert.deepEqual(app.preview, defaults);
});

test('master changes preview without clearing individual selections', () => {
    const app = start({ preferences: { snow: false } });
    app.effect('enabled', false);
    assert.equal(app.cancel.hidden, false);
    assert.equal(app.controls.border.checked, true);
    assert.equal(app.controls.snow.checked, false);
    assert.deepEqual(app.preview, { ...defaults, enabled: false, snow: false });
    app.restore();
    assert.deepEqual(app.preview, { ...defaults, snow: false });
});
