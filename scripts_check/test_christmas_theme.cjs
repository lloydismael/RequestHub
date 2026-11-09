const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '..', 'static', 'js', 'christmas-theme.js');
const template = fs.readFileSync(path.join(__dirname, '..', 'templates', 'base.html'), 'utf8');
const themeScriptMarker = template.indexOf("const toggle = document.getElementById('theme-checkbox');");
const themeScript = template.slice(template.lastIndexOf('<script>', themeScriptMarker) + '<script>'.length, template.indexOf('</script>', themeScriptMarker));

function start({
    theme = 'christmas', reduced = false, effects = false, stored = null, storageBlocked = false,
    account = null, controlsPresent = true, authenticated = true, login = false, loginChristmas = false,
    nativeTransition = false, narrow = false, holdTransition = false, integrated = false,
    transitionFailure = '', noAnimation = false,
} = {}) {
    const keys = ['enabled', 'border', 'snow', 'lights', 'cursor', 'clicks', 'santa'];
    const preferences = account === null ? null : { ...Object.fromEntries(keys.map(key => [key, true])), ...account };
    const callbacks = {};
    const frames = new Map();
    const timers = new Map();
    const animations = [];
    const transitions = [];
    let observer;
    let nextId = 1;
    let time = 0;
    let storage = stored;
    let colorTheme = null;
    function element() {
        const classes = new Set();
        const node = {
            children: [], dataset: {}, attributes: {}, hidden: false, checked: true,
            classList: {
                contains: name => classes.has(name),
                add: name => classes.add(name),
                remove: name => classes.delete(name),
                toggle(name, value) { if (value) classes.add(name); else classes.delete(name); },
            },
            style: { setProperty() {} },
            setAttribute(name, value) { this.attributes[name] = value; },
            appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
            remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; },
            addEventListener(name, callback) { this[name] = callback; },
            animate(keyframes, options) {
                if (transitionFailure === 'pseudo' && options.pseudoElement === '::view-transition-group(christmas-santa)') throw Error('pseudo-element animation unsupported');
                if (transitionFailure === 'gift-pseudo' && options.pseudoElement === '::view-transition-new(christmas-gift-2)') throw Error('gift animation unsupported');
                let finish;
                let reject;
                const finished = new Promise((resolve, rejectPromise) => { finish = resolve; reject = rejectPromise; });
                const animation = {
                    element: this, keyframes, options, finished, finish,
                    effect: { pseudoElement: transitionFailure === 'ignored-pseudo' ? null : options.pseudoElement || null },
                    cancel() { this.cancelled = true; reject(Error('animation cancelled')); },
                };
                animations.push(animation);
                return animation;
            },
            getContext() { return { clearRect() {}, beginPath() {}, arc() {}, fill() {}, moveTo() {}, lineTo() {}, stroke() {}, setTransform() {} }; },
        };
        if (noAnimation) node.animate = undefined;
        return node;
    }
    const body = element();
    if (authenticated) body.dataset.accountTheme = theme;
    body.classList.toggle('authenticated', authenticated);
    body.classList.toggle('page-login', login);
    body.classList.toggle('pref-reduce-motion', reduced);
    body.classList.toggle('pref-reduce-effects', effects);
    const nav = element();
    const panel = element();
    const toggle = element();
    const themeToggle = element();
    themeToggle.id = 'theme-checkbox';
    const themeLabel = element();
    themeLabel.getBoundingClientRect = () => ({ left: 20, top: 20, width: 40, height: 20 });
    const status = element();
    const controls = Object.fromEntries(keys.map(key => [key, key === 'enabled' ? toggle : element()]));
    if (preferences) {
        for (const [key, control] of Object.entries(controls)) {
            control.dataset.christmasEffect = key;
            control.checked = preferences[key];
        }
    }
    const settings = preferences ? { textContent: JSON.stringify(preferences) } : null;
    const media = { matches: false, addEventListener(name, callback) { this.change = callback; } };
    const documentElement = element();
    documentElement.classList.toggle('login-christmas', loginChristmas);
    const document = {
        body, documentElement, hidden: false,
        createElement: element,
        querySelector: selector => selector === '.navbar' ? nav : selector === 'label[for="theme-checkbox"]' ? themeLabel : null,
        querySelectorAll: selector => {
            if (selector === '[data-christmas-effect]' && preferences && controlsPresent) return Object.values(controls);
            if (selector === '.theme-reveal-overlay') return body.children.filter(child => child.className === 'theme-reveal-overlay');
            return [];
        },
        getElementById: id => {
            if (id === 'theme-checkbox') return themeToggle;
            if (id === 'christmas-effects-settings') return settings;
            return controlsPresent ? ({ 'christmas-controls': panel, 'christmas-effects-toggle': toggle, 'christmas-effects-status': status })[id] || null : null;
        },
        addEventListener(name, callback) { callbacks[name] = callback; },
    };
    if (nativeTransition) {
        document.startViewTransition = callback => {
            if (transitionFailure === 'start') throw Error('view transition unavailable');
            let resolveReady;
            let rejectReady;
            const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
            const transition = {
                ready,
                finished: ready,
                release() {
                    if (this.released) return;
                    this.released = true;
                    if (transitionFailure === 'ready') {
                        rejectReady(Error('view transition was skipped'));
                        return;
                    }
                    callback();
                    resolveReady();
                },
                skipTransition() { this.skipped = true; this.release(); },
            };
            transitions.push(transition);
            if (!holdTransition) queueMicrotask(() => transition.release());
            return transition;
        };
    }
    const window = {
        innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
        matchMedia: query => query.includes('max-width') ? { matches: narrow } : media,
        localStorage: {
            getItem(key) { if (storageBlocked) throw Error('unavailable'); return key === 'reqHubTheme' ? colorTheme : storage; },
            setItem(key, value) { if (storageBlocked) throw Error('unavailable'); if (key === 'reqHubTheme') colorTheme = value; else storage = value; },
        },
        requestAnimationFrame(callback) { const id = nextId++; frames.set(id, callback); return id; },
        cancelAnimationFrame(id) { frames.delete(id); },
        setTimeout(callback, delay) { const id = nextId++; timers.set(id, { callback, due: time + delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(name, callback) { callbacks[name] = callback; },
        dispatchEvent() {},
    };
    const context = {
        document, window, Math, localStorage: window.localStorage, performance: { now: () => 0 },
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        MutationObserver: class { constructor(callback) { observer = callback; } observe() {} },
    };
    if (integrated) vm.runInNewContext(themeScript, context);
    vm.runInNewContext(fs.readFileSync(scriptPath, 'utf8'), context);
    return {
        body, nav, panel, toggle, status, media, frames, timers, document, controls, window, animations, transitions, themeToggle,
        finishAnimations() { animations.forEach(animation => animation.finish()); },
        effect(key, value) { const control = controls[key]; control.checked = value; callbacks.change({ target: control }); },
        preview(values) { if (callbacks['requesthub:christmas-effects-preview']) callbacks['requesthub:christmas-effects-preview']({ detail: values }); },
        storageEvent(value) { callbacks.storage({ key: 'reqHubChristmasEffects', newValue: value }); },
        changeTheme(value) { body.dataset.accountTheme = value; observer(); },
        preference(name, value) { body.classList.toggle(name, value); observer(); },
        switchMode(dark, id = 'theme-checkbox') {
            const target = id === 'theme-checkbox' ? themeToggle : { id };
            target.checked = dark;
            if (integrated && id === 'theme-checkbox') themeToggle.change.call(themeToggle);
            if (callbacks.change) callbacks.change({ target });
        },
        santas() { return body.children.filter(child => String(child.className).startsWith('christmas-santa')); },
        click(x = 100, y = 100, detail = 1) { if (callbacks.click) callbacks.click({ clientX: x, clientY: y, detail, button: 0 }); },
        visibility(hidden) { document.hidden = hidden; callbacks.visibilitychange(); },
        resize(width, height) { window.innerWidth = width; window.innerHeight = height; callbacks.resize(); },
        frame() { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(16)); },
        expire(delay = Infinity) {
            time = delay === Infinity
                ? Math.max(time, ...[...timers.values()].map(timer => timer.due))
                : time + delay;
            for (const [id, timer] of [...timers]) {
                if (timer.due <= time) { timers.delete(id); timer.callback(); }
            }
        },
        get storage() { return storage; },
    };
}

test('other themes create no festive decorations or animation', () => {
    const app = start({ theme: 'blue' });
    assert.equal(app.body.children.length, 0);
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.frames.size, 0);
    assert.equal(app.panel.hidden, true);
});

