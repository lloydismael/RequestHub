const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function start({ width = 1280, height = 76, viewport = 1440, theme = 'christmas', flag = true, radius = '999px' } = {}) {
    const listeners = {};
    const observers = [];
    const frames = new Map();
    let builds = 0;
    let removals = 0;
    let resize;
    let nextId = 1;
    function element(tag) {
        const classes = new Set();
        const node = {
            tag, children: [], dataset: {}, attributes: {}, style: {},
            classList: {
                contains: name => classes.has(name),
                add: name => classes.add(name), remove: name => classes.delete(name),
                toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
            },
            setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'class') value.split(' ').forEach(x => classes.add(x)); },
            appendChild(child) { if (classes.has('seasonal-lights-overlay')) builds++; this.children.push(child); child.parentNode = this; },
            remove() { if (classes.has('seasonal-lights-overlay')) removals++; this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); },
            // Mirrors the real navbar:offsetWidth/Height are the untransformed layout
            // box, while the decoration class shifts the border box via top/margin.
            get offsetWidth() { return width; },
            get offsetHeight() { return height; },
            getBoundingClientRect() { return { width, height: height - (classes.has('seasonal-lights--active') ? 16 : 0) }; },
        };
        node.style.setProperty = (name, value) => { node.style[name] = value; };
        return node;
    }
    const body = element('body');
    body.dataset.accountTheme = theme;
    body.classList.add('christmas-effects-on');
    const nav = element('nav');
    nav.dataset.seasonalLightsTheme = 'christmas';
    if (flag) nav.classList.add('seasonal-lights');
    const document = {
        body, hidden: false,
        querySelectorAll: () => [nav], createElementNS: (ns, tag) => element(tag),
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    const window = {
        innerWidth: viewport,
        getComputedStyle: () => ({ borderTopLeftRadius: radius }),
        requestAnimationFrame(callback) { const id = nextId++; frames.set(id, callback); return id; },
        cancelAnimationFrame(id) { frames.delete(id); },
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'christmas-lights.js'), 'utf8'), {
        document, window, Math,
        MutationObserver: class { constructor(callback) { observers.push(callback); } observe() {} },
        ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} },
    });
    function flush() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); }
    flush();
    return {
        body, nav, document, window,
        update() { observers.forEach(callback => callback()); flush(); },
        stats() { return { builds, removals }; },
        reset() { builds = 0; removals = 0; },
        resize(w, h, vw) { width = w; height = h; window.innerWidth = vw; resize(); flush(); },
        windowResize() { listeners.resize(); flush(); },
        visibility(hidden) { document.hidden = hidden; listeners.visibilitychange(); flush(); },
        get overlay() { return nav.children[0]; },
        get bulbs() { return nav.children[0]?.children.filter(child => child.classList.contains('seasonal-light-bulb')) || []; },
    };
}

const angleDiff = (a, b) => Math.abs(((a - b) % 360 + 540) % 360 - 180);
const REFERENCE_COLOURS = 'rbrgrbobrbygyrb' + 'ryg' + 'orbygrygobyrgg' + 'rbog';
const COLOUR_CODES = { '#f83d51': 'r', '#20d779': 'g', '#258aff': 'b', '#ffc451': 'y', '#ff8736': 'o' };
const colours = app => app.bulbs.map(bulb => COLOUR_CODES[bulb.style['--bulb-color']]).join('');
const placement = bulb => bulb.attributes.transform.match(/translate\(([-.\d]+) ([-.\d]+)\) rotate\(([-.\d]+)\)/).slice(1).map(Number);
const wirePoints = path => {
    const values = path.match(/-?\d+(?:\.\d+)?/g).map(Number);
    return values.flatMap((value, index) => index % 2 ? [] : [[value, values[index + 1]]]);
};

test('Christmas light selection removes the overlay and restores the original geometry', () => {
    const app = start();
    const original = app.bulbs.map(bulb => bulb.attributes.transform);
    app.body.classList.add('christmas-lights-off');
    app.update();
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.nav.classList.contains('seasonal-lights--active'), false);
    app.body.classList.remove('christmas-lights-off');
    app.update();
    assert.deepEqual(app.bulbs.map(bulb => bulb.attributes.transform), original);
    app.nav.dataset.seasonalLightsTheme = '';
    app.body.classList.add('christmas-lights-off');
    app.update();
    assert.equal(app.bulbs.length, 36);
});

test('theme and preference class changes never rebuild the overlay', () => {
    const app = start();
    const wires = app.overlay.children.filter(child => child.tag === 'path').map(child => child.attributes.d);
    app.reset();
    // A light/dark toggle rewrites body classes; the navbar offset shifts with it.
    app.body.classList.toggle('dark-mode');
    app.update();
    app.body.classList.toggle('dark-mode');
    app.update();
    app.body.classList.toggle('pref-reduce-effects');
    app.update();
    assert.deepEqual(app.stats(), { builds: 0, removals: 0 });
    assert.deepEqual(
        app.overlay.children.filter(child => child.tag === 'path').map(child => child.attributes.d),
        wires,
    );
    assert.equal(app.bulbs.length, 36);
    // A real size change must still rebuild.
    app.resize(1280, 120, 1440);
    assert.ok(app.stats().builds > 0);
});
test('desktop overlay reproduces the 36-bulb reference sequence with two closed twisted wires', () => {
    const app = start();
    assert.equal(app.bulbs.length, 36);
    assert.equal(colours(app), REFERENCE_COLOURS);
    assert.equal(app.overlay.attributes['aria-hidden'], 'true');
    assert.equal(app.overlay.attributes.focusable, 'false');
    assert.equal(app.overlay.attributes.viewBox, '0 0 1280 76');
    const wires = app.overlay.children.filter(child => child.tag === 'path');
    assert.equal(wires.length, 2);
    assert.ok(wires.every(wire => wire.attributes.d.endsWith('Z') && wire.attributes.d.includes('C')));
    assert.notEqual(wires[0].attributes.d, wires[1].attributes.d);
    assert.ok(app.bulbs.every(bulb => !bulb.attributes.transform.includes('NaN')));
    const again = start();
    assert.deepEqual(again.bulbs.map(bulb => bulb.attributes.transform), app.bulbs.map(bulb => bulb.attributes.transform));
    assert.equal(again.overlay.children[0].attributes.d, wires[0].attributes.d);
});

