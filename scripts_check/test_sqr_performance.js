const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor(tag = 'div', document) {
        this.tagName = tag.toUpperCase(); this.ownerDocument = document;
        this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.events = {};
        this.value = ''; this.checked = false; this.hidden = false; this.className = '';
    }
    set textContent(value) { this.replaceChildren(); this.text = String(value); }
    get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
    set innerHTML(value) { throw new Error('Unsafe HTML sink used: ' + value); }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    appendChild(child) {
        if (child.tagName === '#FRAGMENT') { child.children.slice().forEach(item => this.appendChild(item)); return child; }
        child.remove(); child.parentNode = this; this.children.push(child); return child;
    }
    replaceChildren(...children) {
        this.children.forEach(child => { child.parentNode = null; }); this.children = []; this.text = ''; this.append(...children);
    }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
    setAttribute(key, value) {
        this.attributes[key] = String(value);
        if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
    }
    getAttribute(key) {
        if (key.startsWith('data-')) return this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] ?? null;
        return this.attributes[key] ?? null;
    }
    hasAttribute(key) { return this.getAttribute(key) !== null; }
    addEventListener(key, callback) { this.events[key] = callback; }
    focus() { this.ownerDocument.activeElement = this; }
    matches(selector) {
        const tag = selector.match(/^[\w-]+/);
        if (tag && this.tagName !== tag[0].toUpperCase()) return false;
        for (const match of selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
            const value = this.getAttribute(match[1]);
            if (value === null || (match[2] !== undefined && value !== match[2])) return false;
        }
        return true;
    }
    querySelectorAll(selector) {
        const found = [];
        const visit = node => node.children.forEach(child => { if (child.matches(selector)) found.push(child); visit(child); });
        visit(this); return found;
    }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    click() { if (!this.disabled) this.events.click?.({target: this, preventDefault() {}}); }
}

function project(pk, status = 'ongoing', rating = 'good') {
    return {
        pk, project_name: 'Project ' + pk, reference_code: 'SQR-' + pk, customer_name: 'Customer',
        pm_id: '7', pm_name: 'Manager', pm_fallback: false, sse_id: '9', sse_name: 'Engineer', approver_id: 8,
        status: 'reviewed', overall_status: 'in_progress', proposal_status: 'closed_won', stage: 'delivery',
        status_key: 'in_progress', status_label: 'In Progress', scope: 'Deployment Only', group: 'Group',
        billing_type: 'milestone', billing_state: 'billed', linked_request_id: '', total: '0', progress: 25,
        delivery_start_date: '2026-10-01', delivery_actual_finish_date: null,
        post_svc_warranty_end_date: null, managed_support_start_date: null, managed_support_end_date: null,
        needs_attention: rating === 'danger', attention: rating === 'danger' ? [{tone: 'danger'}] : [],
        reasons: rating === 'danger' ? [{key: 'schedule_performance', label: 'Delayed'}] : [],
        missing_data: [], inconsistencies: [], updates_risks: '<img src=x onerror=alert(1)>',
        search_text: 'project ' + pk, portfolio_status: status, overall_rating: rating,
        overall_label: {good: 'Healthy', warning: 'At Risk', danger: 'Action needed', unknown: 'Not assessed'}[rating],
        assessed_count: rating === 'unknown' ? 0 : 3, assessment_total: 3,
        schedule_performance: {good: 'on_track', warning: 'at_risk', danger: 'delayed', unknown: ''}[rating],
        budget_effort_performance: rating === 'unknown' ? '' : 'within_budget', quality_performance: rating === 'unknown' ? '' : 'met',
        indicators: {
            schedule_performance: {state: rating, label: {good: 'On Track', warning: 'At Risk', danger: 'Delayed', unknown: 'Not assessed'}[rating]},
            budget_effort_performance: {state: rating === 'unknown' ? 'unknown' : 'good', label: rating === 'unknown' ? 'Not assessed' : 'Within Budget'},
            quality_performance: {state: rating === 'unknown' ? 'unknown' : 'good', label: rating === 'unknown' ? 'Not assessed' : 'Met'},
        },
    };
}

function snapshotResponse(projects) {
    return {ok: true, json: async () => ({
        ok: true, as_of: '2026-11-01', fetched_at: '2026-11-01T00:00:00Z', projects,
    })};
}