test('Christmas creates decorations and starts snow; cancelling preview cleans up', () => {
    const app = start({ theme: 'default' });
    app.changeTheme('christmas');
    assert.ok(app.body.children.some(child => child.className === 'christmas-snow'));
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.frames.size, 1);
    assert.equal(app.panel.hidden, false);
    app.click();
    assert.ok(app.body.children.some(child => child.className === 'christmas-burst'));
    app.changeTheme('default');
    assert.equal(app.body.children.length, 0);
    assert.equal(app.nav.children.length, 0);
    assert.equal(app.frames.size, 0);
    assert.equal(app.timers.size, 0);
});

test('Christmas border is a single image frame without the old garland baubles', () => {
    const app = start();
    const frame = app.body.children.find(child => child.className === 'christmas-frame');
    assert.ok(frame);
    assert.equal(frame.attributes['aria-hidden'], 'true');
    assert.equal(frame.children.length, 0);
});

test('effects toggle persists on this device and suspends snow and click bursts', () => {
    const app = start();
    app.toggle.checked = false;
    app.toggle.change();
    assert.equal(app.storage, 'off');
    assert.equal(app.frames.size, 0);
    assert.equal(app.body.classList.contains('christmas-effects-on'), false);
    app.click();
    assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    app.toggle.checked = true;
    app.toggle.change();
    assert.equal(app.frames.size, 1);
    assert.equal(app.storage, 'on');
});

