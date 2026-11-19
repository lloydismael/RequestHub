const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function tracker() {
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'sqr-tracker-prototype.js'), 'utf8');
    const refresh = source.slice(source.indexOf('    function refreshRecord('), source.indexOf('    function apply('));
    const original = {pk: 1, stage: 'delivery', total: '100', attention: [], values: {}};
    const updated = {pk: 1, stage: 'billing', total: '200', attention: [], values: {}};
    const before = {all: {count: 1, total: '100'}, delivery: {count: 1}, billing: {count: 0}};
    const after = {all: {count: 1, total: '200'}, delivery: {count: 0}, billing: {count: 1}};
    const state = {records: [original], byPk: {1: original}, selectedPk: 1, shown: 50, sort: {key: 'attention', direction: 'desc'}, filters: {selections: {}, search: '', mine: false, attention: false, dateRanges: {}}, pipeline: before,
        viewer: {pk: 8, is_pm: true}, schema: {stages: [['delivery', 'Delivery'], ['billing', 'Billing']]}};
    const requests = [];
    const context = {
        state, ENDPOINT: '/data/', URLSearchParams, PAGE_SIZE: 50, el: {resultNotice: {}},
        record: pk => state.byPk[pk], renderAll: () => {}, syncEditor: () => {}, renderPipeline: () => {},
        api: url => { requests.push(url); return Promise.resolve({ok: true, data: url.includes('summary=1') ? {pipeline: context.summary} : {record: context.fresh}}); },
        fresh: updated, summary: after, requests,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'sqr-filter-core.js'), 'utf8'), context);
    context.core = context.SqrFilterCore;
    vm.runInContext(source.slice(source.indexOf('    function needsAction('), source.indexOf('    /* ── pipeline strip')), context);
    const start = source.indexOf('    function pipelineSummary(');
    if (start >= 0) vm.runInContext(source.slice(start, source.indexOf('    function renderPipeline(', start)), context);
    vm.runInContext(refresh, context);
    return {context, state, original, before};
}

test('tracker filters the full authorized pool before presentation slicing', () => {
    const app = tracker();
    app.state.records = Array.from({length: 65}, (_, i) => ({pk: i + 1, created_at: '2026-01-01', search: 'account', status: i < 60 ? 'reviewed' : 'submitted', attention: [], stage: 'delivery', total: '1'}));
    app.state.filters.selections.status = ['submitted'];
    assert.equal(app.context.visibleRecords().length, 5);
    app.state.filters.selections.status = ['submitted', 'reviewed'];
    assert.equal(app.context.visibleRecords().length, 65);
});

test('tracker loader does not request a server-filtered page', async () => {
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'sqr-tracker-prototype.js'), 'utf8');
    let requested;
    const context = {inFlight: null, loadController: null, loadVersion: 0, state: {loaded: true, filters: {stage: 'delivery', search: 'test'}}, ENDPOINT: '/data/', PAGE_SIZE: 50,
        AbortController, URLSearchParams, el: {more: {}}, apply: () => {}, api: url => { requested = url; return Promise.resolve({ok: true, data: {records: []}}); }, showToast: () => {}};
    vm.createContext(context); vm.runInContext(source.slice(source.indexOf('    function load(background'), source.indexOf('    /* ── wiring')), context);
    await context.load(false);
    const query = new URL(requested, 'http://localhost').searchParams;
    assert.equal(query.has('page'), false);
    assert.equal(query.has('stage'), false);
    assert.equal(query.has('q'), false);
});

test('edits and undo refresh authoritative pipeline counts and monetary totals', async () => {
    const app = tracker();
    await app.context.refreshRecord(1);
    assert.equal(app.state.pipeline.all.total, '200');
    assert.equal(app.state.pipeline.delivery.count, 0);
    assert.equal(app.state.pipeline.billing.count, 1);
    assert.equal(app.context.requests.length, 1, 'Refresh must not fetch obsolete server filter summaries');
    app.context.fresh = app.original;
    app.context.summary = app.before;
    await app.context.refreshRecord(1);
    assert.equal(app.state.pipeline.all.total, '100');
    assert.equal(app.state.pipeline.delivery.count, 1);
    assert.equal(app.state.pipeline.billing.count, 0);
});