function dashboard(projects, {storage, search = '', responses} = {}) {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'hub', 'partials', 'sqr_performance.html'), 'utf8');
    const events = {};
    const document = {activeElement: null, getElementById: id => elements[id],
        createElement: tag => new Element(tag, document), createDocumentFragment: () => new Element('#fragment', document),
        addEventListener: (key, callback) => { events[key] = callback; }};
    const elements = {};
    for (const match of template.matchAll(/id="([^"]+)"/g)) elements[match[1]] = document.createElement('div');
    const root = elements['sqr-performance-dashboard'];
    root.hidden = true;
    root.dataset = {endpoint: '/data/', currentUserId: '7', defaultMine: '0'};
    if (elements['sqr-pm-scope']) elements['sqr-pm-scope'].value = 'all';
    const metrics = [...template.matchAll(/data-pm-metric="([^"]+)"/g)].map(match => {
        const metric = new Element();
        metric.dataset.pmMetric = match[1];
        return metric;
    });
    root.querySelectorAll = selector => selector === '[data-pm-metric]' ? metrics : [];
    const frames = []; const stored = new Map();
    let requests = 0;
    let offline = false;
    const context = {
        document, window: {location: {search}}, Intl, Date, Map, URLSearchParams,
        sessionStorage: storage || {getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value)},
        Option: class extends Element {
            constructor(text, value) { super('option', document); this.textContent = text; this.value = value; }
        },
        setTimeout, clearTimeout, AbortController,
        requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
        cancelAnimationFrame: () => {},
        fetch: async () => {
            requests++; if (offline) throw new Error('Offline');
            return responses ? responses.shift()() : snapshotResponse(projects);
        },
    };
    vm.createContext(context);
    ['sqr-filter-core.js', 'sqr-filter-controls.js', 'sqr-performance.js'].forEach(name => {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', name), 'utf8'), context);
    });
    return {
        elements, metrics, stored, document, template, requests: () => requests,
        button(label) {
            const button = elements['sqr-pm-filters']?.querySelectorAll('button').find(node => node.textContent === label);
            assert.ok(button, 'shared filter button: ' + label); button.click(); this.flush();
        },
        select(key, label, values) {
            this.button(label);
            const inputs = elements['sqr-pm-filters'].querySelectorAll('input').filter(node => node.getAttribute('data-filter-focus')?.startsWith('option:' + key + ':'));
            values.forEach(value => assert.ok(inputs.some(node => node.value === value), 'filter option: ' + value));
            inputs.forEach(node => { node.checked = values.includes(node.value); node.events.change(); });
            this.button('Apply ' + label);
        },
        setOffline(value) { offline = value; },
        hide() {
            root.hidden = true;
            events['sqr:performance-view']({detail: {view: 'prototype'}});
        },
        metric: key => metrics.find(metric => metric.dataset.pmMetric === key)?.textContent,
        async open() {
            root.hidden = false;
            events['sqr:performance-view']({detail: {view: 'performance'}});
            await new Promise(resolve => setImmediate(resolve));
            this.flush();
        },
        async invalidate() {
            events['sqr:performance-invalidated']();
            await new Promise(resolve => setImmediate(resolve));
            this.flush();
        },
        async retry() {
            const button = elements['sqr-pm-message'].children.find(child => child.dataset.pmRetry);
            assert.ok(button, 'retry button available');
            root.events.click({target: {closest: () => button}});
            await new Promise(resolve => setImmediate(resolve));
            this.flush();
        },
        flush() { while (frames.length) frames.shift()(); },
    };
}