test('motion and effects preferences override festive animation including OS changes', () => {
    for (const options of [{ reduced: true }, { effects: true }, { stored: 'off' }]) {
        const app = start(options);
        assert.equal(app.frames.size, 0);
        app.click();
        assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    }
    const app = start();
    app.media.matches = true;
    app.media.change();
    assert.equal(app.frames.size, 0);
    app.media.matches = false;
    app.media.change();
    assert.equal(app.frames.size, 1);
    app.preference('pref-reduce-motion', true);
    assert.equal(app.frames.size, 0);
});

test('hidden pages pause and resume a single animation loop', () => {
    const app = start();
    app.visibility(true);
    assert.equal(app.frames.size, 0);
    app.visibility(false);
    app.frame();
    assert.equal(app.frames.size, 1);
    app.changeTheme('blue');
    app.visibility(false);
    assert.equal(app.frames.size, 0);
});

test('the snow loop suspends while the theme is switching and resumes after', () => {
    const app = start();
    app.frame();
    assert.equal(app.frames.size, 1);
    app.preference('theme-switching', true);
    assert.equal(app.frames.size, 0);
    app.frame();
    assert.equal(app.frames.size, 0);
    app.preference('theme-switching', false);
    assert.equal(app.frames.size, 1);
});
test('click bursts are bounded, expire, and ignore keyboard clicks', () => {
    const app = start();
    app.click(0, 0, 0);
    assert.equal(app.timers.size, 0);
    for (let index = 0; index < 50; index++) app.click();
    assert.ok(app.body.children.filter(child => child.className === 'christmas-burst').length <= 6);
    assert.ok(app.timers.size <= 6);
    app.expire();
    assert.equal(app.body.children.filter(child => child.className === 'christmas-burst').length, 0);
});

test('light/dark toggle sends one Santa sleigh across in the matching direction', () => {
    const app = start();
    app.switchMode(true);
    let santas = app.santas();
    assert.equal(santas.length, 1);
    assert.equal(santas[0].className, 'christmas-santa christmas-santa--to-dark');
    assert.equal(santas[0].attributes['aria-hidden'], 'true');
    assert.ok(santas[0].children.some(child => child.className === 'christmas-santa__art'));
    app.switchMode(false);
    santas = app.santas();
    assert.equal(santas.length, 1);
    assert.equal(santas[0].className, 'christmas-santa christmas-santa--to-light');
    app.expire();
    assert.equal(app.santas().length, 0);
    app.switchMode(true, 'christmas-effects-toggle-other');
    assert.equal(app.santas().length, 0);
});

test('Santa carries a decorative blanket in both theme directions', () => {
    for (const dark of [true, false]) {
        const app = start();
        app.switchMode(dark);
        const santa = app.santas()[0];
        const blanket = santa.children.find(child => child.className === 'christmas-santa__blanket');
        assert.ok(blanket, 'Santa must pull a blanket during the theme change');
        assert.equal(blanket.attributes['aria-hidden'], 'true');
    }
});

test('Santa drops decorative gifts without ending the blanket transition', () => {
    for (const dark of [true, false]) {
        const app = start();
        app.switchMode(dark);
        const santa = app.santas()[0];
        const art = santa.children.find(child => child.className === 'christmas-santa__art');
        const gifts = art.children.filter(child => child.className.startsWith('christmas-santa__gift'));
        assert.equal(gifts.length, 3, 'Santa should carry three falling gifts');
        assert.ok(gifts.every(gift => gift.attributes['aria-hidden'] === 'true'));
        santa.animationend({ target: gifts[0] });
        assert.equal(app.santas().length, 1, 'a gift finishing must not remove Santa early');
        santa.animationend({ target: santa });
        assert.equal(app.santas().length, 0);
    }
});

