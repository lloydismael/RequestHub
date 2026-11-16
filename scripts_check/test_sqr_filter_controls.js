const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor(tag, doc) { this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.children = []; this.attrs = {}; this.listeners = {}; this.hidden = false; this.value = ''; this.checked = false; this._text = ''; }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    replaceChildren(...children) { this.children = []; this.append(...children); this._text = ''; }
    setAttribute(key, value) { this.attrs[key] = String(value); }
    getAttribute(key) { return this.attrs[key] || null; }
    addEventListener(key, callback) { (this.listeners[key] ||= []).push(callback); }
    removeEventListener(key, callback) { this.listeners[key] = (this.listeners[key] || []).filter(item => item !== callback); }
    fire(key, extra = {}) { (this.listeners[key] || []).forEach(callback => callback({target: this, preventDefault() {}, ...extra})); }
    click() { this.fire('click'); }
    focus() { this.ownerDocument.activeElement = this; }
    set textContent(text) { this.children = []; this._text = String(text); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set innerHTML(_) { throw new Error('HTML parsing sink used'); }
}
function all(root, predicate) { return root.children.flatMap(child => [...(predicate(child) ? [child] : []), ...all(child, predicate)]); }
function fixture(raw = {}) {
    const document = {createElement: tag => new Element(tag, document), activeElement: null};
    const root = document.createElement('div');
    const context = {document};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'sqr-filter-core.js'), 'utf8'), context);
    const keys = ['status', 'pm_id'];
    let applied = context.SqrFilterCore.normalizeFilters(raw, keys).state;
    const config = {dimensions: [{key: 'status', label: 'Status', options: [{value: 'reviewed', label: 'Reviewed', count: 2}, {value: 'submitted', label: '<script>pending</script>', count: 1}]},
        {key: 'pm_id', label: 'Project Manager', options: [{value: '8', label: 'Manager Eight', count: 3}]}],
        state: applied, resultCount: 3, onApply: next => { applied = next; }};
    const file = path.join(__dirname, '..', 'static', 'js', 'sqr-filter-controls.js');
    let controls;
    if (fs.existsSync(file)) { vm.runInContext(fs.readFileSync(file, 'utf8'), context); controls = context.SqrFilterControls.mount(root, config); }
    function button(text) { const found = all(root, node => node.tagName === 'BUTTON' && node.textContent === text)[0]; assert.ok(found, `Filter control '${text}' must be available`); return found; }
    function input(label) { const found = all(root, node => node.tagName === 'INPUT' && node.getAttribute('aria-label') === label)[0]; assert.ok(found, `Input '${label}' must be labeled`); return found; }
    return {root, document, config, controls, button, input, get applied() { return applied; }};
}

test('checkbox drafts apply atomically; panel Clear, chips and Clear all use the same applied state', () => {
    const app = fixture(); app.button('Status').click();
    const checkbox = app.input('Reviewed (2)'); checkbox.checked = true; checkbox.fire('change');
    assert.equal(app.applied.selections.status.length, 0);
    app.button('Apply Status').click();
    assert.equal(app.applied.selections.status[0], 'reviewed');
    app.button('Remove Status: Reviewed').click();
    assert.equal(app.applied.selections.status.length, 0);
    app.button('Status').click(); app.input('Reviewed (2)').checked = true; app.input('Reviewed (2)').fire('change');
    app.button('Apply Status').click(); app.button('Status').click(); app.button('Clear Status').click();
    assert.equal(app.applied.selections.status.length, 0);
    app.button('My Assigned').click(); assert.equal(app.applied.mine, true);
    app.button('Clear all').click(); assert.equal(app.applied.mine, false);
});

test('option search is text-safe, checkboxes are counted, and Escape restores trigger focus', () => {
    const app = fixture(); const trigger = app.button('Status'); trigger.click();
    app.input('Search Status options').value = 'pending'; app.input('Search Status options').fire('input');
    const checkbox = app.input('<script>pending</script> (1)');
    assert.equal(checkbox.parentNode.hidden, false);
    assert.equal(app.input('Reviewed (2)').parentNode.hidden, true);
    app.root.fire('keydown', {key: 'Escape'});
    assert.equal(app.document.activeElement, trigger);
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
});

