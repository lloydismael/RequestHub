const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Only browser/network boundaries are mocked; filters, rendering and editing run unchanged.
class Element {
    constructor(tag, document, text) {
        this.tagName = tag.toUpperCase(); this.nodeType = tag === '#text' ? 3 : 1;
        this.ownerDocument = document; this.children = []; this.dataset = new Proxy({}, {set(target, key, value) { target[key] = String(value); return true; }}); this.attributes = {};
        this.listeners = {}; this.hidden = false; this.value = ''; this.style = {}; this.className = ''; this._text = text || '';
    }
    get options() { return this.children.filter(child => child.tagName === 'OPTION'); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._text = String(value); this.children = []; }
    set innerHTML(value) { throw new Error('HTML parsing is forbidden in tracker output'); }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    appendChild(child) {
        if (typeof child !== 'object') child = this.ownerDocument.createTextNode(String(child));
        child.remove(); child.parentNode = this; this.children.push(child); return child;
    }
    replaceChildren(...children) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = ''; this.append(...children); }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
    setAttribute(key, value) {
        this.attributes[key] = String(value);
        if (key === 'id') this.id = String(value);
        if (key === 'class') this.className = String(value);
        if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
    }
    getAttribute(key) {
        if (key === 'class') return this.className;
        if (key.startsWith('data-')) return this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] ?? null;
        return this.attributes[key] ?? null;
    }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(key, callback) { (this.listeners[key] ||= []).push(callback); }
    dispatchEvent(event) { event.target ||= this; event.currentTarget = this; (this.listeners[event.type] || []).forEach(callback => callback(event)); }
    click() { if (!this.disabled) this.dispatchEvent({type: 'click', preventDefault() {}}); }
    focus() { this.ownerDocument.activeElement = this; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    matches(selector) {
        const tag = selector.match(/^[\w-]+/);
        if (tag && this.tagName !== tag[0].toUpperCase()) return false;
        for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!this.className.split(' ').includes(match[1])) return false;
        for (const match of selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
            const value = this.getAttribute(match[1]);
            if (value === null || (match[2] !== undefined && value !== match[2])) return false;
        }
        return true;
    }
    querySelectorAll(selector) {
        const selectors = selector.split(',').map(value => value.trim()); const found = [];
        const visit = node => node.children.forEach(child => { if (selectors.some(value => child.matches(value))) found.push(child); visit(child); });
        visit(this); return found;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    scrollIntoView() {}
}
function fixture(pk, overrides = {}) {
    return Object.assign({pk, ref: `SQR-${pk}`, account: 'Customer', service: 'Project', search: 'customer project', created_at: '2026-01-01',
        stage: 'delivery', status: 'reviewed', proposal_status: 'closed_won', overall_status: 'in_progress', total: '100', progress: 25,
        assigned_pm_name: 'PM', assigned_sse_name: 'SSE', approver: 'Reviewer', approver_id: 8,
        attention: [], editable: [], locked: false, values: {assigned_sse: '9', delivery_start_date: '2026-01-01', overall_status: 'in_progress', delivery_progress: '25'},
        next_step: {title: 'Update Delivery', action: '', tone: 'info'}, perms: {edit_form: false, delete: false, history: false},
        urls: {inline: '/inline/', history: '/history/', edit: '', delete: ''}}, overrides);
}
function tracker(records = [fixture(1)], {storage, search = '', viewer = {pk: 8, is_pm: true, is_engineer: false}} = {}) {
    const document = {listeners: {}, createElement(tag) { return new Element(tag, this); }, createTextNode(text) { return new Element('#text', this, text); },
        getElementById(id) { return this.body.querySelectorAll('[id]').find(node => node.id === id) || null; },
        addEventListener(key, callback) { (this.listeners[key] ||= []).push(callback); }, dispatchEvent(event) { (this.listeners[event.type] || []).forEach(callback => callback(event)); }};
    document.body = document.createElement('body'); document.activeElement = document.body;
    const root = document.createElement('section'); root.setAttribute('id', 'sqr-proto'); root.hidden = true; root.dataset.endpoint = '/data/'; document.body.append(root);
    const ids = ['pipeline', 'body', 'items', 'more', 'editor', 'search', 'filters', 'filter-notice', 'toast', 'toast-text', 'toast-undo', 'toast-close',
        'approve', 'revise', 'revise-notes', 'revise-error', 'table', 'list-view', 'table-view', 'date-columns', 'result-notice', 'sort'];
    ids.forEach(id => { const node = document.createElement(id === 'search' ? 'input' : id.endsWith('view') ? 'button' : 'div'); node.setAttribute('id', 'sqr-proto-' + id); root.append(node); });
    const el = id => document.getElementById('sqr-proto-' + id);
    const requests = []; const timers = []; const stored = new Map();
    const context = {document, console, URLSearchParams, AbortController, Intl, Map, CustomEvent: class { constructor(type) { this.type = type; } },
        setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {},
        sessionStorage: storage || {getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value)},
        localStorage: {setItem() {}}, fetch: async (url, options) => {
            requests.push({url, options}); const response = await app.respond(url, options);
            return {ok: response.ok !== false, status: response.status || 200, json: async () => response.data};
        }};
    context.window = {location: {search, reload() {}}, matchMedia: () => ({matches: false}), setTimeout: context.setTimeout};
    vm.createContext(context);
    const source = name => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', name), 'utf8');
    vm.runInContext(source('sqr-filter-core.js'), context);
    vm.runInContext(source('sqr-filter-controls.js'), context);
    const script = source('sqr-tracker-prototype.js').replace(/\}\)\(\);\s*$/, `window.__tracker = {state, apply, select, save, refreshRecord, load, visibleRecords, renderAll, filterDimensions, todoItems, renderSummary,
        setPresentation: typeof setPresentation === 'function' ? setPresentation : null};})();`);
    vm.runInContext(script, context);
    const app = Object.assign(context.window.__tracker, {el, document, context, requests, timers, stored, respond: () => ({data: {record: records[0]}})});
    app.payload = rows => ({viewer, options: {}, records: rows, schema: {fields: [], tabs: [], sections: [], stages: [['opportunity', 'Opportunity'], ['delivery', 'Delivery'], ['billing', 'Billing'], ['closed', 'Closed']]}});
    app.apply(app.payload(records)); return app;
}
function ids(app, mode) { return app.el(mode === 'table' ? 'table' : 'items').querySelectorAll(mode === 'table' ? 'tr[data-pk]' : '.sqr-proto__card').map(node => Number(node.dataset.pk)); }
function button(app, label) { return app.el('table').querySelectorAll('button').find(node => node.textContent.trim() === label); }