test('Santa and the theme reveal sweep together in both directions', async () => {
    for (const dark of [true, false]) {
        const app = start({ nativeTransition: true });
        const blanket = app.window.requestHubChristmasBlanket;
        assert.ok(blanket && typeof blanket.play === 'function', 'the Christmas blanket transition must be available');
        let theme = 'unchanged';
        const pending = blanket.play(dark, () => { theme = dark ? 'dark' : 'light'; });
        await new Promise(setImmediate);

        assert.equal(theme, dark ? 'dark' : 'light');
        assert.equal(app.santas().length, 1);
        const reveal = app.animations.find(animation => animation.options.pseudoElement === '::view-transition-new(root)');
        const sleigh = app.animations.find(animation => animation.options.pseudoElement === '::view-transition-group(christmas-santa)');
        assert.ok(reveal, 'the new theme must be revealed across the page');
        assert.ok(sleigh, 'Santa must travel with the edge of the reveal');
        assert.equal(reveal.options.duration, sleigh.options.duration);
        assert.equal(reveal.options.easing, sleigh.options.easing);
        assert.deepEqual(Array.from(reveal.keyframes.clipPath), dark
            ? ['inset(0 1380px 0 0)', 'inset(0 -180px 0 0)']
            : ['inset(0 0 0 1380px)', 'inset(0 0 0 -180px)']);
        assert.deepEqual(Array.from(sleigh.keyframes.translate), dark
            ? ['-360px 0px', '1200px 0px']
            : ['360px 0px', '-1200px 0px']);

        app.finishAnimations();
        await pending;
        assert.equal(app.santas().length, 0);
        assert.ok(app.animations.every(animation => animation.cancelled), 'finished view-transition animations must not remain attached');
    }
});

test('native transitions animate falling gifts alongside the moving sleigh', async () => {
    for (const dark of [true, false]) {
        const app = start({ nativeTransition: true });
        const pending = app.window.requestHubChristmasBlanket.play(dark, () => {});
        await new Promise(setImmediate);
        const sleigh = app.animations.find(animation => animation.options.pseudoElement === '::view-transition-group(christmas-santa)');
        for (let index = 1; index <= 3; index++) {
            const movingGift = app.animations.find(animation => animation.options.pseudoElement === `::view-transition-group(christmas-gift-${index})`);
            const fallingGift = app.animations.find(animation => animation.options.pseudoElement === `::view-transition-new(christmas-gift-${index})`);
            assert.ok(movingGift, `gift ${index} must follow Santa until release`);
            assert.ok(fallingGift, `gift ${index} must fall during the transition`);
            const releases = [0.24, 0.43, 0.62];
            const positions = [-165.65, 234.10, 730.08];
            const release = movingGift.keyframes.find(frame => frame.offset === releases[index - 1]);
            assert.ok(release);
            assert.ok(Math.abs(parseFloat(release.translate) - (dark ? positions[index - 1] : -positions[index - 1])) < 1);
            assert.equal(fallingGift.options.duration, sleigh.options.duration);
            assert.ok(fallingGift.keyframes.some(frame => frame.opacity === 1));
            assert.ok(fallingGift.keyframes.some(frame => String(frame.transform).includes('translateY(')));
            assert.equal(fallingGift.keyframes.at(-1).opacity, 0);
        }
        app.finishAnimations();
        await pending;
        assert.equal(app.santas().length, 0);
        assert.ok(app.animations.every(animation => animation.cancelled));
    }
});

test('managed gift animations remain visible until the transition finishes', async () => {
    const app = start({ nativeTransition: true });
    const pending = app.window.requestHubChristmasBlanket.play(true, () => {});
    await new Promise(setImmediate);
    app.expire(2300);
    assert.equal(app.santas().length, 1, 'a slow transition must not lose Santa or the gifts early');
    app.finishAnimations();
    await pending;
    assert.equal(app.santas().length, 0);
});

test('a stalled gift transition eventually applies the theme and removes decorations', async () => {
    const app = start({ integrated: true, nativeTransition: true });
    app.switchMode(true);
    await new Promise(setImmediate);
    assert.equal(app.santas().length, 1);
    app.expire(5000);
    await new Promise(setImmediate);
    assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
    assert.equal(app.santas().length, 0);
    assert.equal(app.body.classList.contains('theme-switching'), false);
});

