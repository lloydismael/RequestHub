const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Harness for the login page Christmas scene. The scene is built once into
// [data-xmas-card-deco]; a light/dark flip must only retint the palette
// gradients, never tear the scene down (which would recreate the feTurbulence
// and feGaussianBlur filters and every DPR-scaled canvas raster).
function start({ width = 1280, height = 900, mobile = false, dark = false, reducedMotion = false } = {}) {
    const observers = [];
    const documentListeners = new Map();
    const motionListeners = [];
    const motionQuery = {
        matches: reducedMotion,
        addEventListener(name, handler) { if (name === 'change') motionListeners.push(handler); },
    };
    const frames = new Map();
    let nextId = 1;
    let buildCalls = 0;

    function element(tag, ns) {
        const classes = new Set();
        const node = {

            tag, ns, uid: nextId++, children: [], attributes: {}, dataset: {}, style: {}, textContent: '',
            classList: {
                contains: name => classes.has(name),

                add: name => classes.add(name), remove: name => classes.delete(name),

                toggle: (name, force) => { const on = force === undefined ? !classes.has(name) : force; if (on) classes.add(name); else classes.delete(name); return on; },
            },
            setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'id') this.id = String(value); },
            getAttribute(name) { return this.attributes[name] === undefined ? null : this.attributes[name]; },
            appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
            append(...kids) { kids.forEach(k => this.appendChild(k)); },
            replaceChildren(...kids) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; kids.forEach(k => this.appendChild(k)); },
            remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); },
            addEventListener() {}, removeEventListener() {},
            querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
            querySelectorAll(sel) {
                const wanted = sel.split(',').map(s => s.trim());
                const out = [];
                const walk = n => n.children.forEach(c => {
                    if (matches(c, wanted)) out.push(c);
                    walk(c);
                });
                if (matches(this, wanted)) out.push(this);
                walk(this);
                for (const w of wanted) {
                    if (w.startsWith('#') && this.id === w.slice(1)) out.push(this);
                }
                walk(this);
                return out;
            },

            // SVG path sampling used to place the light string and bulbs.

            getTotalLength: () => 400,

            getPointAtLength: t => ({ x: 20 + (t % 360) * 3, y: 40 + (t % 120) }),

            getContext: () => ctx(),

            // render() keys off the untransformed layout box.

            get offsetWidth() { return (this.box && this.box.width) || width; },

            get offsetHeight() { return (this.box && this.box.height) || height; },

            getBoundingClientRect() { const w = this.offsetWidth; const h = this.offsetHeight; return { width: w, height: h, top: 0, left: 0, right: w, bottom: h }; },
        };
        // Only the scene root counts a build; nested element creation is normal.
        const origAppend = node.appendChild;
        node.appendChild = function (child) { return origAppend.call(this, child); };
        node.width = width; node.height = height;
        node.style.setProperty = (k, v) => { node.style[k] = v; };
        return node;
    }
    function matches(node, wanted) {
        return wanted.some(w => {
            if (w.startsWith('#')) return node.id === w.slice(1);
            if (w.startsWith('.')) return node.classList.contains(w.slice(1));
            if (w.startsWith('[')) return node.attributes[w.slice(1, -1)] !== undefined;
            return node.tag === w;
        });
    }
    const drawCalls = { clearRect: 0, fill: 0 };
    const ctx = () => ({
        scale() {}, clearRect() { drawCalls.clearRect++; }, fillRect() {}, strokeRect() {},
        beginPath() {}, closePath() {}, arc() {}, moveTo() {}, lineTo() {}, ellipse() {},
        fill() { drawCalls.fill++; }, stroke() {}, save() {}, restore() {}, translate() {}, rotate() {},
        setTransform() {}, drawImage() {}, putImageData() {}, getImageData: () => ({ data: new Uint8ClampedArray(4) }),
        createRadialGradient: () => ({ addColorStop() {} }),
        createLinearGradient: () => ({ addColorStop() {} }),
        set fillStyle(v) {}, get fillStyle() { return ''; },
        set strokeStyle(v) {}, get strokeStyle() { return ''; },
        set globalAlpha(v) {}, get globalAlpha() { return 1; },
        set shadowBlur(v) {}, get shadowBlur() { return 0; },
        set shadowColor(v) {}, get shadowColor() { return ''; },
        set globalCompositeOperation(v) {}, get globalCompositeOperation() { return 'source-over'; },
    });

    const body = element('body');
    if (dark) body.classList.add('dark-mode');
    const card = element('div');
    card.classList.add('login-card');
    const deco = element('div');
    deco.setAttribute('data-xmas-card-deco', '');
    card.appendChild(deco);
    const scene = element('div');
    scene.classList.add('xmas-scene');

    const documentElement = element('html');

    documentElement.classList.add('login-christmas');

    const document = {

        documentElement, body, hidden: false, readyState: 'complete', fonts: null,
        createElementNS: (ns, tag) => element(tag, ns),
        createElement: tag => element(tag),
        addEventListener(name, handler) {
            if (!documentListeners.has(name)) documentListeners.set(name, []);
            documentListeners.get(name).push(handler);
        },
        removeEventListener() {},
        querySelector(sel) {
            if (sel === '.xmas-scene') return scene;
            if (sel === '.page-login .login-card') return card;
            return null;
        },
        querySelectorAll: () => [],
        getElementById: () => null,
    };
    // The deco container resolves its scene by scoped query.
    const origQuery = deco.querySelector.bind(deco);
    deco.querySelector = sel => origQuery(sel);

    const window = {
        innerWidth: width, innerHeight: height,
        getComputedStyle: () => ({ borderTopLeftRadius: '24px', padding: '16px' }),
        matchMedia: query => query.includes('reduced-motion') ? motionQuery : ({
            matches: query.includes('520px') ? mobile : false,
            addEventListener() {}, removeEventListener() {},
        }),
        requestAnimationFrame(cb) { const id = nextId++; frames.set(id, cb); return id; },
        cancelAnimationFrame(id) { frames.delete(id); },
        addEventListener() {}, removeEventListener() {},
        devicePixelRatio: 2,
    };
    const raf = cb => window.requestAnimationFrame(cb);

    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'login-christmas.js'), 'utf8'), {
        document, window, Math, JSON, console,
        requestAnimationFrame: raf, cancelAnimationFrame: id => window.cancelAnimationFrame(id),
        MutationObserver: class {
            constructor(cb) { this.cb = cb; observers.push(cb); }
            observe(target, opts) { this.target = target; this.opts = opts; }
            disconnect() {}
        },
        ResizeObserver: class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} },
        getComputedStyle: window.getComputedStyle,
        setTimeout, clearTimeout,
    });

    // render() rebuilds the scene by replacing both canvases, so the child node


    // identities are the reliable signal that the memo guard actually held.


    const sceneNode = () => deco.children[0];


    const lightsNode = () => deco.children.find(node => node.getAttribute('class') === 'xmas-deco-lights');


    const treeKey = () => deco.children.map(c => c.uid).join('|');
    function toggleTheme() {

        body.classList.toggle('dark-mode');

        observers.forEach(cb => cb([{ type: 'attributes', target: body, attributeName: 'class' }], { attributeFilter: ['class'] }));
    }
    // The card's layout box changes when the viewport resizes.

    const resize = (w, h) => {


        card.box = { width: w, height: h };

        observers.forEach(cb => cb([{ type: 'attributes', target: body, attributeName: 'class' }], { attributeFilter: ['class'] }));

    };

    const stops = id => {
        const g = deco.querySelector('#' + id);
        return g ? g.children.map(s => `${s.getAttribute('stop-color')}:${s.getAttribute('stop-opacity')}`) : null;
    };



    // Drains the pending animation callbacks once, like a single painted frame.




    const frame = () => {




        const pending = [...frames.entries()];




        frames.clear();




        pending.forEach(([, cb]) => cb(performance.now()));




    };





    const switching = on => {






        body.classList[on ? 'add' : 'remove']('theme-switching');






        observers.forEach(cb => cb([{ type: 'attributes', target: body, attributeName: 'class' }], { attributeFilter: ['class'] }));






    };





    return {
        body, documentElement, deco, card, toggleTheme, heavy: treeKey, treeKey, sceneNode, lightsNode,
        resize, frame, switching, frames, stops, drawCalls,
        hidden(value) {
            document.hidden = value;
            (documentListeners.get('visibilitychange') || []).forEach(handler => handler());
        },
        reducedMotion(value) {
            motionQuery.matches = value;
            motionListeners.forEach(handler => handler(motionQuery));
        },
    };
}

