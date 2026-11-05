const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'update-mentions.js'), 'utf8');

class Element {
    constructor() { this.children = []; this.attributes = {}; this.listeners = {}; this.hidden = true; this.value = ''; this.selectionStart = 0; this.selectionEnd = 0; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    dispatchEvent(event) { (this.listeners[event.type] || []).forEach(callback => callback(event)); }
    fire(type, extras = {}) { const event = { type, preventDefault() { this.prevented = true; }, ...extras }; this.dispatchEvent(event); return event; }
    querySelectorAll() { return this.children.filter(child => child.attributes.role === 'option'); }
    scrollIntoView() {}
    focus() {}
    setRangeText(value, start, end) { this.value = this.value.slice(0, start) + value + this.value.slice(end); this.selectionStart = this.selectionEnd = start + value.length; }
}

function start(people = [{ username: 'assigned-person', name: 'Assigned Person', role: 'Engineer' }, { username: 'backup-person', name: 'Backup Person', role: 'Engineer' }]) {
    const input = new Element();
    const list = new Element(); list.id = 'request-mention-suggestions';
    const trigger = new Element();
    const status = new Element();
    const data = { textContent: JSON.stringify(people) };
    const composer = new Element();
    const elements = { textarea: input, '#request-mention-people': data, '#request-mention-suggestions': list, '[data-insert-mention]': trigger, '[data-mention-status]': status };
    composer.querySelector = selector => elements[selector];
    composer.contains = target => Object.values(elements).includes(target) || target === composer;
    const document = new Element();
    document.querySelector = () => composer;
    document.createElement = () => new Element();
    vm.runInNewContext(source, { document, Event: class { constructor(type) { this.type = type; } } });
    return { input, list, trigger, status, document, type(value, position = value.length) { input.value = value; input.selectionStart = input.selectionEnd = position; input.fire('input'); } };
}

test('name search shows only supplied request participants', () => {
    const app = start();
    app.type('Please review @Assigned');
    assert.equal(app.list.hidden, false);
    assert.equal(app.list.children.length, 1);
    assert.equal(app.list.children[0].children[0].textContent, 'Assigned Person');
    app.type('@unrelated');
    assert.equal(app.list.querySelectorAll().length, 0);
    assert.equal(app.list.children[0].textContent, 'No related people match this name.');
});

test('keyboard insertion preserves text after the cursor and never submits the form', () => {
    const app = start();
    app.type('Hello @ and thanks', 7);
    const down = app.input.fire('keydown', { key: 'ArrowDown' });
    assert.equal(down.prevented, true);
    const enter = app.input.fire('keydown', { key: 'Enter' });
    assert.equal(enter.prevented, true);
    assert.equal(app.input.value, 'Hello @backup-person  and thanks');
    assert.equal(app.list.hidden, true);
    assert.equal(app.input.attributes['aria-activedescendant'], undefined);
});

test('mention button inserts a trigger and Escape dismisses suggestions', () => {
    const app = start();
    app.type('Hello');
    app.trigger.fire('click');
    assert.equal(app.input.value, 'Hello @');
    assert.equal(app.list.hidden, false);
    app.input.fire('keydown', { key: 'Escape' });
    assert.equal(app.list.hidden, true);
    assert.equal(app.trigger.attributes['aria-expanded'], 'false');
});

test('email addresses do not open the mention list', () => {
    const app = start();
    app.type('Contact support@example.com');
    assert.equal(app.list.hidden, true);
});

test('Unicode names can be searched without treating them as completed mentions', () => {
    const app = start([{ username: 'engineer-one', name: 'José Engineer', role: 'Engineer' }]);
    app.type('@José');
    assert.equal(app.list.hidden, false);
    assert.equal(app.list.children[0].children[0].textContent, 'José Engineer');
});

test('email-style usernames can be searched and inserted intact', () => {
    const app = start([{ username: 'engineer@example.com', name: 'Engineer', role: 'Engineer' }]);
    app.type('Hello @engineer@exam');
    assert.equal(app.list.hidden, false);
    app.input.fire('keydown', { key: 'Enter' });
    assert.equal(app.input.value, 'Hello @engineer@example.com ');
});

test('candidate names are rendered as text, not markup', () => {
    const app = start([{ username: 'engineer-one', name: '<img src=x onerror=alert(1)>', role: 'Engineer' }]);
    app.type('@');
    assert.equal(app.list.children[0].children[0].textContent, '<img src=x onerror=alert(1)>');
});