test('the real theme toggle uses the Christmas blanket reveal and saves the chosen mode', async () => {
    const app = start({ integrated: true, nativeTransition: true });
    app.switchMode(true);
    await new Promise(setImmediate);
    assert.equal(app.body.classList.contains('dark-mode'), true);
    assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
    const reveal = app.animations.find(animation => animation.options.pseudoElement === '::view-transition-new(root)');
    const sleigh = app.animations.find(animation => animation.options.pseudoElement === '::view-transition-group(christmas-santa)');
    assert.ok(reveal.keyframes.clipPath[0].startsWith('inset('), 'the toggle must use a sweeping blanket instead of the circular reveal');
    assert.ok(sleigh, 'the real toggle must synchronize Santa with the sweep');
    app.finishAnimations();
    await new Promise(setImmediate);
    assert.equal(app.santas().length, 0);
    assert.equal(app.body.classList.contains('theme-switching'), false);
});

test('rapid theme toggles keep the last choice even if an older snapshot updates late', async () => {
    for (const firstReady of [false, true]) {
        const app = start({ integrated: true, nativeTransition: true, holdTransition: true });
        app.switchMode(true);
        if (firstReady) {
            app.transitions[0].release();
            await new Promise(setImmediate);
            assert.equal(app.body.classList.contains('dark-mode'), true);
        }
        app.switchMode(false);
        assert.equal(app.transitions[0].skipped, true);
        app.transitions[1].release();
        await new Promise(setImmediate);
        assert.equal(app.body.classList.contains('dark-mode'), false);
        assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'light');
        assert.equal(app.themeToggle.checked, false);
        assert.equal(app.santas().length, 1);
        app.finishAnimations();
        await new Promise(setImmediate);
        assert.equal(app.santas().length, 0);
        assert.equal(app.body.classList.contains('theme-switching'), false);
    }
});

test('the real switch skips Santa outside Christmas or when festive motion is disabled', async () => {
    for (const options of [
        { theme: 'blue' }, { account: { enabled: false } }, { account: { santa: false } },
        { reduced: true }, { effects: true },
    ]) {
        const app = start({ ...options, integrated: true, nativeTransition: true });
        app.switchMode(true);
        await new Promise(setImmediate);
        assert.equal(app.body.classList.contains('dark-mode'), true);
        assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
        assert.equal(app.santas().length, 0);
        assert.equal(app.animations.some(animation => animation.options.pseudoElement === '::view-transition-group(christmas-santa)'), false);
        app.finishAnimations();
        await new Promise(setImmediate);
        assert.equal(app.body.classList.contains('theme-switching'), false);
    }
});

test('the switch honors the operating system reduced-motion setting', async () => {
    const app = start({ integrated: true, nativeTransition: true });
    app.media.matches = true;
    app.media.change();
    app.switchMode(true);
    await new Promise(setImmediate);
    assert.equal(app.body.classList.contains('dark-mode'), true);
    assert.equal(app.santas().length, 0);
    assert.equal(app.animations.length, 0);
    assert.equal(app.body.classList.contains('theme-switching'), false);
});

test('the remembered Christmas login uses the same blanket sweep', async () => {
    const app = start({
        integrated: true, nativeTransition: true, authenticated: false,
        login: true, loginChristmas: true, controlsPresent: false,
    });
    app.switchMode(true);
    await new Promise(setImmediate);
    assert.equal(app.body.classList.contains('dark-mode'), true);
    assert.ok(app.animations.some(animation => animation.options.pseudoElement === '::view-transition-group(christmas-santa)'));
    assert.equal(app.santas().length, 1);
    app.finishAnimations();
    await new Promise(setImmediate);
    assert.equal(app.santas().length, 0);
});

test('disabling festive motion during a pending sweep finishes the selected theme immediately', async () => {
    for (const stop of [
        app => app.effect('santa', false),
        app => app.effect('enabled', false),
        app => app.preference('pref-reduce-motion', true),
        app => app.visibility(true),
        app => app.changeTheme('blue'),
    ]) {
        const app = start({ integrated: true, nativeTransition: true, holdTransition: true, account: {} });
        app.switchMode(true);
        assert.equal(app.body.classList.contains('dark-mode'), false, 'the native update has not run yet');
        stop(app);
        assert.equal(app.body.classList.contains('dark-mode'), true, 'the selected theme must not be lost');
        assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
        assert.equal(app.transitions[0].skipped, true);
        assert.equal(app.santas().length, 0);
        await new Promise(setImmediate);
        assert.equal(app.body.classList.contains('theme-switching'), false);
    }
});

