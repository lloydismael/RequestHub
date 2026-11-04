const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function dashboard() {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'hub', 'dashboard.html'), 'utf8');
    const script = [...template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\b[^>]*>/gi)]
        .map(match => match[1]).find(source => source.includes('window.applyDashboardRows'));
    const timers = [];
    const focused = {};
    const tbody = {innerHTML: 'initial', contains: element => element === focused};
    const wrapper = {scrollLeft: 17, scrolling: false, classList: {contains: () => wrapper.scrolling}};
    const document = {
        activeElement: focused,
        getElementById: id => id === 'dashboard-live-root' ? {dataset: {liveUrl: '/live/', liveVersion: 'initial'}} : id === 'dashboard-live-tbody' ? tbody : null,
        querySelector: () => wrapper,
        addEventListener: () => {},
    };
    const window = {setTimeout: callback => timers.push(callback), setInterval: () => {}};
    vm.runInNewContext(script, {window, document});
    return {window, document, tbody, wrapper, timers};
}

test('live search preserves its query in sort links and resets pagination', async () => {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'hub', 'dashboard.html'), 'utf8');
    const script = [...template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\b[^>]*>/gi)].map(match => match[1])
        .find(source => source.includes('function runSearch()'));
    const events = {};
    const input = {value: 'needle', dataset: {}, addEventListener: (name, fn) => { events[name] = fn; }};
    const link = {href: 'http://localhost/dashboard/?page=2&sort=created&direction=asc'};
    let ready;
    vm.runInNewContext(script, {
        URL, URLSearchParams, AbortController,
        document: {getElementById: id => id === 'dashboard-request-search' ? input : id === 'dashboard-live-root' ? {dataset: {liveUrl: '/live/'}} : null,
            querySelector: () => null, querySelectorAll: selector => selector.includes('thead') ? [link] : [],
            addEventListener: (name, fn) => { if (name === 'DOMContentLoaded') ready = fn; }},
        window: {location: {href: 'http://localhost/dashboard/?page=2', pathname: '/dashboard/', search: '?page=2', hash: ''},
            clearTimeout: () => {}, history: {replaceState: () => {}}},
        fetch: () => Promise.resolve({ok: true, json: () => Promise.resolve({ok: true, html_rows: 'new rows', total_count: 1})}),
    });
    ready();
    events.keydown({key: 'Enter', preventDefault: () => {}});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(new URL(link.href).searchParams.get('q'), 'needle');
    assert.equal(new URL(link.href).searchParams.has('page'), false);
});

test('a superseded focused-table refresh cannot overwrite newer search results', () => {
    const app = dashboard();
    app.window.applyDashboardRows('old poll');
    assert.equal(app.tbody.innerHTML, 'initial');
    app.document.activeElement = null;
    app.window.applyDashboardRows('new search');
    app.timers.shift()();
    assert.equal(app.tbody.innerHTML, 'new search');
    assert.equal(app.wrapper.scrollLeft, 17);
});

test('a superseded horizontal-scroll refresh is discarded', () => {
    const app = dashboard();
    app.document.activeElement = null;
    app.wrapper.scrolling = true;
    app.window.applyDashboardRows('old poll');
    app.wrapper.scrolling = false;
    app.window.applyDashboardRows('new search');
    app.timers.shift()();
    assert.equal(app.tbody.innerHTML, 'new search');
});
