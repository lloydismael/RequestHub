const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const data = input.data;
const requests = [], writes = [], listeners = {}, nodes = new Map();
let afterMutation = false;
const camel = key => key.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = []; this.attributes = {}; this.listeners = {};
        this.dataset = {}; this.style = {}; this.hidden = false; this.disabled = false; this.value = ''; this.open = false; this.className = '';
        this.classList = {contains: name => this.className.split(/\s+/).includes(name),
            add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).filter(Boolean).concat(names))].join(' '); },
            remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); },
            toggle: (name, enabled) => { enabled = enabled === undefined ? !this.classList.contains(name) : enabled; this.classList[enabled ? 'add' : 'remove'](name); return enabled; }};
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return (this.text || '') + this.children.map(node => node.textContent).join(''); }
    get options() { return this.children.filter(node => node.tagName === 'OPTION'); }
    get firstChild() { return this.children[0] || null; }
    setAttribute(key, value) {
        this.attributes[key] = String(value);
        if (key === 'id') this.id = String(value);
        if (key === 'class') this.className = String(value);
        if (key.startsWith('data-')) this.dataset[camel(key.slice(5))] = String(value);
        if (key === 'disabled') this.disabled = true;
        if (key === 'hidden') this.hidden = true;
    }
    getAttribute(key) {
        if (key === 'id') return this.id || null;
        if (key === 'class') return this.className || null;
        if (key.startsWith('data-')) return this.dataset[camel(key.slice(5))] === undefined ? null : String(this.dataset[camel(key.slice(5))]);
        return this.attributes[key] === undefined ? null : this.attributes[key];
    }
    removeAttribute(key) { delete this.attributes[key]; if (key === 'disabled') this.disabled = false; }
    append(...items) { for (let node of items) { if (typeof node !== 'object') node = Object.assign(new Element('#text'), {textContent: node}); if (node.tagName === 'FRAGMENT') this.append(...node.children); else { node.parentElement = this; this.children.push(node); } } }
    appendChild(node) { this.append(node); return node; }
    add(node) { this.append(node); }
    replaceChildren(...items) { for (const node of this.children) node.parentElement = null; this.children = []; this.text = ''; this.append(...items); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    removeEventListener() {}
    emit(type, extra = {}) { for (const callback of this.listeners[type] || []) callback({target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...extra}); }
    focus() { document.activeElement = this; }
    blur() { document.activeElement = null; }
    scrollIntoView() {}
    showModal() { this.open = true; }
    close() { this.open = false; }
    walk() { return [this, ...this.children.flatMap(node => node.walk())]; }
    contains(node) { return this.walk().includes(node); }
    matches(selector) {
        return selector.split(',').some(part => {
            part = part.trim().split(/\s+/).at(-1);
            const tag = part.match(/^[a-z]+/i);
            if (tag && this.tagName !== tag[0].toUpperCase()) return false;
            const id = part.match(/#([\w-]+)/); if (id && this.id !== id[1]) return false;
            for (const match of part.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
            for (const match of part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
                const value = this.getAttribute(match[1]); if (value === null || (match[2] !== undefined && value !== match[2])) return false;
            }
            return true;
        });
    }
    querySelectorAll(selector) { return this.walk().slice(1).filter(node => node.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; }
}

const root = new Element(); root.id = 'sqr-proto'; root.dataset = {endpoint: '/sqr/prototype-data/', csrf: 'fixture-only', defaultAssigned: 'all'};
const document = {
    activeElement: null, hidden: false,
    createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment'),
    createTextNode: text => Object.assign(new Element('#text'), {textContent: text}),
    getElementById: id => root.walk().find(node => node.id === id) || nodes.get(id) || null,
    addEventListener: (type, callback) => { (listeners[type] ||= []).push(callback); },
    removeEventListener() {}, dispatchEvent: event => { for (const callback of listeners[event.type] || []) callback(event); }
};
function staticNode(id, tag = 'div', parent = root) { const node = new Element(tag); node.id = id; nodes.set(id, node); parent.append(node); return node; }
for (const id of ['body', 'pipeline', 'editor', 'search', 'toast', 'toast-text', 'toast-undo', 'toast-close', 'approve', 'revise', 'revise-notes', 'revise-error', 'layout-list', 'layout-table', 'catalog', 'list', 'table-wrap', 'table-rows', 'items', 'more']) staticNode('sqr-proto-' + id, id === 'items' ? 'ul' : id === 'table-rows' ? 'tbody' : id.startsWith('layout-') ? 'button' : 'div');
const filterRoot = staticNode('sqr-proto-filters'); filterRoot.dataset = {optionsId: 'filters-options', initialCount: String(data.pagination.total), canMine: 'false'};
staticNode('filters-options').textContent = JSON.stringify(data.filter_options);
const location = new URL('https://fixture.invalid/sqr/' + input.query);
const storage = new Map(input.savedLayout === undefined ? [] : [['sqr_tracker_layout_v1', input.savedLayout]]);
const window = {
    location, matchMedia: () => ({matches: false}),
    history: {replaceState(_state, _title, value) { location.href = new URL(value, location).href; }},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
    sessionStorage: {getItem: () => null, setItem() {}}, setTimeout: () => 1, clearTimeout() {}, addEventListener() {}, removeEventListener() {}
};
const clone = value => JSON.parse(JSON.stringify(value));
const context = {document, window, URLSearchParams, Intl, AbortController, Map, Date,
    sessionStorage: window.sessionStorage, localStorage: window.localStorage,
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
        requests.push(String(url));
        if (options.method === 'POST') { writes.push({url, body: JSON.parse(options.body)}); afterMutation = true; return {ok: true, status: 200, json: async () => ({ok: true})}; }
        const params = new URL(url, location).searchParams;
        let value = afterMutation && input.after ? input.after : data;
        if (params.has('pk')) {
            const record = clone(data.records.find(record => String(record.pk) === params.get('pk')));
            if (afterMutation && writes.length) { record.values[writes.at(-1).body.field] = writes.at(-1).body.value; record[writes.at(-1).body.field] = writes.at(-1).body.value; }
            return {ok: true, status: 200, json: async () => ({ok: true, record})};
        }
        return {ok: true, status: 200, json: async () => clone(value)};
    }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'sqr-filters.js'), 'utf8'), context);