test('a theme flip retints gradients instead of rebuilding the scene', () => {
    const app = start({ dark: false });
    const scene = app.sceneNode();
    const lights = app.lightsNode();
    const lightPuff = app.stops('xmas-puff');
    assert.ok(lightPuff && lightPuff.length, 'puff gradient built on first render');

    app.toggleTheme();                 // -> dark
    assert.strictEqual(app.sceneNode(), scene, 'scene canvas node reused, not rebuilt');
    assert.strictEqual(app.lightsNode(), lights, 'lights canvas node reused, not rebuilt');
    const darkPuff = app.stops('xmas-puff');
    assert.notDeepEqual(darkPuff, lightPuff, 'puff palette changed');
    assert.equal(darkPuff[0], '#f4f7ff:1', 'dark puff crown');

    app.toggleTheme();                 // -> light (round trip)
    assert.strictEqual(app.sceneNode(), scene, 'scene canvas still reused on the way back');
    assert.deepEqual(app.stops('xmas-puff'), lightPuff, 'palette restored exactly');
});

test('halo glow opacity follows the theme', () => {
    const app = start({ dark: false });
    const lightHalo = app.stops('xmas-halo-red');
    assert.ok(lightHalo, 'halo gradient built');
    app.toggleTheme();
    const darkHalo = app.stops('xmas-halo-red');
    assert.notDeepEqual(darkHalo, lightHalo, 'halo opacity changed with the theme');
    assert.match(darkHalo[0], /:0\.95$/, 'dark halo is brighter');
    assert.match(lightHalo[0], /:0\.85$/, 'light halo is dimmer');
});