test('portfolio remains lazy and saved assessment summaries include terminal and pipeline projects', async () => {
    const app = dashboard([
        project(1, 'closed'), project(2), project(3, 'pipeline', 'unknown'), project(4, 'removed', 'unknown'),
        project(5, 'ongoing', 'danger'),
    ]);
    for (const id of ['sqr-pm-count', 'sqr-pm-refresh', 'sqr-pm-snapshot']) {
        assert.equal(app.elements[id], undefined, id);
    }
    assert.equal(app.requests(), 0);
    await app.open();
    assert.equal(app.requests(), 1);
    assert.match(app.elements['sqr-pm-as-of'].textContent, /^AS OF .*2026/);
    assert.equal(app.metric('total'), '5');
    assert.equal(app.metric('closed'), '1');
    assert.equal(app.metric('ongoing'), '2');
    assert.equal(app.metric('pipeline'), '1');
    assert.equal(app.metric('removed'), '1');
    assert.equal(app.metric('rating_good'), '2');
    assert.equal(app.metric('rating_danger'), '1');
    assert.equal(app.metric('rating_unknown'), '2');
    app.select('overall_rating', 'Overall Rating', ['danger']);
    assert.equal(app.metric('total'), '1');
    assert.equal(app.metric('rating_danger'), '1');
    app.button('Clear all');
    app.select('portfolio_status', 'Portfolio Status', ['pipeline']);
    assert.equal(app.metric('total'), '1');
    assert.equal(app.metric('pipeline'), '1');
});

test('reference charts and tables show project details without repeated action buttons', async () => {
    const app = dashboard([project(1, 'ongoing', 'danger')]);
    await app.open();
    for (const id of ['sqr-pm-manager-chart', 'sqr-pm-status-queue', 'sqr-pm-rating-queue', 'sqr-pm-indicator-chart', 'sqr-pm-status-rows', 'sqr-pm-indicator-rows']) {
        assert.ok(app.elements[id].children.length > 0, id);
        assert.ok(!app.elements[id].textContent.includes('View in Tracker'), id);
    }
    assert.ok(app.elements['sqr-pm-status-rows'].textContent.includes('Project 1'));
    assert.ok(app.elements['sqr-pm-indicator-rows'].textContent.includes('Delayed'));
    assert.ok(app.elements['sqr-pm-indicator-rows'].textContent.includes('Within Budget'));
    assert.equal(app.elements['sqr-pm-directory'], undefined);
    assert.equal(app.elements['sqr-pm-queue'], undefined);
});

test('automatic refresh failure keeps the last snapshot and retry restores it', async () => {
    const projects = [project(1)];
    const app = dashboard(projects);
    await app.open();
    app.setOffline(true);
    await app.invalidate();
    assert.equal(app.metric('total'), '1');
    assert.equal(app.elements['sqr-pm-message'].dataset.state, 'stale');
    assert.match(app.elements['sqr-pm-message'].textContent, /Refresh failed/);
    assert.match(app.elements['sqr-pm-message'].textContent, /Retry/);
    projects.push(project(2));
    app.setOffline(false);
    await app.retry();
    assert.equal(app.requests(), 3);
    assert.equal(app.elements['sqr-pm-message'].dataset.state, '');
    assert.equal(app.metric('total'), '2');
});

test('initial load failure offers retry without a refresh button', async () => {
    const app = dashboard([project(1)]);
    app.setOffline(true);
    await app.open();
    assert.equal(app.elements['sqr-pm-message'].dataset.state, 'error');
    assert.match(app.elements['sqr-pm-message'].textContent, /Retry/);
    app.setOffline(false);
    await app.retry();
    assert.equal(app.requests(), 2);
    assert.equal(app.elements['sqr-pm-message'].dataset.state, '');
    assert.equal(app.metric('total'), '1');
});

test('tracker changes automatically refresh the portfolio without a refresh button', async () => {
    const projects = [project(1)];
    const app = dashboard(projects);
    await app.open();
    projects.push(project(2));
    await app.invalidate();
    assert.equal(app.requests(), 2);
    assert.equal(app.metric('total'), '2');
});

test('tracker renders untrusted text safely, paginates both tables and recomputes filtered charts', async () => {
    const projects = Array.from({length: 25}, (_, index) => project(index + 1));
    projects[0].project_name = '<script>alert(1)</script>';
    projects[0].search_text = 'needle';
    const app = dashboard(projects);
    await app.open();
    assert.ok(app.elements['sqr-pm-status-rows'].textContent.includes('<script>alert(1)</script>'));
    assert.ok(app.elements['sqr-pm-status-rows'].textContent.includes('<img src=x onerror=alert(1)>'));
    assert.ok(app.elements['sqr-pm-indicator-rows'].textContent.includes('On Track'));
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 1 of 2/);
    app.elements['sqr-pm-next'].events.click();
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 2 of 2/);
    assert.ok(app.elements['sqr-pm-status-rows'].textContent.includes('Project 25'));
    assert.ok(!app.elements['sqr-pm-status-rows'].textContent.includes('Project 24'));
    const input = app.elements['sqr-pm-search'];
    input.value = 'no matches'; input.events.input();
    await new Promise(resolve => setTimeout(resolve, 140)); app.flush();
    assert.equal(app.metric('total'), '0');
    assert.ok(app.elements['sqr-pm-empty'].hidden === false);
    assert.ok(!app.elements['sqr-pm-manager-chart'].textContent.includes('Manager'));
    app.button('Clear all');
    assert.equal(app.metric('total'), '25');
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 1 of 2/);
});