// Catches separate view state, inconsistent sort order and re-fetching on a presentation switch.
test('switching views preserves filtered IDs selection and sort', () => {
    const app = tracker([fixture(1, {account: 'Zulu'}), fixture(2, {account: 'Alpha'}), fixture(3, {status: 'submitted'})]);
    app.state.filters.selections.status = ['reviewed']; app.select(1);
    assert.equal(typeof app.setPresentation, 'function', 'Tracker must offer List and Table');
    app.setPresentation('table'); button(app, 'Customer').click();
    assert.deepEqual(ids(app, 'table'), [2, 1]);
    app.setPresentation('list'); assert.deepEqual(ids(app, 'list'), [2, 1]);
    app.setPresentation('table'); assert.deepEqual(ids(app, 'table'), [2, 1]);
    assert.equal(app.state.selectedPk, 1); assert.equal(app.requests.length, 0);
});

test('counts include results beyond page', () => {
    const app = tracker(Array.from({length: 65}, (_, i) => fixture(i + 1)));
    app.el('table-view').click();
    assert.equal(ids(app, 'table').length, 50);
    assert.match(app.el('filters').textContent, /65/);
    app.el('more').click(); assert.equal(ids(app, 'table').length, 65);
});

test('Engineer Mine keeps List Table pagination pipeline and facets on the same SSE population', () => {
    const records = Array.from({length: 55}, (_, i) => fixture(i + 1, {sse_id: '9', approver_id: 8}));
    records.push(fixture(56, {sse_id: '10', approver_id: 9, status: 'submitted'}), fixture(57, {sse_id: '', approver_id: 9}));
    const app = tracker(records, {viewer: {pk: 9, is_pm: false, is_engineer: true}});
    const mine = app.el('pipeline').querySelector('[data-stage="mine"]');
    assert.ok(mine, 'Engineer has the same personal shortcut in the pipeline');
    assert.equal(app.state.pipeline.mine.count, 55);
    mine.click();
    assert.equal(app.state.filters.mine, true);
    assert.equal(app.visibleRecords().length, 55);
    assert.equal(ids(app, 'list').length, 50);
    app.setPresentation('table'); assert.equal(ids(app, 'table').length, 50);
    app.el('more').click(); assert.deepEqual(ids(app, 'table'), Array.from({length: 55}, (_, i) => 55 - i));
    app.setPresentation('list'); assert.deepEqual(ids(app, 'list'), Array.from({length: 55}, (_, i) => 55 - i));
    assert.equal(app.state.pipeline.all.count, 55);
    const status = app.filterDimensions().find(dim => dim.key === 'status');
    assert.equal(status.options.find(option => option.value === 'reviewed').count, 55);
    assert.equal(status.options.find(option => option.value === 'submitted').count, 0);
    assert.match(app.el('filters').textContent, /55/);
});