test('the snow loop suspends while the theme is switching and resumes after', () => {
    const app = start({ dark: false });
    app.frame();                       // one painted frame of the running loop
    assert.equal(app.frames.size, 1, 'snow loop scheduled its next frame');

    app.switching(true);               // theme swap snapshot begins
    app.frame();
    assert.equal(app.frames.size, 0, 'snow loop parked while the theme switches');

    app.switching(false);              // swap finished
    app.frame();
    assert.equal(app.frames.size, 1, 'snow loop resumed once the swap finished');
});
test('a genuine resize still rebuilds the scene', () => {
    const app = start({ dark: false });
    const scene = app.sceneNode();

    app.resize(700, 500);              // viewport change, not a theme change
    assert.notStrictEqual(app.sceneNode(), scene, 'geometry change rebuilds the scene');
    assert.ok(app.stops('xmas-puff'), 'gradients rebuilt for the new geometry');
});

function descendants(node) {
    return node.children.flatMap(child => [child, ...descendants(child)]);
}

test('twinkle work is bounded to three glow layers without animating bulb descendants', () => {
    for (const mobile of [false, true]) {
        const app = start({ mobile });
        const glows = app.deco.children.filter(node => node.getAttribute('class') === 'xmas-deco-glows');
        assert.equal(glows.length, 3, 'one cached glow layer per colour, not one animation per bulb');
        for (const layer of glows) {
            assert.ok(layer.style['--twinkle-duration']);
            assert.ok(layer.style['--twinkle-delay']);
            const halos = descendants(layer).filter(node => node.getAttribute('class') === 'xmas-bulb__halo');
            assert.ok(halos.length > 0, 'the animated layer contains visible glows');
            assert.ok(halos.every(node => node.style['--twinkle-duration'] === undefined));
        }
        const bulbs = descendants(app.lightsNode());
        const cores = bulbs.filter(node => node.getAttribute('class') === 'xmas-bulb__core');
        assert.equal(cores.length, mobile ? 18 : 34, 'the original glass bulbs remain visible');
        assert.ok(cores.every(node => node.style['--twinkle-duration'] === undefined));
        assert.ok(bulbs.every(node => node.getAttribute('class') !== 'xmas-bulb__halo'), 'glows stay behind the glass');
    }
});

test('glows pause with the snow while the theme is switching', () => {
    const app = start();
    const tree = app.treeKey();
    app.switching(true);
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), true);
    app.frame();
    assert.equal(app.frames.size, 0);
    app.switching(false);
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), false);
    assert.equal(app.frames.size, 1);
    assert.equal(app.treeKey(), tree, 'pausing must not rebuild the filtered decoration');
});

test('hidden pages park festive effects until they become visible', () => {
    const app = start();
    app.hidden(true);
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), true);
    assert.equal(app.frames.size, 0);
    app.hidden(false);
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), false);
    assert.equal(app.frames.size, 1);
});

test('reduced motion keeps static snow and glows and can change at runtime', () => {
    const app = start({ reducedMotion: true });
    const tree = app.treeKey();
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), true);
    assert.equal(app.frames.size, 0);
    assert.ok(app.drawCalls.clearRect > 0, 'a static snow frame remains visible');
    app.reducedMotion(false);
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), false);
    assert.equal(app.frames.size, 1);
    app.reducedMotion(true);
    assert.equal(app.documentElement.classList.contains('xmas-effects-paused'), true);
    assert.equal(app.frames.size, 0);
    assert.equal(app.treeKey(), tree);
});