test('PPT shows exactly Schedule Budget-Effort Quality and saved coverage', async () => {
    const app = dashboard([project(1)]);
    assert.match(app.template, /<th scope="col">Budget\/Effort<\/th>/);
    assert.doesNotMatch(app.template, /Start \/ target|Recorded delivery health|<th scope="col">Resources/);
    await app.open();
    const chart = app.elements['sqr-pm-indicator-chart'];
    assert.equal(chart.children.length, 3);
    assert.deepEqual(chart.children.map(row => row.children[0].textContent), ['Schedule', 'Budget/Effort', 'Quality']);
    const cells = app.elements['sqr-pm-indicator-rows'].children[0].children;
    assert.deepEqual(cells.slice(1).map(cell => cell.textContent), ['On Track', 'Within Budget', 'Met', 'Healthy', '3/3']);
    assert.match(app.elements['sqr-pm-status-rows'].textContent, /Customer/);
});

test('partial ratings preserve gray and coverage without fabricated assessments', async () => {
    const partial = project(1, 'closed', 'unknown');
    partial.schedule_performance = 'on_track'; partial.assessed_count = 1; partial.overall_label = 'Incomplete assessment';
    partial.indicators.schedule_performance = {state: 'good', label: 'On Track'};
    const app = dashboard([partial]); await app.open();
    const cells = app.elements['sqr-pm-indicator-rows'].children[0].children;
    assert.deepEqual(cells.slice(1).map(cell => cell.textContent), ['On Track', 'Not assessed', 'Not assessed', 'Incomplete assessment', '1/3']);
    assert.match(cells[2].className, /rating--unknown/);
    assert.match(cells[3].className, /rating--unknown/);
    assert.equal(app.metric('rating_unknown'), '1'); assert.equal(app.metric('rating_good'), '0');
});

test('multi-filters and charts use all matches before pagination and Mine uses reviewer', async () => {
    const projects = Array.from({length: 30}, (_, index) => Object.assign(project(index + 1), {
        approver_id: 7, pm_id: index % 2 ? '8' : '9', pm_name: index % 2 ? 'Owner eight' : 'Owner nine',
        scope: index < 26 ? 'Deployment Only' : 'Managed Services',
    }));
    projects.push(Object.assign(project(31), {pm_id: '7', approver_id: 8}));
    const app = dashboard(projects); await app.open();
    app.select('pm_id', 'Project Manager', ['8', '9']);
    app.select('scope', 'Scope', ['Deployment Only']);
    app.button('My Assigned');
    assert.equal(app.metric('total'), '26'); assert.equal(app.metric('rating_good'), '26');
    assert.equal(app.elements['sqr-pm-status-rows'].children.length, 24);
    assert.match(app.elements['sqr-pm-manager-chart'].textContent, /Owner eight13 projects/);
    assert.match(app.elements['sqr-pm-manager-chart'].textContent, /Owner nine13 projects/);
    assert.match(app.elements['sqr-pm-filters'].textContent, /26 matching records/);
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 1 of 2/);
    assert.equal(app.requests(), 1);
});

test('SQR invalidation refreshes saved values and unavailable selections remain visible', async () => {
    const projects = [project(1)]; const app = dashboard(projects); await app.open();
    app.select('schedule_performance', 'Schedule Performance', ['on_track']);
    projects[0] = project(1, 'ongoing', 'danger'); await app.invalidate();
    assert.equal(app.metric('total'), '0');
    assert.match(app.elements['sqr-pm-filters'].textContent, /On Track \(0\)/);
    app.button('Clear all');
    assert.equal(app.metric('rating_danger'), '1');
    assert.match(app.elements['sqr-pm-indicator-rows'].textContent, /Delayed/);
    assert.match(app.elements['sqr-pm-rating-queue'].textContent, /Action needed/);
});

