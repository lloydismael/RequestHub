(() => {
    'use strict';
    const body = document.body;
    if (!body.classList.contains('authenticated')) return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const panel = document.getElementById('christmas-controls');
    const toggle = document.getElementById('christmas-effects-toggle');
    const status = document.getElementById('christmas-effects-status');
    const storageKey = 'reqHubChristmasEffects';
    const effectKeys = ['border', 'snow', 'lights', 'cursor', 'clicks', 'santa'];
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
    } else {
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

    const isChristmas = () => body.dataset.accountTheme === 'christmas';
    const reduced = () => media.matches || body.classList.contains('pref-reduce-motion') || body.classList.contains('pref-reduce-effects');
    const effectEnabled = effect => isChristmas() && preferences.enabled && preferences[effect];
    const canAnimate = effect => effectEnabled(effect) && !reduced() && !document.hidden;
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
        santa = null;
    }
    function flySanta(toDark) {
        removeSanta();
        santa = decorative(`christmas-santa christmas-santa--to-${toDark ? 'dark' : 'light'}`, body);
        decorative('christmas-santa__art', santa, 'span');
        santa.addEventListener('animationend', removeSanta);
        santaTimer = window.setTimeout(removeSanta, 2300);
    }
    function pauseSnow() {
        if (frameId !== null) window.cancelAnimationFrame(frameId);
        frameId = null;
        lastTime = null;
        if (canvas && context) context.clearRect(0, 0, width, height);
    }
    function pause() {
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
        if (!canAnimate('santa')) removeSanta();
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
        } else if (target.id === 'theme-checkbox' && canAnimate('santa')) {
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
    window.addEventListener('resize', resize, { passive: true });
    window.addEventListener('pagehide', pause);
    window.addEventListener('pageshow', reconcile);
    window.addEventListener('storage', event => {
        if (!settings && event.key === storageKey) { preferences.enabled = event.newValue !== 'off'; reconcile(); }
    });
    reconcile();
})();