test('resizing during a pending sweep completes the selected theme and removes the old snapshot', async () => {
    const app = start({ integrated: true, nativeTransition: true, holdTransition: true });
    app.switchMode(true);
    app.resize(375, 812);
    assert.equal(app.body.classList.contains('dark-mode'), true);
    assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
    assert.equal(app.transitions[0].skipped, true);
    await new Promise(setImmediate);
    assert.equal(app.body.classList.contains('theme-switching'), false);
    assert.equal(app.santas().length, 0);
});

test('phones and unsupported browsers reveal the theme underneath a moving blanket', async () => {
    for (const options of [{ nativeTransition: true, narrow: true }, { nativeTransition: false }]) {
        for (const dark of [true, false]) {
            const app = start(options);
            const blanket = app.window.requestHubChristmasBlanket;
            let theme;
            let blanketsAtSwitch;
            const pending = blanket.play(dark, () => {
                blanketsAtSwitch = app.santas().length;
                theme = dark ? 'dark' : 'light';
            });
            const santa = app.santas()[0];
            assert.equal(theme, dark ? 'dark' : 'light');
            assert.equal(blanketsAtSwitch, 1, 'the blanket must cover the page before the theme changes');
            assert.ok(santa.className.includes('christmas-santa--fallback'));
            assert.equal(app.transitions.length, 0, 'the lightweight fallback must avoid page snapshots');
            const motion = app.animations.find(animation => animation.element === santa);
            assert.ok(motion, 'the blanket must move to reveal the page');
            assert.deepEqual(Array.from(motion.keyframes.translate), dark
                ? ['0px 0px', '1200px 0px']
                : ['0px 0px', '-1200px 0px']);
            app.finishAnimations();
            await pending;
            assert.equal(app.santas().length, 0);
        }
    }
});

test('rapid mobile switches replace the falling gifts and keep the last theme', async () => {
    const app = start({ integrated: true, nativeTransition: true, narrow: true });
    app.switchMode(true);
    const first = app.santas()[0];
    app.switchMode(false);
    const santa = app.santas()[0];
    assert.notEqual(santa, first);
    assert.equal(app.santas().length, 1);
    assert.ok(santa.className.includes('christmas-santa--to-light'));
    const art = santa.children.find(child => child.className === 'christmas-santa__art');
    assert.equal(art.children.length, 3);
    assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'light');
    app.finishAnimations();
    await new Promise(setImmediate);
    assert.equal(app.santas().length, 0);
    assert.equal(app.body.classList.contains('theme-switching'), false);
});

test('failed native transitions fall back to the lightweight blanket without losing the theme', async () => {
    for (const failure of ['start', 'ready', 'pseudo', 'gift-pseudo', 'ignored-pseudo']) {
        const app = start({ integrated: true, nativeTransition: true, transitionFailure: failure });
        app.switchMode(true);
        await new Promise(setImmediate);
        assert.equal(app.body.classList.contains('dark-mode'), true, failure);
        assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
        const santa = app.santas()[0];
        assert.ok(santa && santa.className.includes('christmas-santa--fallback'), failure);
        assert.ok(app.animations.some(animation => animation.element === santa), failure);
        app.finishAnimations();
        await new Promise(setImmediate);
        assert.equal(app.santas().length, 0);
        assert.equal(app.body.classList.contains('theme-switching'), false);
    }
});

test('the theme still changes when web animations are unavailable', async () => {
    const app = start({ integrated: true, noAnimation: true });
    app.switchMode(true);
    await new Promise(setImmediate);
    assert.equal(app.body.classList.contains('dark-mode'), true);
    assert.equal(app.window.localStorage.getItem('reqHubTheme'), 'dark');
    assert.equal(app.santas().length, 0);
    assert.equal(app.body.classList.contains('theme-switching'), false);
});

test('Santa safety cleanup waits for the faster flight then removes it without animationend', () => {
    for (const dark of [true, false]) {
        const app = start();
        app.switchMode(dark);
        app.expire(1900);
        assert.equal(app.santas().length, 1);
        app.expire(400);
        assert.equal(app.santas().length, 0);
        assert.equal(app.timers.size, 0);
    }
});

test('Santa flyover respects theme, effects toggle, and motion preferences', () => {
    for (const options of [{ theme: 'blue' }, { reduced: true }, { effects: true }, { stored: 'off' }]) {
        const app = start(options);
        app.switchMode(true);
        assert.equal(app.santas().length, 0);
    }
    const app = start();
    app.switchMode(true);
    app.changeTheme('default');
    assert.equal(app.santas().length, 0);
    assert.equal(app.timers.size, 0);
});

