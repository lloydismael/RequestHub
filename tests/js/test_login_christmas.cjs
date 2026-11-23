const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'login-christmas.js'), 'utf8');

function start({ dark = false } = {}) {
    const window = new EventTarget();
    window.matchMedia = () => new EventTarget();
    const document = new EventTarget();
    const classes = new Set(dark ? ['dark-mode'] : []);
    const toggle = (name, enabled) => enabled ? classes.add(name) : classes.delete(name);
    const scene = { clientWidth: 1200, clientHeight: 900, appendChild() {} };
    const elements = { '.xmas-scene': scene, '.login-card': new EventTarget(), '.xmas-controls': new EventTarget() };
    const controls = new Map();
    let notify, paints = 0;
    Object.assign(document, {
        body: { classList: { contains: name => classes.has(name), toggle } },
        documentElement: { classList: { contains: name => name === 'login-christmas' } },
        hidden: false,
        querySelector: selector => elements[selector],
        getElementById(id) {
            if (!controls.has(id)) controls.set(id, Object.assign(new EventTarget(), { valueAsNumber: 0, setAttribute() {} }));
            return controls.get(id);
        },
        createElement() {
            return {
                setAttribute() {},
                getContext() {
                    return {
                        createRadialGradient: () => ({ addColorStop() {} }),
                        arc() {}, beginPath() {}, fill() {}, fillRect() {}, lineTo() {}, moveTo() {},
                        restore() {}, rotate() {}, save() {}, scale() {}, setTransform() {}, stroke() {}, translate() {},
                        drawImage() {},
                        clearRect() { paints++; },
                    };
                },
            };
        },
    });
    const frames = new Map();
    let next = 0;
    vm.runInNewContext(script, {
        window, document,
        localStorage: { getItem: () => null },
        MutationObserver: class { constructor(callback) { notify = callback; } observe() {} },
        requestAnimationFrame(callback) { frames.set(++next, callback); return next; },
        cancelAnimationFrame(id) { frames.delete(id); },
    });
    return {
        pending: () => frames.size,
        paints: () => paints,
        frame(now) {
            for (const [id, callback] of [...frames]) {
                if (!frames.delete(id)) continue;
                callback(now);
            }
        },
        appearance(name, enabled) { toggle(name, enabled); notify(); },
        hidden(value) {
            document.hidden = value;
            document.dispatchEvent(new Event('visibilitychange'));
        },
    };
}

test('sign-in snow keeps a still frame in light mode and animates in dark mode', () => {
    const app = start();
    const still = app.paints();
    assert.ok(still > 0);
    assert.equal(app.pending(), 0);
    app.appearance('dark-mode', true);
    app.frame(40);
    assert.ok(app.paints() > still);
    app.appearance('dark-mode', false);
    assert.equal(app.pending(), 0);
});

test('dark sign-in parks snowfall during theme swaps and hidden visits', () => {
    const app = start({ dark: true });
    app.frame(40);
    const visible = app.paints();
    app.appearance('theme-switching', true);
    assert.equal(app.pending(), 0);
    app.hidden(true);
    app.appearance('theme-switching', false);
    app.frame(80);
    assert.equal(app.paints(), visible);
    assert.equal(app.pending(), 0);
    app.hidden(false);
    app.frame(120);
    assert.ok(app.paints() > visible);
});