test('Engineer Mine refresh notices follow reassignment without detaching the editor', async () => {
    const own = fixture(1, {sse_id: '9', approver_id: 8});
    const app = tracker([own], {viewer: {pk: 9, is_pm: false, is_engineer: true}});
    app.state.filters.mine = true; app.select(1);
    app.el('result-notice').textContent = 'Stale notice'; app.el('result-notice').hidden = false;
    app.respond = () => ({data: {record: own}});
    await app.refreshRecord(1);
    assert.equal(app.el('result-notice').hidden, true);
    assert.deepEqual(ids(app, 'list'), [1]);
    app.respond = () => ({data: {record: fixture(1, {sse_id: '10', approver_id: 9})}});
    await app.refreshRecord(1);
    assert.deepEqual(ids(app, 'list'), []);
    assert.match(app.el('result-notice').textContent, /no longer matches.*filters/i);
    assert.equal(app.state.selectedPk, 1);
    assert.equal(app.state.pipeline.mine.count, 0);
    app.setPresentation('table'); assert.deepEqual(ids(app, 'table'), []);
});

test('saved row leaving filters refetches and notifies without losing Undo', async () => {
    const app = tracker(Array.from({length: 51}, (_, i) => fixture(i + 1)));
    app.state.filters.selections.status = ['reviewed']; app.state.shown = 100; app.select(1);
    app.respond = url => url === '/inline/' ? {data: {undo_url: '/undo/'}} : {data: {record: fixture(1, {status: 'submitted'})}};
    await app.save(1, 'status', 'submitted', null);
    assert.equal(app.requests.length, 2); assert.equal(app.requests[1].url, '/data/?pk=1');
    assert.equal(app.visibleRecords().length, 50); assert.equal(app.state.shown, 50);
    assert.match(app.el('result-notice').textContent, /no longer matches.*filters/i);
    assert.equal(app.el('toast-undo').hidden, false);
});

test('refresh removing selected row is recoverable and cannot save detached data', async () => {
    const app = tracker(); app.select(1); app.respond = () => ({ok: false, status: 404, data: {error: 'Not found.'}});
    await app.refreshRecord(1);
    assert.equal(app.state.selectedPk, null); assert.equal(app.state.byPk[1], undefined);
    assert.equal(app.el('editor').querySelectorAll('[data-field-key]').length, 0);
    assert.doesNotMatch(app.el('editor').textContent, /Customer/);
    assert.match(app.el('result-notice').textContent, /no longer available/i);
    await app.save(1, 'status', 'reviewed', null); assert.equal(app.requests.length, 1);
});

test('full reload does not resurrect a missing selected record', () => {
    const app = tracker(); app.select(1); app.apply(app.payload([]));
    assert.equal(app.state.selectedPk, null); assert.equal(app.state.byPk[1], undefined);
    assert.doesNotMatch(app.el('editor').textContent, /Customer/);
});

test('search changes clear an obsolete filtered-out notice', () => {
    const app = tracker();
    app.el('result-notice').textContent = 'Saved SQR no longer matches the current filters.';
    app.el('result-notice').hidden = false;
    app.el('search').value = 'SQR'; app.el('search').dispatchEvent({type: 'input'});
    app.timers.pop()();
    assert.equal(app.el('result-notice').hidden, true);
    assert.equal(app.el('result-notice').textContent, '');
});

