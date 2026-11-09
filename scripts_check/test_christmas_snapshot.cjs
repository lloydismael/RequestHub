const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Node {
    constructor(tag, attrs = {}, text = '') {
        this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.children = [];
        this.textContent = text; this.style = {}; this.events = new Map();
    }
    get attributes() { return Object.entries(this.attrs).map(([name, value]) => ({ name, value })); }
    get outerHTML() { return `<${this.tagName.toLowerCase()}${Object.entries(this.attrs).map(([k, v]) => ` ${k}="${v}"`).join('')}>${this.textContent}${this.children.map(n => n.outerHTML).join('')}</${this.tagName.toLowerCase()}>`; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    getAttribute(name) { return this.attrs[name] ?? null; }
    removeAttribute(name) { delete this.attrs[name]; }
    appendChild(node) { this.children.push(node); node.parent = this; return node; }
    remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
    replaceWith(node) { const index = this.parent.children.indexOf(this); this.parent.children[index] = node; node.parent = this.parent; this.parent = null; }
    cloneNode(deep) {
        const copy = new Node(this.tagName, this.attrs, this.textContent);
        for (const key of ['value', 'checked', 'selected', 'width', 'height']) copy[key] = this[key];
        if (deep) this.children.forEach(child => copy.appendChild(child.cloneNode(true)));
        return copy;
    }
    querySelectorAll(selector) {
        const result = [];
        const matches = (node, s) => s === '*' || (s.startsWith('.') ? (node.attrs.class || '').split(' ').includes(s.slice(1)) : s.includes('[') ? node.tagName === s.split('[')[0].toUpperCase() && node.attrs[s.match(/\[([^=\]]+)/)[1]] !== undefined : node.tagName === s.toUpperCase());
        const walk = node => { for (const child of node.children) { if (selector.split(',').some(s => matches(child, s.trim()))) result.push(child); walk(child); } };
        walk(this); return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(type, callback) { this.events.set(type, callback); }
    removeEventListener(type) { this.events.delete(type); }
    emit(type) { this.events.get(type)?.(); }
}

function start() {
    const root = new Node('html', { 'data-theme': 'light' });
    const head = root.appendChild(new Node('head'));
    head.appendChild(new Node('link', { rel: 'stylesheet', href: '/static/css/app.css' }));
    head.appendChild(new Node('script', {}, 'window.bad = true'));
    const body = root.appendChild(new Node('body', { class: 'authenticated', 'data-account-theme': 'christmas' }));
    body.appendChild(new Node('button', { onclick: 'window.bad=true', autofocus: '' }, 'safe'));
    const input = body.appendChild(new Node('input', { type: 'text', value: 'saved' })); input.value = 'edited';
    body.appendChild(new Node('input', { type: 'password', value: 'private' }));
    body.appendChild(new Node('input', { type: 'hidden', value: 'credential' }));
    body.appendChild(new Node('iframe', { src: '/api/probe' }));
    const canvas = body.appendChild(new Node('canvas')); canvas.width = 240; canvas.height = 100; canvas.toDataURL = () => 'data:image/png;base64,synthetic';
    const timers = new Map(); let id = 0; const scrolls = [];
    const document = { documentElement: root, body, baseURI: 'https://example.invalid/dashboard/?private=1#fragment',
        querySelectorAll: s => root.querySelectorAll(s),
        createElement(tag) { const node = new Node(tag); if (tag === 'iframe') { node.contentWindow = { scrollTo: (...args) => scrolls.push(args) }; node.contentDocument = { fonts: { ready: Promise.resolve() } }; } return node; },
    };
    const window = { innerWidth: 375, innerHeight: 800, scrollX: 0, scrollY: 321,
        setTimeout(cb) { timers.set(++id, cb); return id; }, clearTimeout(id) { timers.delete(id); },
    };
    const filename = path.join(__dirname, '..', 'static', 'js', 'christmas-theme-snapshot.js');
    if (fs.existsSync(filename)) vm.runInNewContext(fs.readFileSync(filename, 'utf8'), { document, window, URL, Error });
    assert.ok(window.requestHubChristmasSnapshot, 'script-free snapshot API must be available');
    return { api: window.requestHubChristmasSnapshot, document, body, root, timers, scrolls };
}

test('capture preserves old theme and viewport without mutating the live document', () => {
    const app = start(), source = app.api.capture();
    assert.equal(source.viewport.width, 375); assert.equal(source.viewport.scrollY, 321);
    assert.match(source.html, /data-theme="light"/);
    assert.match(source.html, /https:\/\/example.invalid\/static\/css\/app.css/);
    assert.doesNotMatch(source.html, /private=1|#fragment/);
    assert.match(source.html, /value="edited"/);
    assert.match(source.html, /data:image\/png;base64,synthetic/);
    assert.equal(app.root.querySelectorAll('script').length, 1);
    assert.equal(app.root.querySelectorAll('iframe').length, 1);
});

test('snapshot strips executable content and sensitive field values', () => {
    const source = start().api.capture();
    assert.doesNotMatch(source.html, /<script|<iframe|onclick|autofocus|private|credential/);
});

test('nested and RTL scroll positions survive capture and layout restoration', async () => {
    const app = start();
    const table = app.body.appendChild(new Node('div'));
    table.scrollLeft = 720; table.scrollTop = 0;
    const panel = app.body.appendChild(new Node('div'));
    panel.scrollLeft = -96; panel.scrollTop = 280;
    const source = app.api.capture();
    assert.deepEqual(Array.from(source.scrolls || [], s => ({ left: s.left, top: s.top })), [
        { left: 720, top: 0 }, { left: -96, top: 280 },
    ]);
    assert.equal(table.getAttribute('data-christmas-snapshot-scroll'), null);
    const targets = Object.fromEntries(source.scrolls.map(s => [s.id, { scrollLeft: 0, scrollTop: 0 }]));
    const pending = app.api.mount(source);
    const iframe = app.body.children.at(-1);
    iframe.contentDocument.querySelector = selector => targets[selector.match(/"([^"]+)"/)[1]] || null;
    iframe.emit('load'); const layer = await pending;
    assert.deepEqual(Object.values(targets), [{ scrollLeft: 720, scrollTop: 0 }, { scrollLeft: -96, scrollTop: 280 }]);
    layer.dispose();
});

test('preparing snapshots stay hidden until fonts and scroll restoration are ready', async () => {
    const app = start();
    const pending = app.api.mount(app.api.capture());
    const iframe = app.body.children.at(-1);
    assert.equal(iframe.style.visibility, 'hidden');
    let releaseFonts;
    iframe.contentDocument.fonts.ready = new Promise(resolve => { releaseFonts = resolve; });
    iframe.emit('load');
    assert.equal(iframe.style.visibility, 'hidden');
    releaseFonts(); const layer = await pending;
    assert.deepEqual(app.scrolls, [[0, 321]]);
    assert.equal(iframe.style.visibility, 'visible');
    layer.dispose();
});

test('snapshot readiness and disposal are bounded', async () => {
    const app = start(), source = app.api.capture();
    const pending = app.api.mount(source, { timeoutMs: 1000 });
    const iframe = app.body.children.at(-1);
    assert.equal(iframe.getAttribute('sandbox'), 'allow-same-origin');
    assert.equal(iframe.getAttribute('aria-hidden'), 'true');
    iframe.emit('load'); const layer = await pending;
    assert.deepEqual(app.scrolls, [[0, 321]]);
    layer.dispose(); layer.dispose();
    assert.equal(app.body.querySelectorAll('iframe').length, 1, 'only the original fixture frame remains');
    assert.equal(app.timers.size, 0); assert.equal(iframe.events.size, 0);
});

test('aborted and timed out snapshots remove their iframe and listeners', async () => {
    for (const mode of ['abort', 'timeout']) {
        const app = start(), controller = new AbortController();
        const pending = app.api.mount(app.api.capture(), { signal: controller.signal, timeoutMs: 1000 });
        const iframe = app.body.children.at(-1);
        if (mode === 'abort') controller.abort(); else [...app.timers.values()][0]();
        await assert.rejects(pending);
        assert.equal(app.body.querySelectorAll('iframe').length, 1);
        assert.equal(app.timers.size, 0); assert.equal(iframe.events.size, 0);
    }
});