test('saved account choices override the legacy device switch on every page', () => {
    const app = start({ account: {}, stored: 'off', controlsPresent: false });
    assert.equal(app.frames.size, 1);
    app.storageEvent('off');
    assert.equal(app.frames.size, 1);
    const paused = start({ account: { enabled: false }, stored: 'on' });
    assert.equal(paused.body.children.length, 0);
    assert.equal(paused.frames.size, 0);
});

test('border and snowfall can be switched independently of click and Santa animation', () => {
    const app = start({ account: { border: false } });
    assert.equal(app.body.children.some(child => child.className === 'christmas-frame'), false);
    assert.equal(app.frames.size, 1);
    app.effect('snow', false);
    assert.equal(app.frames.size, 0);
    assert.equal(app.body.children.some(child => child.className === 'christmas-snow'), false);
    app.click();
    assert.ok(app.body.children.some(child => child.className === 'christmas-burst'));
    app.switchMode(true);
    assert.equal(app.santas().length, 1);
    app.effect('border', true);
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
});

test('cursor and navbar light choices do not disable the other festive effects', () => {
    const app = start({ account: { cursor: false, lights: false } });
    assert.ok(app.body.classList.contains('christmas-cursor-off'));
    assert.ok(app.body.classList.contains('christmas-lights-off'));
    assert.equal(app.frames.size, 1);
    app.effect('cursor', true);
    app.effect('lights', true);
    assert.equal(app.body.classList.contains('christmas-cursor-off'), false);
    assert.equal(app.body.classList.contains('christmas-lights-off'), false);
    app.changeTheme('blue');
    assert.equal(app.body.classList.contains('christmas-lights-off'), false);
});

test('disabling clicks or Santa cleans up a running effect without stopping snowfall', () => {
    const app = start({ account: {} });
    app.click();
    app.switchMode(true);
    app.effect('clicks', false);
    assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    assert.equal(app.santas().length, 1);
    app.effect('santa', false);
    assert.equal(app.santas().length, 0);
    assert.equal(app.timers.size, 0);
    assert.equal(app.frames.size, 1);
    app.click();
    app.switchMode(false);
    assert.equal(app.body.children.some(child => child.className === 'christmas-burst'), false);
    assert.equal(app.santas().length, 0);
});

test('master off removes all effects but restores the individual selections when enabled', () => {
    const app = start({ account: { snow: false, santa: false } });
    app.click();
    app.effect('enabled', false);
    assert.equal(app.body.children.length, 0);
    assert.equal(app.timers.size, 0);
    assert.ok(app.body.classList.contains('christmas-lights-off'));
    assert.equal(app.controls.border.checked, true);
    assert.equal(app.controls.snow.checked, false);
    assert.equal(app.controls.santa.checked, false);
    app.effect('enabled', true);
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
    assert.equal(app.frames.size, 0);
    app.switchMode(true);
    assert.equal(app.santas().length, 0);
});

test('preview changes and cancellation reconcile all effects without changing device storage', () => {
    const app = start({ account: {} });
    app.preview({ border: false, snow: false, santa: false });
    assert.equal(app.body.children.length, 0);
    app.switchMode(true);
    assert.equal(app.santas().length, 0);
    app.preview({ border: true, snow: true, santa: true });
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
    assert.equal(app.frames.size, 1);
    app.switchMode(false);
    assert.equal(app.santas().length, 1);
    assert.equal(app.storage, null);
});

test('account effect changes still respect accessibility and hidden-page preferences', () => {
    const app = start({ account: { santa: false }, reduced: true });
    app.effect('snow', true);
    app.click();
    app.switchMode(true);
    assert.equal(app.frames.size, 0);
    assert.equal(app.timers.size, 0);
    app.preference('pref-reduce-motion', false);
    assert.equal(app.frames.size, 1);
    app.switchMode(true);
    assert.equal(app.santas().length, 0);
    app.visibility(true);
    app.click();
    app.switchMode(false);
    assert.equal(app.frames.size, 0);
    assert.equal(app.timers.size, 0);
});

const christmasLogin = { authenticated: false, login: true, loginChristmas: true, controlsPresent: false };

