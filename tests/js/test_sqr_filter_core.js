const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const keys = ['stage', 'status', 'pm_id', 'sse_id', 'schedule_performance', 'quality_performance', 'linked_request_id'];
function core() {
    const file = path.join(__dirname, '..', '..', 'static', 'js', 'sqr-filter-core.js');
    const context = {URLSearchParams};
    vm.createContext(context);
    if (fs.existsSync(file)) {
        vm.runInContext(fs.readFileSync(file, 'utf8'), context);
        return context.SqrFilterCore;
    }
    // Before the shared engine exists, exercise the actual legacy tracker predicate.
    const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'sqr-tracker-prototype.js'), 'utf8');
    vm.runInContext(source.slice(source.indexOf('    function needsAction('), source.indexOf('    /* ── pipeline strip')), context);
    return {
        filterRecords(records, filters, viewerId) {
            context.state = {records, viewer: {pk: viewerId, is_pm: true}, filters: {
                search: filters.search || '', stage: (filters.selections?.stage || [])[0] || 'all',
            }};
            return context.visibleRecords();
        },
    };
}
const engine = core();
const rows = [
    {pk: 1, stage: 'delivery', status: 'reviewed', pm_id: '8', approver_id: 3, schedule_performance: '',
        search: 'acme network pat', total: '100', delivery_start_date: '2028-02-01', attention: [], created_at: '2028-01-01'},
    {pk: 2, stage: 'billing', status: 'submitted', pm_id: '', approver_id: 8, schedule_performance: 'delayed',
        search: 'acme network sam', total: '200', delivery_start_date: '2028-02-29', attention: [{tone: 'danger'}], created_at: '2028-01-01'},
    {pk: 3, stage: 'closed', status: 'reviewed', pm_id: '8', approver_id: 3, schedule_performance: 'on_track',
        search: 'beta cloud pat', total: '', delivery_start_date: '', attention: [], created_at: '2028-01-01'},
];
function ids(filters, viewer = 8) {
    const state = engine.normalizeFilters ? engine.normalizeFilters(filters, keys).state : filters;
    return Array.from(engine.filterRecords(rows, state, viewer), row => row.pk).sort();
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }

test('OR within a dimension and AND with another dimension and all-word search', () => {
    assert.deepEqual(ids({selections: {stage: ['delivery', 'billing']}, search: 'NETWORK acme'}), [1, 2]);
    assert.deepEqual(ids({selections: {stage: ['delivery', 'billing'], status: ['submitted']}, search: 'network acme'}), [2]);
    assert.deepEqual(ids({selections: {status: []}}), [1, 2, 3]);
});

test('blank assessments and assignments have explicit sentinels; unavailable IDs do not become All', () => {
    assert.deepEqual(ids({selections: {schedule_performance: ['__not_assessed__']}}), [1]);
    assert.deepEqual(ids({selections: {pm_id: ['__unassigned__']}}), [2]);
    const normalized = engine.normalizeFilters({selections: {pm_id: ['999']}}, keys);
    assert.deepEqual(plain(normalized.state.selections.pm_id), ['999']);
    assert.deepEqual(ids(normalized.state), []);
});

test('My Assigned tests reviewer ownership, not selected delivery PM', () => {
    assert.deepEqual(ids({mine: true}), [2]);
    assert.deepEqual(ids({mine: true, selections: {pm_id: ['8']}}), []);
});

test('Engineer My Assigned uses explicit SSE rather than reviewer or form ownership', () => {
    const records = [
        {pk: 1, sse_id: '9', approver_id: 8, engineer_id: 9},
        {pk: 2, sse_id: '10', approver_id: 9, engineer_id: 9},
        {pk: 3, sse_id: '', approver_id: 9, engineer_id: 9},
    ];
    const filters = engine.normalizeFilters({mine: true}, keys).state;
    assert.deepEqual(Array.from(engine.filterRecords(records, filters, {pk: 9, is_engineer: true, is_pm: false}), row => row.pk), [1]);
    filters.selections.sse_id = ['__unassigned__'];
    assert.deepEqual(Array.from(engine.filterRecords(records, filters, {pk: 9, is_engineer: true}), row => row.pk), []);
});

test('role-aware PM matching preserves the PPT reviewer shortcut', () => {
    assert.deepEqual(ids({mine: true}, {pk: 8, is_pm: true, is_engineer: false}), [2]);
    assert.deepEqual(ids({mine: true}, '8'), [2]);
});

