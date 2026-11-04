const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function tracker() {
    const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'sqr-tracker-prototype.js'), 'utf8');
    const refresh = source.slice(source.indexOf('    function refreshRecord('), source.indexOf('    function apply('));
    const original = {pk: 1, stage: 'delivery', total: '100', values: {}};
    const updated = {pk: 1, stage: 'billing', total: '200', values: {}};
    const before = {all: {count: 1, total: '100'}, delivery: {count: 1}, billing: {count: 0}};
    const after = {all: {count: 1, total: '200'}, delivery: {count: 0}, billing: {count: 1}};
    const state = {records: [original], byPk: {1: original}, selectedPk: 1, filters: {stage: 'all', search: ''}, pipeline: before};
    const context = {
        state, ENDPOINT: '/data/', URLSearchParams,
        record: pk => state.byPk[pk], renderAll: () => {}, syncEditor: () => {}, renderPipeline: () => {},
        api: url => Promise.resolve({ok: true, data: url.includes('summary=1') ? {pipeline: context.summary} : {record: context.fresh}}),
        fresh: updated, summary: after,
    };
    vm.createContext(context);
    vm.runInContext(refresh, context);
    return {context, state, original, before};
}

test('filter navigation does not reopen onboarding or steal filter focus', () => {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'hub', 'sqr.html'), 'utf8');
    const script = [...template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\b[^>]*>/gi)].map(match => match[1])
        .find(source => source.includes("var storageKey = 'requestHub.sqr.hideNewVersionNotice'"));
    let visible = false;
    let focusCalls = 0;
    const element = {addEventListener: () => {}, classList: {toggle: (name, value) => { visible = value; }},
        setAttribute: () => {}, focus: () => { focusCalls += 1; }};
    vm.runInNewContext(script, {
        document: {body: {dataset: {sqrFilterNavigation: '1'}}, getElementById: () => element, addEventListener: () => {}},
        window: {localStorage: {getItem: () => null}},
    });
    assert.equal(visible, false);
    assert.equal(focusCalls, 0);
});

test('edits and undo refresh authoritative pipeline counts and monetary totals', async () => {
    const app = tracker();
    await app.context.refreshRecord(1);
    assert.equal(app.state.pipeline.all.total, '200');
    assert.equal(app.state.pipeline.delivery.count, 0);
    assert.equal(app.state.pipeline.billing.count, 1);
    app.context.fresh = app.original;
    app.context.summary = app.before;
    await app.context.refreshRecord(1);
    assert.equal(app.state.pipeline.all.total, '100');
    assert.equal(app.state.pipeline.delivery.count, 1);
    assert.equal(app.state.pipeline.billing.count, 0);
});