test('Christmas login enables pointer and Santa effects without a second snow loop or border', () => {
    const app = start({ ...christmasLogin, stored: 'off' });
    assert.equal(app.body.classList.contains('christmas-effects-on'), true, 'the active login theme is the gate, not a stale legacy switch');
    assert.equal(app.body.classList.contains('christmas-cursor-off'), false);
    app.storageEvent('off');
    assert.equal(app.body.classList.contains('christmas-effects-on'), true, 'legacy storage events must not override the remembered login theme');
    for (const effect of ['border', 'snow', 'lights']) {
        assert.equal(app.body.classList.contains(`christmas-${effect}-off`), true);
    }
    assert.equal(app.body.children.length, 0);
    assert.equal(app.frames.size, 0);
    app.preview({ border: true, snow: true, lights: true });
    app.click();
    const burst = app.body.children.find(child => child.className === 'christmas-burst');
    assert.ok(burst);
    assert.equal(burst.attributes['aria-hidden'], 'true');
    assert.equal(burst.children.length, 12);
    app.switchMode(true);
    assert.equal(app.santas().length, 1);
    assert.equal(app.frames.size, 0, 'login owns the existing snowfall renderer');
    assert.ok(app.body.children.every(child => child.className !== 'christmas-snow' && child.className !== 'christmas-frame'));
});

test('authenticated login-success pages retain their account Christmas effects', () => {
    const app = start({ login: true, controlsPresent: false, account: {} });
    assert.equal(app.body.classList.contains('christmas-effects-on'), true);
    assert.ok(app.body.children.some(child => child.className === 'christmas-frame'));
    assert.ok(app.body.children.some(child => child.className === 'christmas-snow'));
    assert.equal(app.frames.size, 1);
});

test('standard login and other anonymous pages create no festive interactions', () => {
    for (const options of [{ authenticated: false }, { ...christmasLogin, loginChristmas: false }]) {
        const app = start(options);
        app.click();
        app.switchMode(true);
        assert.equal(app.body.children.length, 0);
        assert.equal(app.frames.size, 0);
        assert.equal(app.timers.size, 0);
        assert.equal(app.body.classList.contains('christmas-effects-on'), false);
    }
});

test('Christmas login interactions respect reduced motion and reduced effects', () => {
    for (const preferences of [{ reduced: true }, { effects: true }]) {
        const app = start({ ...christmasLogin, ...preferences });
        app.click();
        app.switchMode(true);
        assert.equal(app.body.classList.contains('christmas-effects-on'), false);
        assert.equal(app.body.children.length, 0);
        assert.equal(app.frames.size, 0);
        assert.equal(app.timers.size, 0);
    }
});

test('login click bursts stay bounded, ignore keyboard clicks, and clean up', () => {
    const app = start(christmasLogin);
    app.click(100, 100, 0);
    assert.equal(app.timers.size, 0);
    for (let index = 0; index < 50; index++) app.click();
    assert.equal(app.body.children.filter(child => child.className === 'christmas-burst').length, 6);
    assert.equal(app.timers.size, 6);
    assert.equal(app.frames.size, 0);
    app.expire();
    assert.equal(app.body.children.length, 0);
    assert.equal(app.timers.size, 0);
});

test('Santa survives the theme reveal while background snowfall stays paused', () => {
    for (const options of [{}, christmasLogin]) {
        const app = start(options);
        app.preference('theme-switching', true);
        app.switchMode(true);
        assert.equal(app.santas().length, 1, 'a toggle must start Santa even when the theme reveal has begun');
        const santa = app.santas()[0];
        app.preference('dark-mode', true);
        assert.equal(app.santas()[0], santa, 'theme reconciliation must not cancel the flight');
        assert.equal(app.frames.size, 0);
        app.preference('theme-switching', false);
        assert.equal(app.santas()[0], santa);
        app.switchMode(false);
        assert.equal(app.santas().length, 1);
        assert.equal(app.santas()[0].className, 'christmas-santa christmas-santa--to-light');
        app.expire();
        assert.equal(app.santas().length, 0);
    }
});

test('hiding Christmas login removes running flakes and Santa without restarting extra snow', () => {
    const app = start(christmasLogin);
    app.click();
    app.switchMode(true);
    assert.equal(app.santas().length, 1);
    assert.equal(app.timers.size, 2);
    app.visibility(true);
    assert.equal(app.body.children.length, 0);
    assert.equal(app.timers.size, 0);
    app.visibility(false);
    assert.equal(app.frames.size, 0);
});

test('blocked local storage does not prevent preview or effects toggling', () => {
    const app = start({ storageBlocked: true });
    assert.equal(app.frames.size, 1);
    app.toggle.checked = false;
    assert.doesNotThrow(() => app.toggle.change());
    assert.equal(app.frames.size, 0);
});