function filterConsumer(storage = {getItem: () => null, setItem: () => {}}, search = '') {
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'sqr-tracker-prototype.js'), 'utf8');
    const context = {URLSearchParams, sessionStorage: storage, window: {location: {search}}, STORE_KEY: 'filters', PRESENTATION_KEY: 'presentation', TABLE_COLUMNS: [], DATE_COLUMNS: [], preferenceNotices: [],
        state: {records: [], options: {}, specs: {}, schema: {stages: [['delivery', 'Delivery']]}, viewer: {pk: 7}},
        el: {filterNotice: {textContent: '', hidden: true}}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'sqr-filter-core.js'), 'utf8'), context);
    context.core = context.window.SqrFilterCore;
    vm.runInContext(source.slice(source.indexOf('    var FILTER_LABELS'), source.indexOf('    var preferenceNotices')), context);
    context.state.filters = context.core.normalizeFilters({}, context.FILTER_KEYS).state;
    vm.runInContext(source.slice(source.indexOf('    function savePrefs'), source.indexOf('    /* ── toast')), context);
    vm.runInContext(source.slice(source.indexOf('    function filterDimensions'), source.indexOf('    function renderFilters')), context);
    return context;
}

test('tracker facets count beyond the current page, excluding their own dimension and using permitted labels', () => {
    const app = filterConsumer();
    app.state.options = {pm_users: [{value: '8', label: 'Directory manager'}]};
    app.state.specs = {status: {choices: [['reviewed', 'Reviewed'], ['submitted', 'Submitted']]}};
    app.state.records = Array.from({length: 65}, (_, i) => ({pk: i + 1, status: i < 60 ? 'reviewed' : 'submitted',
        pm_id: i < 60 ? '8' : '9', assigned_pm_name: i < 60 ? 'Record manager' : 'Reviewer fallback', stage: 'delivery', attention: []}));
    app.state.filters.selections.status = ['submitted'];
    let facets = app.filterDimensions();
    const facet = key => facets.find(dimension => dimension.key === key).options;
    assert.equal(facet('status').find(option => option.value === 'reviewed').count, 60);
    assert.equal(facet('pm_id').find(option => option.value === '8').count, 0);
    assert.equal(facet('pm_id').find(option => option.value === '8').label, 'Directory manager');
    assert.equal(facet('pm_id').find(option => option.value === '9').count, 5);
    assert.equal(facet('pm_id').find(option => option.value === '9').label, 'Reviewer fallback');
    app.state.options = {pm_users: [], sse_users: [], rq_options: []};
    facets = app.filterDimensions();
    assert.equal(facet('pm_id').find(option => option.value === '8').label, 'Record manager');
    assert.equal(facet('pm_id').some(option => option.value === '100'), false);
});

test('consumer URL migration preserves unavailable IDs and stores filters rather than records or permissions', () => {
    let saved;
    const app = filterConsumer({getItem: key => key === 'filters' ? JSON.stringify({records: [{pk: 1}], selections: {pm_id: ['500']}}) : null,
        setItem: (key, value) => { saved = JSON.parse(value); }}, '?manager=999&status=reviewed&status=submitted&q=alpha');
    app.loadPrefs();
    assert.deepEqual(Array.from(app.state.filters.selections.pm_id), ['999']);
    assert.deepEqual(Array.from(app.state.filters.selections.status), ['reviewed', 'submitted']);
    assert.equal(app.state.filters.search, 'alpha');
    app.state.records = [{pk: 1, perms: {admin: true}}]; app.savePrefs();
    assert.equal(saved.records, undefined); assert.equal(saved.viewer, undefined);
    assert.deepEqual(saved.selections.pm_id, ['999']);
});

test('corrupt and unavailable browser storage leaves applied filters usable with a visible notice', () => {
    const app = filterConsumer({getItem: () => '{broken', setItem: () => { throw new Error('quota'); }});
    app.loadPrefs();
    assert.equal(app.state.filters.search, '');
    assert.ok(app.preferenceNotices.length > 0);
    app.state.filters.search = 'retained'; app.savePrefs();
    assert.equal(app.state.filters.search, 'retained');
    assert.equal(app.el.filterNotice.hidden, false);
    assert.ok(app.el.filterNotice.textContent.includes('could not be saved'));
});