test('desktop Table Open moves focus to the below-table editor', () => {
    const app = tracker(); app.el('table-view').click();
    const open = app.el('table').querySelector('tbody').querySelector('button');
    open.focus(); open.click();
    assert.ok(app.document.activeElement === app.el('editor').querySelector('.sqr-proto__ed-title'), 'Open must focus the editor heading');
    assert.equal(app.state.presentation, 'table');
});

test('storage failure keeps tracker usable', () => {
    const app = tracker([fixture(1)], {storage: {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }}});
    app.el('table-view').click(); assert.deepEqual(ids(app, 'table'), [1]);
    assert.match(app.el('filter-notice').textContent, /could not/);
    app.el('list-view').click(); assert.deepEqual(ids(app, 'list'), [1]);
});

test('table Open obeys record permissions', () => {
    const app = tracker([fixture(1, {perms: {edit_form: true}, urls: {edit: '/allowed/edit/'}}), fixture(2, {urls: {edit: '/must-not-use/'}})]);
    app.el('table-view').click();
    const rows = app.el('table').querySelectorAll('tr[data-pk]');
    const owned = rows.find(row => row.dataset.pk === '1'); const other = rows.find(row => row.dataset.pk === '2');
    assert.equal(owned.querySelector('a').getAttribute('href'), '/allowed/edit/');
    assert.equal(other.querySelector('a'), null);
    other.querySelector('button').click(); assert.equal(app.state.selectedPk, 2);
});

test('sortable headers and empty table are accessible', () => {
    const app = tracker(); app.el('table-view').click();
    assert.ok(app.el('table').querySelector('caption'));
    const header = app.el('table').querySelectorAll('th').find(node => node.textContent.trim() === 'Customer');
    assert.ok(header, 'Table has a sortable Customer column'); assert.equal(header.getAttribute('scope'), 'col');
    header.querySelector('button').click();
    assert.equal(app.el('table').querySelectorAll('th').find(node => node.textContent.trim().startsWith('Customer')).getAttribute('aria-sort'), 'ascending');
    const open = app.el('table').querySelector('tbody').querySelector('button'); assert.match(open.getAttribute('aria-label'), /Open.*SQR-1/);
    app.state.filters.search = 'absent'; app.renderAll(); assert.match(app.el('table').textContent, /No matches/);
});

test('Summary and action items use recorded assessments and Finish, not retired fields', () => {
    const rec = fixture(1, {delivery_health: 'completed', schedule_performance: 'delayed', budget_effort_performance: 'within_budget', quality_performance: '',
        delivery_actual_finish_date: '2026-06-01', post_svc_warranty_end_date: '2026-07-01'});
    const app = tracker([rec]); app.select(1);
    const text = app.el('editor').textContent;
    assert.match(text, /Schedule Performance/); assert.match(text, /Delayed/); assert.match(text, /Budget\/Effort Performance/); assert.match(text, /Not assessed/);
    assert.doesNotMatch(text, /Target finish|target finish date|Completion signed|health status/);
    assert.ok(!app.todoItems(rec).some(item => /delivery_health|target_finish/.test(item.key)));
    assert.ok(!app.todoItems(fixture(1, {stage: 'billing'})).some(item => item.key === 'delivery_completion_signed_date'));
});

// Catches omission of independent assessments from List cards, including blanks.
test('List cards label each recorded assessment and keep blanks neutral', () => {
    const app = tracker([fixture(1, {schedule_performance: 'on_track', budget_effort_performance: 'at_risk', quality_performance: 'unmet'}), fixture(2)]);
    const cards = app.el('items').querySelectorAll('.sqr-proto__card');
    const rated = cards.find(card => card.dataset.pk === '1');
    const badges = rated.querySelectorAll('.sqr-proto__badge');
    for (const [label, tone] of [['Schedule Performance: On Track', 'success'], ['Budget/Effort Performance: At Risk', 'warning'], ['Quality Performance: Unmet', 'danger']]) {
        assert.equal(badges.find(node => node.textContent === label)?.dataset.tone, tone);
    }
    const blank = cards.find(card => card.dataset.pk === '2');
    assert.equal(blank.querySelectorAll('.sqr-proto__badge').filter(node => node.textContent.endsWith(': Not assessed') && node.dataset.tone === 'muted').length, 3);
});