let source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'sqr-tracker-prototype.js'), 'utf8');
const end = source.lastIndexOf('})();');
source = source.slice(0, end) + 'window.fixtureTracker = {state:state, el:el, setLayout:typeof setLayout === "function" ? setLayout : null, select:select, load:load};\n' + source.slice(end);
vm.runInContext(source, context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const listPks = () => nodes.get('sqr-proto-items').querySelectorAll('[data-pk]').map(node => Number(node.dataset.pk));
const tablePks = () => nodes.get('sqr-proto-table-rows').querySelectorAll('tr[data-pk]').map(node => Number(node.dataset.pk));

(async () => {
    await settle(); await settle();
    const tracker = window.fixtureTracker;
    const result = {initial_layout: tracker.state.layout || null, query: location.search, requests};
    if (input.scenario === 'invalid') {
        assert.ok(listPks().length, 'A bad preference must not leave the catalog empty');
    } else {
        assert.equal(typeof tracker.setLayout, 'function', 'The existing controller must support List/Table switching');
        const before = listPks(); const count = filterRoot.textContent;
        tracker.select(data.records[0].pk);
        const selected = tracker.state.selectedPk;
        nodes.get('sqr-proto-layout-table').emit('click');
        assert.equal(tracker.state.layout, 'table');
        result.table_pks = tablePks(); result.list_pks = before;
        result.selected_before = selected; result.selected_after = tracker.state.selectedPk;
        result.count_preserved = filterRoot.textContent === count;
        if (input.scenario === 'switch') {
            nodes.get('sqr-proto-layout-list').emit('click');
            assert.equal(tracker.state.layout, 'list'); result.list_after = listPks();
            result.saved_layout = storage.get('sqr_tracker_layout_v1');
        } else {
            const button = nodes.get('sqr-proto-table-rows').querySelector('button');
            assert.ok(button, 'Table rows expose an Open action'); button.emit('click');
            const delivery = nodes.get('sqr-proto-editor').querySelector('[data-tab="delivery"]');
            assert.ok(delivery, 'Table Open must use the existing record editor'); delivery.emit('click');
            const field = input.scenario === 'membership' ? 'overall_status' : 'quality_performance';
            const control = nodes.get('sqr-proto-editor').querySelector('[data-key="' + field + '"]');
            if (input.scenario === 'readonly') { assert.ok(!control || control.disabled, 'Locked records cannot gain write controls in Table'); }
            else { assert.ok(control, 'The shared Delivery editor provides its existing control'); control.value = input.value; control.emit('change'); await settle(); await settle(); }
            result.selected_after_save = tracker.state.selectedPk;
            result.after_table_pks = tablePks(); result.after_list_pks = listPks();
            result.toast = nodes.get('sqr-proto-toast-text').textContent;
            result.total_after = tracker.state.pagination.total; result.page_after = tracker.state.pagination.page;
        }
    }
    result.writes = writes; result.query = location.search;
    console.log(JSON.stringify(result));
})().catch(error => { console.error(error); process.exitCode = 1; });
