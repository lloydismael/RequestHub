const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'hub', 'sqr.html'), 'utf8');
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

function load({query = '', saved = 'tracker', performance = true, classic = true, initialView, blocked = false} = {}) {
    const buttons = (classic ? ['tracker', 'prototype'] : []).map(name => element({sqrViewButton: name}));
    const names = [...(classic ? ['tracker'] : []), ...(performance ? ['performance'] : []), 'prototype'];
    const panels = names.map(name => element({sqrViewPanel: name}, name !== 'tracker' || initialView !== 'tracker'));
    const nav = ['performance', 'prototype'].map(name => {
        const url = '/sqr/?view=' + name;
        const link = element({sqrNavView: name, sqrNavUrl: url});
        if (name !== (query.includes('view=performance') && performance ? 'performance' : 'prototype')) {
            link.setAttribute('href', url);
        }
        return link;
    });
    const header = element(initialView ? {sqrInitialView: initialView} : {});
    const trackerControls = [element(), element({}, true)];
    const prototypeControls = [element({}, true)];
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
            }[selector] || [];
        },
        dispatchEvent(event) { events.push(event.detail.view); },
    };
    class CustomEvent { constructor(_name, options) { this.detail = options.detail; } }
    vm.runInNewContext(source, {document, window, CustomEvent, URLSearchParams});
    return {
        document, window, buttons, panels, nav, header, trackerControls, prototypeControls, events,
        panel(name) { return panels.find(panel => panel.dataset.sqrViewPanel === name); },
        navItem(name) { return nav.find(link => link.dataset.sqrNavView === name); },
        stored() { return saved; },
    };
}

function assertCurrent(app, name) {
    name = name === 'tracker' ? 'prototype' : name;
    assert.equal(app.navItem(name).attributes['aria-current'], 'page');
    assert.equal(app.navItem(name).attributes.href, undefined);
    const other = name === 'performance' ? 'prototype' : 'performance';
    assert.equal(app.navItem(other).attributes['aria-current'], undefined);
    assert.equal(app.navItem(other).attributes.href, '/sqr/?view=' + other);
}

test('PPT link opens the performance panel even when tracker was saved', () => {
    const app = load({query: '?view=performance', saved: 'tracker'});
    assert.equal(app.panel('performance').hidden, false);
    assert.equal(app.panel('tracker').hidden, true);
    assert.equal(app.header.hidden, true);
    assert.equal(app.trackerControls[0].hidden, true);
    assertCurrent(app, 'performance');
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
    assertCurrent(app, 'performance');
    app.window.setSqrView('tracker');
    assert.equal(app.header.hidden, false);
    assert.equal(app.panel('tracker').hidden, false);
    assertCurrent(app, 'tracker');
});

test('unavailable performance falls back to unified while shown focused links select Classic', () => {
    const restricted = load({query: '?view=performance', performance: false});
    assert.equal(restricted.panel('prototype').hidden, false);
    assert.equal(restricted.header.hidden, false);
    assert.equal(restricted.window.location.searchParams.get('view'), 'prototype');
    assertCurrent(restricted, 'prototype');

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

test('saved preferences cannot override the server-resolved primary SQR view', () => {
    for (const saved of ['performance', 'tracker', 'prototype']) {
        const app = load({saved, initialView: 'prototype'});
        assert.equal(app.panel('prototype').hidden, false);
        assertCurrent(app, 'prototype');
    }
});

test('hidden Classic cannot be resurrected by stored preferences, focus or manual switching', () => {
    const app = load({classic: false, saved: 'tracker', query: '?view=tracker&focus=42', initialView: 'prototype'});
    assert.equal(app.panel('tracker'), undefined);
    assert.equal(app.panel('prototype').hidden, false);
    assert.equal(app.prototypeControls[0].hidden, false);
    assert.equal(app.events.at(-1), 'prototype');
    app.window.setSqrView('tracker');
    assert.equal(app.panel('prototype').hidden, false);
    assert.equal(app.window.location.searchParams.get('view'), 'prototype');
    assertCurrent(app, 'prototype');
});

test('authorized PPT initializes without the tracker selector', () => {
    const app = load({classic: false, query: '?view=performance', initialView: 'performance'});
    assert.equal(app.panel('performance').hidden, false);
    assert.equal(app.panel('prototype').hidden, true);
    assert.equal(app.header.hidden, true);
    assert.equal(app.prototypeControls[0].hidden, true);
    assertCurrent(app, 'performance');
    assert.equal(app.events.at(-1), 'performance');
});

test('unknown bookmarks fall back to unified SQR', () => {
    const app = load({query: '?view=unknown'});
    assert.equal(app.panel('prototype').hidden, false);
    assertCurrent(app, 'prototype');
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
