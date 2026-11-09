(() => {
    'use strict';
    const body = document.body;
    const root = document.documentElement;
    const login = body.classList.contains('page-login') && !body.classList.contains('authenticated');
    if (!body.classList.contains('authenticated') && !(login && root.classList.contains('login-christmas'))) return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const panel = document.getElementById('christmas-controls');
    const toggle = document.getElementById('christmas-effects-toggle');
    const status = document.getElementById('christmas-effects-status');
    const storageKey = 'reqHubChristmasEffects';
    const effectKeys = ['border', 'snow', 'lights', 'cursor', 'clicks', 'santa'];
    const loginEffects = ['cursor', 'clicks', 'santa'];
    const preferences = Object.fromEntries(['enabled', ...effectKeys].map(key => [key, true]));
    const settings = document.getElementById('christmas-effects-settings');
    const controls = Array.from(document.querySelectorAll('[data-christmas-effect]'));
    function applyPreferences(values) {
        if (!values || typeof values !== 'object') return;
        for (const key of ['enabled', ...effectKeys]) {
            if (typeof values[key] === 'boolean') preferences[key] = values[key];
        }
    }
    if (settings) {
        try { applyPreferences(JSON.parse(settings.textContent)); } catch (_) {}
        // Bound form values can contain an unsaved preview after validation errors.
        controls.forEach(control => applyPreferences({ [control.dataset.christmasEffect]: control.checked }));
    } else if (!login) {
        try { preferences.enabled = window.localStorage.getItem(storageKey) !== 'off'; } catch (_) {}
    }
    let border = null;
    let canvas = null;
    let context = null;
    let frameId = null;
    let lastTime = null;
    let width = 0;
    let height = 0;
    let flakes = [];
    const bursts = new Map();
    let santa = null;
    let santaTimer = null;
    let santaGifts = null;
    let blanketTransition = null;
    const blanketDuration = 1500;
    const blanketEasing = 'cubic-bezier(0.45, 0.05, 0.55, 0.95)';

    const isChristmas = () => login ? root.classList.contains('login-christmas') : body.dataset.accountTheme === 'christmas';
    const reduced = () => media.matches || body.classList.contains('pref-reduce-motion') || body.classList.contains('pref-reduce-effects');
    // The snow loop repaints a full-viewport canvas and the bulbs re-evaluate a
    // non-compositable drop-shadow; both are wasted while the theme is snapshotting.
    const switching = () => body.classList.contains('theme-switching');
    const effectEnabled = effect => isChristmas() && preferences.enabled && preferences[effect] && (!login || loginEffects.includes(effect));
    const canAnimate = effect => effectEnabled(effect) && !reduced() && !switching() && !document.hidden;
    // Santa has its own transition layer, unlike the paused background effects.
    const canFlySanta = () => effectEnabled('santa') && !reduced() && !document.hidden;
    function decorative(className, parent, tag = 'div') {
        const element = document.createElement(tag);
        element.className = className;
        element.setAttribute('aria-hidden', 'true');
        parent.appendChild(element);
        return element;
    }
    function resize() {
        if (!canvas) return;
        width = window.innerWidth;
        height = window.innerHeight;
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = width * ratio;
        canvas.height = height * ratio;
        if (context) context.setTransform(ratio, 0, 0, ratio, 0, 0);
        const count = Math.min(72, Math.max(20, Math.round(width / 20)));
        flakes = Array.from({ length: count }, () => ({
            x: Math.random() * width, y: Math.random() * height,
            radius: 1 + Math.random() * 3, speed: 14 + Math.random() * 28,
            phase: Math.random() * Math.PI * 2,
        }));
    }
    function decorate() {
        if (effectEnabled('border')) {
            if (!border) border = decorative('christmas-frame', body);
        } else if (border) {
            border.remove();
            border = null;
        }
        if (effectEnabled('snow')) {
            if (!canvas) {
                canvas = decorative('christmas-snow', body, 'canvas');
                context = canvas.getContext('2d');
                resize();
            }
        } else if (canvas) {
            pauseSnow();
            canvas.remove();
            canvas = null;
            context = null;
            flakes = [];
        }
    }
    function removeBurst(burst) {
        window.clearTimeout(bursts.get(burst));
        bursts.delete(burst);
        burst.remove();
    }
    function removeSanta() {
        if (santaTimer !== null) window.clearTimeout(santaTimer);
        santaTimer = null;
        if (santa) santa.remove();
        if (santaGifts) santaGifts.remove();
        santa = null;
        santaGifts = null;
    }
    function flySanta(toDark, mode = '') {
        removeSanta();
        const modifier = mode ? ` christmas-santa--${mode}` : '';
        santa = decorative(`christmas-santa christmas-santa--to-${toDark ? 'dark' : 'light'}${modifier}`, body);
        decorative('christmas-santa__blanket', santa, 'span');
        const art = decorative('christmas-santa__art', santa, 'span');
        for (let index = 1; index <= 3; index++) {
            decorative(`christmas-santa__gift christmas-santa__gift--${index}`, art, 'span');
        }
        const layer = santa;
        layer.addEventListener('animationend', event => {
            if (event.target === layer && santa === layer) removeSanta();
        });
        santaTimer = window.setTimeout(() => {
            if (santa !== layer) return;
            if (mode) cancelBlanket(true);
            else removeSanta();
        }, mode ? 5000 : 2300);
        return santa;
    }
    function cancelAnimations(run) {
        run.animations.forEach(animation => {
            try {
                animation.finished.catch(() => {});
                animation.cancel();
            } catch (_) {}
        });
    }
    function cancelBlanket(finishTheme = false) {
        const run = blanketTransition;
        if (!run) return;
        if (finishTheme) run.commit();
        run.cancelled = true;
        run.abort.abort();
        if (run.snapshot) run.snapshot.dispose();
        run.source = null;
        window.clearTimeout(run.timer);
        cancelAnimations(run);
        if (run.transition && typeof run.transition.skipTransition === 'function') {
            try { run.transition.skipTransition(); } catch (_) {}
        }
        removeSanta();
        body.classList.remove('christmas-blanket-switching');
        root.classList.remove('christmas-blanket-transition');
        blanketTransition = null;
    }
    function blanketGeometry(toDark) {
        const width = window.innerWidth;
        // Keep the hem width in sync with --blanket-width in the stylesheet.
        const edge = Math.min(180, Math.max(64, width * 0.16));
        return {
            width, edge,
            from: toDark ? -2 * edge : 2 * edge,
            to: toDark ? width : -width,
            translate: toDark ? [`${-2 * edge}px 0px`, `${width}px 0px`] : [`${2 * edge}px 0px`, `${-width}px 0px`],
            clipPath: toDark ? [`inset(0 ${width + edge}px 0 0)`, `inset(0 ${-edge}px 0 0)`] : [`inset(0 0 0 ${width + edge}px)`, `inset(0 0 0 ${-edge}px)`],
            oldClipPath: toDark ? [`inset(0 0 0 ${-edge}px)`, `inset(0 0 0 ${width + edge}px)`] : [`inset(0 ${-edge}px 0 0)`, `inset(0 ${width + edge}px 0 0)`],
        };
    }
    function giftTimeline(geometry, toDark) {
        // Travel is the shared cubic-bezier progress at each gift's release time.
        return [
            { at: 0.24, travel: 0.124582, spin: -35 },
            { at: 0.43, travel: 0.380836, spin: 30 },
            { at: 0.62, travel: 0.698770, spin: -25 },
        ].map(drop => {
            const x = geometry.from + (geometry.to - geometry.from) * drop.travel;
            const rest = `${x + (toDark ? 24 : -24)}px 0px`;
            return {
                moving: [
                    { offset: 0, translate: `${geometry.from}px 0px` },
                    { offset: drop.at, translate: `${x}px 0px` },
                    { offset: drop.at + 0.32, translate: rest },
                    { offset: 1, translate: rest },
                ],
                falling: [
                    { offset: 0, opacity: 0, transform: 'translateY(0px) scale(0.7)' },
                    { offset: drop.at, opacity: 0, transform: 'translateY(0px) scale(0.7)' },
                    { offset: drop.at + 0.03, opacity: 1, transform: 'translateY(0px) scale(1)', easing: 'ease-in' },
                    { offset: drop.at + 0.25, opacity: 1, transform: `translateY(30vh) rotate(${drop.spin}deg)` },
                    { offset: drop.at + 0.32, opacity: 0, transform: `translateY(42vh) rotate(${drop.spin}deg) scale(0.8)` },
                    { offset: 1, opacity: 0, transform: `translateY(42vh) rotate(${drop.spin}deg) scale(0.8)` },
                ],
            };
        });
    }
    async function revealWithBlanket(run, toDark) {
        run.fallback = true;
        try {
            const helper = window.requestHubChristmasSnapshot;
            if (!helper || !run.source || typeof root.animate !== 'function') {
                run.commit();
                return;
            }
            run.snapshot = await helper.mount(run.source, { signal: run.abort.signal, timeoutMs: 1000 });
            run.source = null;
            if (run.cancelled) { run.snapshot.dispose(); return; }
            const layer = flySanta(toDark, 'fallback');
            const art = layer.querySelector('.christmas-santa__art');
            const gifts = Array.from(art.children);
            const positions = gifts.map(gift => gift.getBoundingClientRect());
            santaGifts = decorative('christmas-gifts', body);
            gifts.forEach((gift, index) => {
                gift.style.left = `${positions[index].left}px`;
                gift.style.top = `${positions[index].top}px`;
                santaGifts.appendChild(gift);
            });
            const timing = { duration: blanketDuration, easing: blanketEasing, fill: 'forwards' };
            const animate = (element, keyframes, easing = blanketEasing) => {
                const animation = element.animate(keyframes, { ...timing, easing });
                run.animations.push(animation);
            };
            run.commit();
            animate(run.snapshot.element, { clipPath: run.geometry.oldClipPath });
            animate(layer, { translate: run.geometry.translate });
            giftTimeline(run.geometry, toDark).forEach((frames, index) => {
                animate(gifts[index], frames.moving, 'linear');
                animate(gifts[index], frames.falling, 'linear');
            });
            await Promise.all(run.animations.map(animation => animation.finished.catch(() => {})));
        } catch (_) {
            run.commit();
        }
    }
    async function playBlanket(toDark, applyTheme) {
        cancelBlanket();
        if (!canFlySanta()) {
            applyTheme();
            return;
        }
        const run = {
            cancelled: false, applied: false, fallback: false, animations: [], transition: null,
            geometry: blanketGeometry(toDark),
            source: window.requestHubChristmasSnapshot ? window.requestHubChristmasSnapshot.capture() : null,
            snapshot: null, abort: new AbortController(), timer: null,
            commit() {
                if (!this.cancelled && !this.applied) {
                    this.applied = true;
                    applyTheme();
                }
            },
        };
        blanketTransition = run;
        run.timer = window.setTimeout(() => { if (blanketTransition === run) cancelBlanket(true); }, 5000);
        body.classList.add('christmas-blanket-switching');
        root.classList.add('christmas-blanket-transition');
        try {
            if (typeof document.startViewTransition !== 'function') {
                await revealWithBlanket(run, toDark);
                return;
            }
            run.transition = document.startViewTransition(() => {
                if (run.cancelled || run.fallback) return;
                run.commit();
                flySanta(toDark, 'snapshot');
            });
            await run.transition.ready;
            if (run.cancelled) return;
            const { clipPath, translate } = run.geometry;
            const timing = { duration: blanketDuration, fill: 'forwards' };
            const animateSnapshot = (keyframes, pseudoElement, easing = blanketEasing) => {
                const animation = root.animate(keyframes, { ...timing, easing, pseudoElement });
                run.animations.push(animation);
                if (!animation.effect || animation.effect.pseudoElement !== pseudoElement) {
                    throw new Error('View-transition animation is not supported');
                }
            };
            animateSnapshot({ clipPath }, '::view-transition-new(root)');
            animateSnapshot({ translate }, '::view-transition-group(christmas-santa)');

            giftTimeline(run.geometry, toDark).forEach((frames, index) => {
                const name = `christmas-gift-${index + 1}`;
                animateSnapshot(frames.moving, `::view-transition-group(${name})`, 'linear');
                animateSnapshot(frames.falling, `::view-transition-new(${name})`, 'linear');
            });
            run.source = null;
            await Promise.all(run.animations.map(animation => animation.finished.catch(() => {})));
            if (run.cancelled) return;
            removeSanta();
            await run.transition.finished.catch(() => {});
        } catch (_) {
            if (!run.cancelled) {
                run.fallback = true;
                cancelAnimations(run);
                if (run.transition && typeof run.transition.skipTransition === 'function') {
                    try { run.transition.skipTransition(); } catch (_) {}
                }
                removeSanta();
                await revealWithBlanket(run, toDark);
            }
        } finally {
            window.clearTimeout(run.timer);
            run.source = null;
            if (run.snapshot) run.snapshot.dispose();
            run.abort.abort();
            cancelAnimations(run);
            if (blanketTransition === run) {
                removeSanta();
                body.classList.remove('christmas-blanket-switching');
                root.classList.remove('christmas-blanket-transition');
                blanketTransition = null;
            }
        }
    }
    window.requestHubChristmasBlanket = { canRun: canFlySanta, play: playBlanket, cancel: cancelBlanket };
    function pauseSnow() {
        if (frameId !== null) window.cancelAnimationFrame(frameId);
        frameId = null;
        lastTime = null;
        if (canvas && context) context.clearRect(0, 0, width, height);
    }
    function pause() {
        cancelBlanket(true);
        pauseSnow();
        for (const burst of bursts.keys()) removeBurst(burst);
        removeSanta();
    }
    function draw(time) {
        frameId = null;
        if (!canAnimate('snow') || !context) return;
        const delta = lastTime === null ? 0 : Math.min((time - lastTime) / 1000, 0.05);
        lastTime = time;
        context.clearRect(0, 0, width, height);
        context.fillStyle = '#ffffff';
        context.strokeStyle = '#ffffff';
        context.shadowColor = body.classList.contains('dark-mode') ? '#bce9ff' : '#507c70';
        context.shadowBlur = 3;
        for (const flake of flakes) {
            flake.y += flake.speed * delta;
            flake.x += Math.sin(time / 1800 + flake.phase) * 9 * delta;
            if (flake.y > height + 8) flake.y = -8;
            if (flake.x > width + 8) flake.x = -8;
            if (flake.x < -8) flake.x = width + 8;
            context.globalAlpha = 0.45 + flake.radius / 9;
            context.beginPath();
            if (flake.radius > 3) {
                for (let arm = 0; arm < 3; arm++) {
                    const angle = arm * Math.PI / 3;
                    const x = Math.cos(angle) * flake.radius * 1.7;
                    const y = Math.sin(angle) * flake.radius * 1.7;
                    context.moveTo(flake.x - x, flake.y - y);
                    context.lineTo(flake.x + x, flake.y + y);
                }
                context.lineWidth = 1.2;
                context.stroke();
            } else {
                context.arc(flake.x, flake.y, flake.radius, 0, Math.PI * 2);
                context.fill();
            }
        }
        context.globalAlpha = 1;
        frameId = window.requestAnimationFrame(draw);
    }
    function reconcile() {
        const active = isChristmas();
        if (panel) panel.hidden = !active;
        if (toggle) toggle.checked = preferences.enabled;
        controls.forEach(control => {
            const key = control.dataset.christmasEffect;
            if (typeof preferences[key] === 'boolean') control.checked = preferences[key];
        });
        decorate();
        const moving = active && preferences.enabled && !reduced();
        if (body.classList.contains('christmas-effects-on') !== moving) body.classList.toggle('christmas-effects-on', moving);
        for (const key of effectKeys) {
            const className = `christmas-${key}-off`;
            const off = active && !effectEnabled(key);
            if (body.classList.contains(className) !== off) body.classList.toggle(className, off);
        }
        if (status) status.textContent = !preferences.enabled
            ? 'All festive effects are off. Your individual selections are kept.'
            : reduced() ? 'Reduced-motion/effects preferences override animation. Your selections are kept.'
                : `${effectKeys.filter(key => preferences[key]).length} of 6 effects selected. Changes preview immediately.`;
        if (!canAnimate('snow')) pauseSnow();
        else if (context && frameId === null) frameId = window.requestAnimationFrame(draw);
        if (!canAnimate('clicks')) for (const burst of bursts.keys()) removeBurst(burst);
        if (!canFlySanta()) {
            cancelBlanket(true);
            removeSanta();
        }
    }
    document.addEventListener('click', event => {
        if (!canAnimate('clicks') || !event.detail || event.button !== 0) return;
        if (bursts.size >= 6) removeBurst(bursts.keys().next().value);
        const burst = decorative('christmas-burst', body);
        burst.style.setProperty('--burst-x', `${event.clientX}px`);
        burst.style.setProperty('--burst-y', `${event.clientY}px`);
        for (let index = 0; index < 12; index++) {
            const particle = decorative('christmas-burst__flake', burst, 'span');
            particle.textContent = '\u2744';
            const angle = index * Math.PI / 6;
            const distance = 28 + Math.random() * 52;
            particle.style.setProperty('--dx', `${Math.cos(angle) * distance}px`);
            particle.style.setProperty('--dy', `${Math.sin(angle) * distance + 24}px`);
            particle.style.setProperty('--spin', `${Math.random() * 160 - 80}deg`);
        }
        bursts.set(burst, window.setTimeout(() => removeBurst(burst), 950));
    }, { passive: true });
    document.addEventListener('change', event => {
        const target = event.target;
        if (!target) return;
        const key = target.dataset && target.dataset.christmasEffect;
        if (key === 'enabled' || effectKeys.includes(key)) {
            applyPreferences({ [key]: target.checked });
            reconcile();
        } else if (target.id === 'theme-checkbox' && !blanketTransition && canFlySanta()) {
            flySanta(target.checked);
        }
    });
    document.addEventListener('requesthub:christmas-effects-preview', event => {
        applyPreferences(event.detail);
        reconcile();
    });
    if (toggle && !toggle.dataset.christmasEffect) toggle.addEventListener('change', () => {
        preferences.enabled = toggle.checked;
        if (!settings) {
            try { window.localStorage.setItem(storageKey, preferences.enabled ? 'on' : 'off'); } catch (_) {}
        }
        reconcile();
    });
    new MutationObserver(reconcile).observe(body, { attributes: true, attributeFilter: ['data-account-theme', 'class'] });
    media.addEventListener('change', reconcile);
    document.addEventListener('visibilitychange', reconcile);
    window.addEventListener('resize', () => {
        cancelBlanket(true);
        resize();
    }, { passive: true });
    window.addEventListener('pagehide', pause);
    window.addEventListener('pageshow', reconcile);
    window.addEventListener('storage', event => {
        if (!settings && !login && event.key === storageKey) { preferences.enabled = event.newValue !== 'off'; reconcile(); }
    });
    reconcile();
})();
