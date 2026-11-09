const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag;
        this.children = [];
        this.dataset = {};
        this.style = {};
        this.attributes = {};
        this.events = {};
        this.value = '';
        this.checked = false;
        this.hidden = false;
        this.className = '';
        this.classList = {contains: name => this.className.split(' ').includes(name)};
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
    set innerHTML(value) { throw new Error('Unsafe HTML sink used: ' + value); }
    append(...children) { this.children.push(...children); }
    prepend(...children) { this.children.unshift(...children); }
    replaceChildren(...children) { this.text = ''; this.children = children; }
    setAttribute(key, value) { this.attributes[key] = value; }
    hasAttribute(key) { return Object.hasOwn(this.attributes, key); }
    addEventListener(key, callback) { this.events[key] = callback; }
    add(option) { this.append(option); }
    focus() {}
}

function project(pk, status = 'ongoing', rating = 'good') {
    return {
        pk, project_name: 'Project ' + pk, reference_code: 'SQR-' + pk, customer_name: 'Customer',
        pm_id: '7', pm_name: 'Manager', pm_fallback: false, sse_name: 'Engineer',
        status_key: 'in_progress', status_label: 'In Progress',
        health_key: 'on_track', health_label: 'On Track', progress: 25,
        start_date: '2026-10-01', target_date: '2026-12-01', actual_finish_date: null,
        completion_signed_date: null, active_delivery: status === 'ongoing', days_to_target: 30,
        needs_attention: rating === 'danger', overdue: false, due_soon: false,
        reasons: rating === 'danger' ? [{key: 'off_track', label: 'Off Track'}] : [],
        missing_data: [], inconsistencies: [], updates_risks: '<img src=x onerror=alert(1)>',
        search_text: 'project ' + pk, portfolio_status: status, overall_rating: rating,
        indicators: Object.fromEntries(['schedule', 'scope', 'resources', 'customer', 'risks'].map(key => [
            key, {state: key === 'schedule' ? rating : 'unknown', label: key === 'schedule' ? 'On target' : 'N/R'}
        ])),
    };
}

function dashboard(projects) {
    const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'hub', 'partials', 'sqr_performance.html'), 'utf8');
    const elements = {};
    for (const match of template.matchAll(/id="([^"]+)"/g)) elements[match[1]] = new Element();
    const root = elements['sqr-performance-dashboard'];
    root.hidden = true;
    root.dataset = {endpoint: '/data/', currentUserId: '7', defaultMine: '0'};
    elements['sqr-pm-scope'].value = template.includes('<option value="all" selected>') ? 'all' : 'active';
    const metrics = [...template.matchAll(/data-pm-metric="([^"]+)"/g)].map(match => {
        const metric = new Element();
        metric.dataset.pmMetric = match[1];
        return metric;
    });
    root.querySelectorAll = selector => selector === '[data-pm-metric]' ? metrics : [];
    const events = {};
    const frames = [];
    let requests = 0;
    let offline = false;
    const context = {
        document: {
            getElementById: id => elements[id], createElement: tag => new Element(tag),
            createDocumentFragment: () => new Element(), addEventListener: (key, callback) => { events[key] = callback; }
        },
        window: {}, Intl, Date, Map, Option: class extends Element {
            constructor(text, value) { super('option'); this.textContent = text; this.value = value; }
        },
        setTimeout, clearTimeout, AbortController,
        requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
        cancelAnimationFrame: () => {},
        fetch: async () => { requests++; if (offline) throw new Error('Offline'); return {ok: true, json: async () => ({
            ok: true, as_of: '2026-11-01', fetched_at: '2026-11-01T00:00:00Z', projects
        })}; },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'sqr-performance.js'), 'utf8'), context);
    return {
        elements, metrics, requests: () => requests,
        setOffline(value) { offline = value; },
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

test('portfolio remains lazy and status and health summaries include terminal and pipeline projects', async () => {
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
    app.elements['sqr-pm-rating'].value = 'danger';
    app.elements['sqr-pm-rating'].events.change();
    app.flush();
    assert.equal(app.metric('total'), '1');
    assert.equal(app.metric('rating_danger'), '1');
    app.elements['sqr-pm-reset'].events.click();
    app.flush();
    app.elements['sqr-pm-portfolio'].value = 'pipeline';
    app.elements['sqr-pm-portfolio'].events.change();
    app.flush();
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
    assert.ok(app.elements['sqr-pm-indicator-rows'].textContent.includes('N/R'));
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
    assert.ok(app.elements['sqr-pm-indicator-rows'].textContent.includes('N/R'));
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 1 of 2/);
    app.elements['sqr-pm-next'].events.click();
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 2 of 2/);
    assert.ok(app.elements['sqr-pm-status-rows'].textContent.includes('Project 25'));
    assert.ok(!app.elements['sqr-pm-status-rows'].textContent.includes('Project 24'));
    app.elements['sqr-pm-manager'].value = 'missing';
    app.elements['sqr-pm-manager'].events.change();
    app.flush();
    assert.equal(app.metric('total'), '0');
    assert.ok(app.elements['sqr-pm-empty'].hidden === false);
    assert.ok(!app.elements['sqr-pm-manager-chart'].textContent.includes('Manager'));
    app.elements['sqr-pm-reset'].events.click();
    app.flush();
    assert.equal(app.metric('total'), '25');
    assert.match(app.elements['sqr-pm-page'].textContent, /Page 1 of 2/);
});