test('My Assigned cannot match missing viewer and assignment identities', () => {
    const filters = engine.normalizeFilters({mine: true}, keys).state;
    for (const viewer of [null, '', {}, {pk: '', is_engineer: true}]) {
        assert.deepEqual(Array.from(engine.filterRecords([{pk: 1, approver_id: '', sse_id: ''}], filters, viewer)), []);
    }
});

test('price and ISO date boundaries are inclusive and blank values never match a range', () => {
    assert.deepEqual(ids({priceMin: '100', priceMax: '200', dateRanges: {
        delivery_start_date: {from: '2028-02-01', to: '2028-02-29'},
    }}), [1, 2]);
    assert.deepEqual(ids({priceMin: '100', priceMax: '100'}), [1]);
    assert.deepEqual(ids({dateRanges: {delivery_start_date: {from: '2028-02-29', to: '2028-02-29'}}}), [2]);
});

test('normalization discards malformed saved state and retired ranges with a notice', () => {
    assert.equal(typeof engine.normalizeFilters, 'function', 'shared preference normalization is available');
    const result = engine.normalizeFilters({selections: {status: [null, {}, 'reviewed'], bogus: ['x'], pm_id: ['<script>']},
        mine: 'false', records: [{secret: 'not a preference'}], priceMin: '-1', priceMax: 'NaN', dateRanges: {
            delivery_start_date: {from: '2027-02-29', to: '2028-01-01'},
            delivery_target_finish_date: {from: '2028-01-01'},
        }}, keys);
    assert.deepEqual(plain(result.state.selections.status), ['reviewed']);
    assert.deepEqual(plain(result.state.selections.pm_id), []);
    assert.equal(result.state.mine, false);
    assert.equal(result.state.priceMin, '');
    assert.equal(result.state.priceMax, '');
    assert.equal(result.state.dateRanges.delivery_start_date.from, '');
    assert.equal(result.state.dateRanges.delivery_target_finish_date, undefined);
    assert.equal(result.state.records, undefined);
    assert.ok(result.notices.length > 0);
});

test('assessment preferences reject choices belonging to a different indicator', () => {
    const result = engine.normalizeFilters({selections: {
        schedule_performance: ['within_budget', 'delayed'],
        budget_effort_performance: ['met', 'at_risk'],
        quality_performance: ['on_track', '__not_assessed__'],
    }}, ['schedule_performance', 'budget_effort_performance', 'quality_performance']);
    assert.deepEqual(plain(result.state.selections.schedule_performance), ['delayed']);
    assert.deepEqual(plain(result.state.selections.budget_effort_performance), ['at_risk']);
    assert.deepEqual(plain(result.state.selections.quality_performance), ['__not_assessed__']);
    assert.ok(result.notices.length > 0);
});

test('repeated query values preserve OR, canonical keys win aliases and legacy mine normalizes', () => {
    assert.equal(typeof engine.fromQuery, 'function', 'shared URL preference parsing is available');
    const result = engine.fromQuery(new URLSearchParams('stage=mine&status=reviewed&status=submitted&manager=8&pm_id=999&health=on_track'), keys);
    assert.deepEqual(plain(result.state.selections.status), ['reviewed', 'submitted']);
    assert.deepEqual(plain(result.state.selections.pm_id), ['999']);
    assert.equal(result.state.mine, true);
    assert.deepEqual(plain(result.state.selections.schedule_performance), ['on_track']);
    const retired = engine.fromQuery(new URLSearchParams('health=off_track'), keys);
    assert.deepEqual(plain(retired.state.selections.schedule_performance), []);
    assert.ok(retired.notices.length > 0);
    const canonical = engine.fromQuery(new URLSearchParams('health=at_risk&schedule_performance=delayed'), keys);
    assert.deepEqual(plain(canonical.state.selections.schedule_performance), ['delayed']);
});

test('attention includes Maintenance warnings on hold without inventing lifecycle assessments', () => {
    const records = [
        {pk: 7, status: 'on_hold', attention: [{key: 'maintenance', tone: 'warning'}]},
        {pk: 8, status: 'on_hold', attention: [{key: 'hold', tone: 'muted'}]},
        {pk: 9, status: 'reviewed', attention: [{key: 'billing', tone: 'info'}]},
    ];
    const state = engine.normalizeFilters ? engine.normalizeFilters({attention: true}, keys).state : {attention: true};
    assert.deepEqual(Array.from(engine.filterRecords(records, state, 8), row => row.pk), [7]);
});