test('unavailable IDs remain visible until deliberately cleared; refresh does not discard them', () => {
    const app = fixture({selections: {pm_id: ['999']}}); app.button('Project Manager').click();
    assert.equal(app.input('Unavailable: 999 (0)').checked, true);
    app.controls.setState(app.applied);
    assert.deepEqual(Array.from(app.applied.selections.pm_id), ['999']);
    app.button('Remove Project Manager: Unavailable: 999').click();
    assert.equal(app.applied.selections.pm_id.length, 0);
});

test('invalid price or date draft leaves applied ranges intact and announces the error', () => {
    const app = fixture({priceMin: '10'}); app.button('Price and dates').click();
    app.input('Minimum price').value = '20'; app.input('Maximum price').value = '5';
    app.button('Apply ranges').click(); assert.equal(app.applied.priceMin, '10');
    assert.ok(all(app.root, node => node.getAttribute('role') === 'alert').some(node => node.textContent.includes('minimum')));
    app.input('Maximum price').value = '30'; app.input('Start from').value = '2027-02-29';
    app.button('Apply ranges').click(); assert.equal(app.applied.priceMin, '10');
    app.input('Start from').value = '2028-02-29'; app.button('Apply ranges').click();
    assert.equal(app.applied.priceMin, '20'); assert.equal(app.applied.dateRanges.delivery_start_date.from, '2028-02-29');
});

test('Apply returns focus to its disclosure; toggles and external refresh retain keyboard focus', () => {
    const app = fixture(); app.button('Status').click(); app.button('Apply Status').focus();
    app.button('Apply Status').click();
    assert.ok(app.document.activeElement === app.button('Status'), 'Apply must focus the replacement disclosure');
    app.button('My Assigned').focus(); app.button('My Assigned').click();
    assert.ok(app.document.activeElement === app.button('My Assigned'), 'Toggle must retain focus');
    app.controls.setState(app.applied);
    assert.ok(app.document.activeElement === app.button('My Assigned'), 'External refresh must retain focus');
});

test('record refresh preserves an open filter draft and focus while updating counts', () => {
    const app = fixture(); app.button('Status').click();
    app.input('Search Status options').value = 'review'; app.input('Search Status options').fire('input');
    const checkbox = app.input('Reviewed (2)'); checkbox.checked = true; checkbox.fire('change'); checkbox.focus();
    app.config.resultCount = 5; app.config.dimensions[0].options[0].count = 4;
    app.controls.setState(app.applied);
    assert.equal(app.button('Status').getAttribute('aria-expanded'), 'true', 'Refresh must not close an active draft');
    assert.ok(app.document.activeElement === app.input('Reviewed (4)'), 'Focus must remain on the draft option');
    assert.equal(app.input('Reviewed (4)').checked, true);
    assert.equal(app.input('Search Status options').value, 'review');
    assert.equal(app.input('<script>pending</script> (1)').parentNode.hidden, true);
    assert.ok(all(app.root, node => node.getAttribute('role') === 'status').some(node => node.textContent === '5 matching records'));
    app.button('Apply Status').click(); assert.equal(app.applied.selections.status[0], 'reviewed');
});

test('external applied-filter changes replace drafts even when the consumer mutates its original state', () => {
    const app = fixture(); app.button('Status').click(); app.input('Reviewed (2)').checked = true; app.input('Reviewed (2)').fire('change');
    app.config.state.selections.status = ['submitted'];
    app.controls.setState(app.config.state);
    assert.equal(app.button('Status').getAttribute('aria-expanded'), 'false');
    app.button('Status').click(); app.button('Apply Status').click();
    assert.deepEqual(Array.from(app.applied.selections.status), ['submitted']);
});