test('PPT preferences are separate, canonical URL filters override saved state and aliases migrate', async () => {
    const stored = new Map([['requestHub.sqr.ppt.filters.v1', JSON.stringify({selections: {pm_id: ['9']}, search: 'ignored'})]]);
    const storage = {getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value)};
    const app = dashboard([project(1)], {storage, search: '?manager=9&pm_id=7&health=on_track'}); await app.open();
    assert.equal(app.metric('total'), '1');
    assert.match(app.elements['sqr-pm-filters'].textContent, /Remove Schedule Performance: On Track/);
    app.button('My Assigned');
    const saved = JSON.parse(stored.get('requestHub.sqr.ppt.filters.v1'));
    assert.deepEqual(saved.selections.pm_id, ['7']); assert.equal(saved.mine, true);
    assert.equal(saved.projects, undefined); assert.equal(stored.has('requestHub.sqr.prototype.filters.v1'), false);
});

test('legacy PPT scope and reason links remain usable with explicit retirement notices', async () => {
    for (const scope of ['all', 'active']) {
        const app = dashboard([project(1), project(2, 'ongoing', 'danger')], {search: '?scope=' + scope + '&attention=missing'});
        await app.open();
        assert.equal(app.metric('total'), '1');
        assert.match(app.elements['sqr-pm-filter-notice'].textContent, /Legacy.*scope/i);
        assert.match(app.elements['sqr-pm-filter-notice'].textContent, /reason.*Needs attention/i);
        assert.doesNotMatch(app.elements['sqr-pm-filters'].textContent, /Unavailable: (all|active)/);
    }
    const app = dashboard([project(1)], {search: '?scope=Deployment+Only&attention=false'});
    await app.open(); assert.equal(app.metric('total'), '1');
    assert.equal(app.elements['sqr-pm-filter-notice'].textContent, '');
});

test('in-flight invalidation discards stale assessments and refetches once', async () => {
    let resolve;
    const first = new Promise(done => { resolve = done; });
    const app = dashboard([], {responses: [() => first, () => snapshotResponse([project(2, 'ongoing', 'danger')])]});
    await app.open(); await app.invalidate(); await app.invalidate();
    assert.equal(app.requests(), 1);
    resolve(snapshotResponse([project(1)]));
    await new Promise(done => setImmediate(done)); app.flush();
    assert.equal(app.requests(), 2); assert.equal(app.metric('rating_danger'), '1');
    assert.doesNotMatch(app.elements['sqr-pm-status-rows'].textContent, /Project 1/);
});

test('hidden in-flight invalidation waits for return without rendering stale data', async () => {
    let resolve;
    const first = new Promise(done => { resolve = done; });
    const app = dashboard([], {responses: [() => first, () => snapshotResponse([project(2)])]});
    await app.open(); app.hide(); await app.invalidate();
    resolve(snapshotResponse([project(1)]));
    await new Promise(done => setImmediate(done)); app.flush();
    assert.equal(app.requests(), 1); assert.equal(app.elements['sqr-pm-status-rows'].textContent, '');
    await app.open();
    assert.equal(app.requests(), 2); assert.match(app.elements['sqr-pm-status-rows'].textContent, /Project 2/);
});

test('blocked preference storage is visible and nonfatal; shared range filters accept zero', async () => {
    const app = dashboard([project(1)], {storage: {getItem() { throw new Error('Blocked'); }, setItem() { throw new Error('Blocked'); }}});
    await app.open(); assert.equal(app.metric('total'), '1');
    assert.match(app.elements['sqr-pm-filter-notice'].textContent, /preferences/i);
    app.button('Price and dates');
    const inputs = app.elements['sqr-pm-filters'].querySelectorAll('input');
    const price = inputs.find(node => node.getAttribute('aria-label') === 'Maximum price'); price.value = '0';
    app.button('Apply ranges'); assert.equal(app.metric('total'), '1');
    app.button('Needs attention'); assert.equal(app.metric('total'), '0');
});
