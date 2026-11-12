const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'hub', 'sqr.html'), 'utf8');
const start = template.indexOf('// ── SQR table view toggle');
const end = template.indexOf('</script>', start);
assert.ok(start >= 0 && end > start, 'SQR view controller exists');
const source = template.slice(start, end);

function element(dataset = {}, hidden = false) {
    const attributes = {};
    const classes = new Set();
    return {
        dataset, hidden, attributes, events: {}, disabled: false,
        classList: {toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }},
        setAttribute(name, value) { attributes[name] = String(value); },
        removeAttribute(name) { delete attributes[name]; },
        addEventListener(name, callback) { this.events[name] = callback; },
        click() { this.events.click(); },
    };
}

function load({query = '', saved = 'tracker', performance = true, blocked = false} = {}) {
    const buttons = ['tracker', 'prototype'].map(name => element({sqrViewButton: name}));
    const names = performance ? ['tracker', 'performance', 'prototype'] : ['tracker', 'prototype'];
    const panels = names.map(name => element({sqrViewPanel: name}));
    const nav = ['performance', 'tracker'].map(name => element({sqrNavView: name}));
    const header = element();
    const trackerLink = element();
    const trackerControls = [element(), element({}, true)];
    const prototypeControls = [element()];
    const events = [];
    const location = new URL('https://fixture.invalid/sqr/' + query);
    const storage = {
        getItem() { if (blocked) throw new Error('blocked'); return saved; },
        setItem(_key, value) { if (blocked) throw new Error('blocked'); saved = value; },
    };
    const window = {
        location,
        localStorage: storage,
        history: {state: null, replaceState(_state, _title, url) { location.href = new URL(url, location).href; }},
    };
    const document = {
        title: 'SQR · Request Hub',
        querySelector(selector) { return selector === '[data-sqr-header]' ? header : null; },
        querySelectorAll(selector) {
            return {
                '[data-sqr-view-button]': buttons,
                '[data-sqr-view-panel]': panels,
                '[data-sqr-tracker-only]': trackerControls,
                '[data-sqr-prototype-only]': prototypeControls,
                '[data-sqr-nav-view]': nav,
                '[data-sqr-tracker-link]': [trackerLink],
            }[selector] || [];
        },
        dispatchEvent(event) { events.push(event.detail.view); },
    };
    class CustomEvent { constructor(_name, options) { this.detail = options.detail; } }
    vm.runInNewContext(source, {document, window, CustomEvent, URLSearchParams});
    return {
        document, window, buttons, panels, nav, header, trackerLink, trackerControls, prototypeControls, events,
        panel(name) { return panels.find(panel => panel.dataset.sqrViewPanel === name); },
        navItem(name) { return nav.find(link => link.dataset.sqrNavView === name); },
        stored() { return saved; },
    };
}

function assertCurrent(app, name) {
    assert.equal(app.navItem(name).attributes['aria-current'], 'page');
    const other = name === 'performance' ? 'tracker' : 'performance';
    assert.equal(app.navItem(other).attributes['aria-current'], undefined);
}

test('PPT link opens the performance panel even when tracker was saved', () => {
    const app = load({query: '?view=performance', saved: 'tracker'});
    assert.equal(app.panel('performance').hidden, false);
    assert.equal(app.panel('tracker').hidden, true);
    assert.equal(app.header.hidden, true);
    assert.equal(app.trackerControls[0].hidden, true);
    assertCurrent(app, 'performance');
    assert.equal(app.trackerLink.attributes['aria-current'], undefined);
    assert.equal(app.document.title, 'PPT · Request Hub');
    assert.equal(app.events.at(-1), 'performance');
});

test('SQR link overrides an old performance preference and restores tracker controls', () => {
    const app = load({query: '?view=tracker', saved: 'performance'});
    assert.equal(app.panel('tracker').hidden, false);
    assert.equal(app.panel('performance').hidden, true);
    assert.equal(app.header.hidden, false);
    assert.equal(app.trackerControls[0].hidden, false);
    assert.equal(app.trackerControls[1].hidden, true);
    assertCurrent(app, 'tracker');
    assert.equal(app.trackerLink.attributes['aria-current'], 'page');
    assert.equal(app.stored(), 'tracker');
});

test('switching to the prototype updates navigation and keeps URL filters', () => {
    const app = load({query: '?view=tracker&q=example#details'});
    app.buttons.find(button => button.dataset.sqrViewButton === 'prototype').click();
    assert.equal(app.panel('prototype').hidden, false);
    assert.equal(app.panel('performance').hidden, true);
    assert.equal(app.header.hidden, false);
    assert.equal(app.prototypeControls[0].hidden, false);
    assert.equal(app.window.location.searchParams.get('view'), 'prototype');
    assert.equal(app.window.location.searchParams.get('q'), 'example');
    assert.equal(app.window.location.hash, '#details');
    assertCurrent(app, 'tracker');
    assert.equal(app.document.title, 'SQR · Request Hub');
});

test('switching from PPT to the tracker restores the SQR header', () => {
    const app = load({query: '?view=performance'});
    assert.equal(app.header.hidden, true);
    app.window.setSqrView('tracker');
    assert.equal(app.header.hidden, false);
    assert.equal(app.panel('tracker').hidden, false);
    assertCurrent(app, 'tracker');
});

test('unavailable performance and focused tracker links safely select the tracker', () => {
    const restricted = load({query: '?view=performance', performance: false});
    assert.equal(restricted.panel('tracker').hidden, false);
    assert.equal(restricted.header.hidden, false);
    assert.equal(restricted.window.location.searchParams.get('view'), 'tracker');
    assertCurrent(restricted, 'tracker');

    const focused = load({query: '?view=performance&focus=42'});
    assert.equal(focused.panel('tracker').hidden, false);
    assert.equal(focused.header.hidden, false);
    assert.equal(focused.window.location.searchParams.get('focus'), '42');
    assert.equal(focused.window.location.searchParams.get('view'), 'tracker');
});

test('explicit navigation works when browser storage is blocked', () => {
    const app = load({query: '?view=performance', blocked: true});
    assert.equal(app.panel('performance').hidden, false);
    assert.equal(app.header.hidden, true);
    assertCurrent(app, 'performance');
});

test('old performance preference cannot hijack a plain SQR link', () => {
    const tracker = load({saved: 'performance'});
    assert.equal(tracker.panel('tracker').hidden, false);
    const prototype = load({saved: 'prototype'});
    assert.equal(prototype.panel('prototype').hidden, false);
});

test('focusing a project on another page returns to the tracker with filters intact', () => {
    const start = template.indexOf('    window.focusSqrTrackerProject = function (pk) {');
    const end = template.indexOf('\n    if (focusDismiss)', start);
    assert.ok(start >= 0 && end > start, 'focused project controller exists');
    let destination = '';
    const window = {location: {pathname: '/sqr/', search: '?view=performance&page=3&q=example',
        assign(url) { destination = url; }}};
    const document = {querySelector: () => ({querySelectorAll: () => []}), getElementById: () => ({})};
    vm.runInNewContext(template.slice(start, end), {window, document, URLSearchParams});
    assert.equal(window.focusSqrTrackerProject('42'), true);
    const url = new URL(destination, 'https://fixture.invalid');
    assert.equal(url.searchParams.get('view'), 'tracker');
    assert.equal(url.searchParams.get('focus'), '42');
    assert.equal(url.searchParams.get('q'), 'example');
    assert.equal(url.searchParams.has('page'), false);
    assert.equal(window.focusSqrTrackerProject('not-a-number'), false);
});