// Catches plain-text Delivery ratings and stale badges after authoritative refresh.
test('Delivery assessment badges support editing readonly blanks and refresh without losing focus', async () => {
    const rec = fixture(1, {schedule_performance: 'on_track', budget_effort_performance: 'at_risk', quality_performance: '', editable: ['schedule_performance'],
        values: {schedule_performance: 'on_track', budget_effort_performance: 'at_risk', quality_performance: ''}});
    const app = tracker([rec]);
    const payload = app.payload([rec]);
    payload.schema.tabs = [['delivery', 'Delivery']];
    payload.schema.fields = [
        {key: 'schedule_performance', label: 'Schedule performance', choices: [['on_track', 'On Track'], ['delayed', 'Delayed']]},
        {key: 'budget_effort_performance', label: 'Budget/Effort performance', choices: [['at_risk', 'At Risk']]},
        {key: 'quality_performance', label: 'Quality performance', choices: [['met', 'Met']]}
    ].map(spec => ({...spec, type: 'select', tab: 'delivery', section: 'Progress'}));
    app.apply(payload); app.select(1);
    app.el('editor').querySelectorAll('[role="tab"]').find(node => node.textContent.includes('Delivery')).click();
    const field = key => app.el('editor').querySelector('[data-field-key="' + key + '"]');
    const rating = key => field(key).querySelector('.sqr-proto__badge');
    assert.equal(rating('schedule_performance')?.textContent, 'On Track');
    assert.equal(rating('schedule_performance')?.dataset.tone, 'success');
    assert.equal(rating('budget_effort_performance')?.dataset.tone, 'warning');
    assert.equal(rating('quality_performance')?.textContent, 'Not assessed');
    assert.equal(rating('quality_performance')?.dataset.tone, 'muted');
    const control = field('schedule_performance').querySelector('select'); control.focus();
    app.respond = () => ({data: {record: {...rec, schedule_performance: 'delayed', values: {...rec.values, schedule_performance: 'delayed'}}}});
    await app.refreshRecord(1);
    assert.equal(rating('schedule_performance')?.textContent, 'Delayed');
    assert.equal(rating('schedule_performance')?.dataset.tone, 'danger');
    assert.equal(field('schedule_performance').querySelector('select'), control);
    assert.equal(app.document.activeElement, control);
    app.setPresentation('table');
    assert.match(app.el('table').textContent, /Delayed/);
    app.setPresentation('list');
    assert.match(app.el('items').textContent, /Schedule Performance: Delayed/);
    control.value = 'delayed'; control.focus();
    app.apply({...payload, records: [{...rec, budget_effort_performance: '', values: {...rec.values, budget_effort_performance: ''}}]});
    assert.equal(rating('schedule_performance')?.textContent, 'On Track');
    assert.equal(rating('budget_effort_performance')?.textContent, 'Not assessed');
    assert.equal(rating('budget_effort_performance')?.dataset.tone, 'muted');
    assert.equal(field('schedule_performance').querySelector('select'), control);
    assert.equal(app.document.activeElement, control);
    assert.equal(control.value, 'delayed');
});

test('optional date columns use authoritative computed dates and persist with presentation', () => {
    const app = tracker([fixture(1, {post_svc_warranty_end_date: '2026-07-01', managed_support_start_date: '2026-07-02'})]);
    app.el('table-view').click();
    const choices = app.el('date-columns').querySelectorAll('input'); assert.equal(choices.length, 5);
    choices[2].checked = true; choices[2].dispatchEvent({type: 'change'});
    assert.match(app.el('table').textContent, /Warranty/); assert.match(app.el('table').textContent, /2026/);
    const saved = [...app.stored.values()].map(value => JSON.parse(value)).find(value => value.presentation);
    assert.equal(saved.presentation, 'table'); assert.ok(saved.dateColumns.includes('post_svc_warranty_end_date'));
    assert.equal(saved.records, undefined);
});

test('Table shares initial load errors and retry instead of silently appearing empty', async () => {
    const app = tracker(); app.state.loaded = false; app.respond = () => ({ok: false, data: {error: 'Unavailable'}});
    await app.load(); app.el('table-view').click(); assert.match(app.el('table').textContent, /Unavailable/);
    const retry = button(app, 'Try again'); assert.ok(retry);
    app.respond = () => ({data: app.payload([fixture(2)])}); retry.click(); await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(ids(app, 'table'), [2]);
});