test('reference bulb angles and wire sags map onto the measured navbar perimeter', () => {
    const app = start();
    const [firstX, firstY, firstAngle] = placement(app.bulbs[0]);
    assert.ok(Math.abs(firstX - 65.2) < 1, `first top bulb x ${firstX}`);
    assert.ok(firstY > -9 && firstY < 4);
    assert.ok(angleDiff(firstAngle, 84) < 0.5);
    const [bottomX, bottomY, bottomAngle] = placement(app.bulbs[19]);
    assert.ok(Math.abs(bottomX - 1090.4) < 1, `inward bottom bulb x ${bottomX}`);
    assert.ok(bottomY > 70 && bottomY < 90);
    assert.ok(angleDiff(bottomAngle, 45) < 0.5);
    const [leftX, , leftAngle] = placement(app.bulbs[33]);
    assert.ok(leftX < 7, `left-end bulb x ${leftX}`);
    assert.ok(angleDiff(leftAngle, 234.9) < 0.5);
    assert.equal(app.bulbs.filter(bulb => bulb.attributes.class.includes('--inward')).length, 7);
    assert.ok(app.bulbs[0].attributes.transform.includes('scale(1.06)'));
    const wire = wirePoints(app.overlay.children[0].attributes.d);
    const lowest = wire.reduce((low, point) => point[1] > low[1] ? point : low);
    assert.ok(lowest[1] > 85 && lowest[0] > 1000, `bottom-right sag ${lowest}`);
    assert.ok(wire.some(([x, y]) => x > 500 && x < 800 && y > 2 && y < 8), 'top wire should overlap the border');
    assert.ok(app.nav.classList.contains('seasonal-lights--active'));
    app.resize(350, 80, 375);
    assert.equal(app.nav.classList.contains('seasonal-lights--active'), false);
});

test('bulbs use five holiday colours and staggered 2–4 second loops', () => {
    const app = start();
    const colours = new Set(app.bulbs.map(bulb => bulb.style['--bulb-color']));
    assert.equal(colours.size, 5);
    const times = app.bulbs.map(bulb => parseFloat(bulb.style['--twinkle-duration']));
    assert.ok(times.every(time => time >= 2 && time <= 4));
    assert.ok(new Set(times).size > 20);
});

test('resizing recalculates actual navbar geometry with 24 tablet bulbs', () => {
    const app = start();
    const old = app.overlay;
    app.resize(730, 90, 800);
    assert.equal(app.bulbs.length, 24);
    assert.equal(colours(app), [...REFERENCE_COLOURS].filter((_, index) => index % 3 !== 2).join(''));
    assert.equal(app.overlay.attributes.viewBox, '0 0 730 90');
    assert.notEqual(app.overlay, old);
    app.windowResize();
    assert.equal(app.nav.children.length, 1);
});

test('under 480px and zero-size navbars remove the overlay; wider sizes restore it', () => {
    const app = start();
    app.resize(350, 80, 375);
    assert.equal(app.nav.children.length, 0);
    app.resize(600, 80, 700);
    assert.equal(app.bulbs.length, 24);
    app.resize(0, 0, 700);
    assert.equal(app.nav.children.length, 0);
});

test('removing the flag or changing account themes removes lights without altering the navbar', () => {
    const app = start();
    assert.equal(Object.keys(app.nav.style).length, 1);
    assert.equal(Object.keys(app.nav.attributes).length, 0);
    app.nav.classList.remove('seasonal-lights');
    app.update();
    assert.equal(app.nav.children.length, 0);
    app.nav.classList.add('seasonal-lights');
    app.update();
    assert.equal(app.bulbs.length, 36);
    app.body.dataset.accountTheme = 'blue';
    app.update();
    assert.equal(app.nav.children.length, 0);
    const off = start({ flag: false });
    assert.equal(off.nav.children.length, 0);
});

test('preferences and hidden tabs pause twinkle without rebuilding or moving bulbs', () => {
    const app = start();
    const overlay = app.overlay;
    app.body.classList.add('pref-reduce-motion');
    app.update();
    assert.equal(app.overlay, overlay);
    assert.ok(app.overlay.classList.contains('seasonal-lights-overlay--paused'));
    app.body.classList.remove('pref-reduce-motion');
    app.body.classList.remove('christmas-effects-on');
    app.update();
    assert.ok(app.overlay.classList.contains('seasonal-lights-overlay--paused'));
    app.body.classList.add('christmas-effects-on');
    app.update();
    assert.equal(app.overlay.classList.contains('seasonal-lights-overlay--paused'), false);
    app.visibility(true);
    assert.ok(app.overlay.classList.contains('seasonal-lights-overlay--paused'));
});

test('tall expanded navbars and non-pill rounded corners remain finite', () => {
    const app = start({ width: 660, height: 280, radius: '32px', viewport: 800 });
    assert.equal(app.bulbs.length, 24);
    assert.ok(app.overlay.children.every(child => !String(child.attributes.d || child.attributes.transform).includes('NaN')));
});
