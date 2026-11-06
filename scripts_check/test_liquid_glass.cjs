const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function start({ reducedMotion = false, preference = '' } = {}) {
    const frames = new Map();
    const listeners = new Map();
    let nextFrame = 1;
    let layoutReads = 0;

    function element() {
        const classes = new Set();
        const handlers = new Map();
        return {
            style: { setProperty(name, value) { this[name] = value; } },
            classList: {
                contains: name => classes.has(name),
                add: name => classes.add(name),
                remove: name => classes.delete(name),
            },
            addEventListener(name, handler) {
                if (!handlers.has(name)) handlers.set(name, []);
                handlers.get(name).push(handler);
            },
            fire(name, event = {}) { (handlers.get(name) || []).forEach(fn => fn(event)); },
            querySelector: () => null,
        };
    }

    const lens = element();
    const sheen = element();
    const card = element();
    card.querySelectorAll = () => [lens, sheen];
    card.getBoundingClientRect = () => {
        layoutReads++;
        return { left: 100, top: 100, width: 400, height: 500 };
    };
    const body = element();
    if (preference) body.classList.add(preference);
    const document = {
        body, hidden: false, readyState: 'complete',
        querySelector: selector => selector === '.page-login .login-card' ? card : null,
        querySelectorAll: () => [],
        addEventListener(name, handler) { listeners.set(name, handler); },
    };
    const window = {
        matchMedia: query => ({ matches: query.includes('reduced-motion') && reducedMotion }),
        addEventListener() {},
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'liquid-glass.js'), 'utf8'), {
        document, window, console,
        requestAnimationFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
        cancelAnimationFrame(id) { frames.delete(id); },
    });
    const frame = () => {
        const pending = [...frames.values()];
        frames.clear();
        pending.forEach(callback => callback());
    };
    frame(); // Complete the entrance before measuring cursor work.
    return {
        card, lens, sheen, frames, frame,
        layoutReads: () => layoutReads,
        hidden(value) { document.hidden = value; listeners.get('visibilitychange')?.(); },
    };
}

test('pointer light updates stay on optical layers, not the decorated card', () => {
    const app = start();
    app.card.fire('pointermove', { clientX: 400, clientY: 420 });
    app.frame();

    assert.equal(app.card.style['--login-light-x'], undefined, 'no inherited light change on the card');
    assert.equal(app.card.style['--login-light-y'], undefined, 'no inherited light change on the card');
    for (const layer of [app.lens, app.sheen]) {
        assert.equal(layer.style['--login-light-x'], '54.0%');
        assert.equal(layer.style['--login-light-y'], '25.4%');
    }
});

test('a burst of pointer samples reads layout only once on the next frame', () => {
    const app = start();
    for (const point of [[100, 100], [200, 200], [400, 420]]) {
        app.card.fire('pointermove', { clientX: point[0], clientY: point[1] });
    }
    assert.equal(app.layoutReads(), 0, 'input events must not force layout');
    assert.equal(app.frames.size, 1, 'pointer samples share a frame');
    app.frame();
    assert.equal(app.layoutReads(), 1);
    assert.equal(app.sheen.style['--login-light-x'], '54.0%', 'the latest pointer sample wins');
});

test('leaving before the pending frame restores the resting light', () => {
    const app = start();
    app.card.fire('pointermove', { clientX: 400, clientY: 420 });
    app.card.fire('pointerleave');
    app.frame();
    assert.equal(app.sheen.style['--login-light-x'], '50.0%');
    assert.equal(app.sheen.style['--login-light-y'], '18.0%');
    assert.equal(app.layoutReads(), 0, 'a discarded sample does not need layout');
    assert.equal(app.frames.size, 0);
});

test('cursor animation parks when hidden and resumes when visible', () => {
    const app = start();
    app.card.fire('pointermove', { clientX: 400, clientY: 420 });
    app.hidden(true);
    assert.equal(app.frames.size, 0);
    app.hidden(false);
    assert.equal(app.frames.size, 1);
    app.frame();
    assert.equal(app.sheen.style['--login-light-x'], '54.0%');
});

test('reduced-motion and reduced-effects preferences skip cursor animation', () => {
    for (const options of [{ reducedMotion: true }, { preference: 'pref-reduce-motion' }, { preference: 'pref-reduce-effects' }]) {
        const app = start(options);
        app.card.fire('pointermove', { clientX: 400, clientY: 420 });
        assert.equal(app.frames.size, 0);
        assert.equal(app.layoutReads(), 0);
    }
});
