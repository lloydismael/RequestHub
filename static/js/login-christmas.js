(() => {
    'use strict';
    if (!document.documentElement.classList.contains('login-christmas')) return;

    const body = document.body;
    const scene = document.querySelector('.xmas-scene');
    const card = document.querySelector('.login-card');
    const controls = document.querySelector('.xmas-controls');
    if (!scene || !card || !controls) return;

    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const mobileQuery = window.matchMedia('(max-width: 520px)');
    const lightsQuery = window.matchMedia('(min-width: 480px)');
    const pauseKey = 'reqHubLoginChristmasMotionPaused';
    const flow = document.getElementById('login-light-flow');
    const flowValue = document.getElementById('login-light-flow-value');
    const nudge = document.getElementById('login-nudge-cord');
    const pause = document.getElementById('login-pause-motion');
    const help = document.getElementById('login-motion-help');
    let paused = false, pageHidden = false;
    try { paused = localStorage.getItem(pauseKey) === '1'; } catch (_) {}
    const reduced = () => motionQuery.matches || body.classList.contains('pref-reduce-motion') || body.classList.contains('pref-reduce-effects');
    const dark = () => body.classList.contains('dark-mode');
    const hidden = () => pageHidden || document.hidden;
    const parked = () => hidden() || body.classList.contains('theme-switching');

    function seeded(seed) {
        let state = seed >>> 0;
        return () => {
            state = (state + 0x6D2B79F5) >>> 0;
            let t = state;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    function flakeSprite(size, stops) {
        const sprite = document.createElement('canvas');
        sprite.width = sprite.height = size;
        const context = sprite.getContext('2d');
        const r = size / 2;
        const gradient = context.createRadialGradient(r, r, 0, r, r, r);
        stops.forEach(([offset, alpha]) => gradient.addColorStop(offset, `rgba(255, 255, 255, ${alpha})`));
        context.fillStyle = gradient;
        context.fillRect(0, 0, size, size);
        return sprite;
    }

    // Six-armed dendrite drawn once and stamped for the mid-depth crystal flakes.
    function crystalSprite(size) {
        const sprite = document.createElement('canvas');
        sprite.width = sprite.height = size;
        const context = sprite.getContext('2d');
        const r = size / 2;
        context.translate(r, r);
        context.strokeStyle = 'rgba(255, 255, 255, 0.95)';
        context.lineCap = 'round';
        context.shadowColor = 'rgba(255, 255, 255, 0.9)';
        context.shadowBlur = size * 0.06;
        context.lineWidth = size * 0.045;
        for (let arm = 0; arm < 6; arm++) {
            context.save();
            context.rotate(arm * Math.PI / 3);
            context.beginPath();
            context.moveTo(0, 0);
            context.lineTo(0, -r * 0.82);
            [0.36, 0.58].forEach((at, index) => {
                const branch = r * (index ? 0.17 : 0.24);
                context.moveTo(0, -r * at);
                context.lineTo(-branch, -r * at - branch);
                context.moveTo(0, -r * at);
                context.lineTo(branch, -r * at - branch);
            });
            context.stroke();
            context.restore();
        }
        context.fillStyle = 'rgba(255, 255, 255, 0.95)';
        context.beginPath();
        context.arc(0, 0, size * 0.06, 0, Math.PI * 2);
        context.fill();
        return sprite;
    }

    function startSnow() {
        const canvas = document.createElement('canvas');
        canvas.className = 'xmas-snow';
        canvas.setAttribute('aria-hidden', 'true');
        scene.appendChild(canvas);
        const context = canvas.getContext('2d');
        if (!context) return;
        // Soft-edged sprites: crisp distant flakes and out-of-focus bokeh flakes up front.
        const crisp = flakeSprite(32, [[0, 1], [0.35, 0.9], [0.62, 0.35], [1, 0]]);
        const bokeh = flakeSprite(64, [[0, 0.5], [0.55, 0.38], [0.8, 0.14], [1, 0]]);
        const crystal = crystalSprite(64);

        let width = 0;
        let height = 0;
        let flakes = [];
        let frame = 0;
        let last = 0;
        let queued = 0;
        let time = 0;
        let random = seeded(2712);

        // kind: 'dust' (distant, crisp), 'crystal' (mid-depth dendrite), 'front' (out-of-focus bokeh).
        function makeFlake(kind, anywhere) {
            const front = kind === 'front';
            const isCrystal = kind === 'crystal';
            const radius = front ? 6 + random() * 7
                : isCrystal ? 3.5 + random() * 3.5
                    : 0.8 + Math.pow(random(), 1.8) * 3;
            return {
                kind,
                radius,
                depth: front ? 1.6 : isCrystal ? 1 : 0.35 + radius / 6,
                x: random() * width,
                y: anywhere ? random() * height : -radius * 3 - random() * 40,
                speed: front ? 55 + random() * 35 : isCrystal ? 28 + random() * 18 : 14 + radius * 12 + random() * 14,
                drift: front ? 18 + random() * 22 : 5 + random() * 16,
                sway: 0.35 + random() * 0.8,
                phase: random() * Math.PI * 2,
                wind: 3 + random() * 9,
                angle: random() * Math.PI * 2,
                spin: (random() - 0.5) * 1.6,
                alpha: front ? 0.3 + random() * 0.3 : isCrystal ? 0.55 + random() * 0.35 : 0.4 + random() * 0.55,
            };
        }

        function resize() {
            width = scene.clientWidth;
            height = scene.clientHeight;
            random = seeded(2712);
            time = 0;
            const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(1800000 / (width * height)));
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(height * ratio);
            context.setTransform(ratio, 0, 0, ratio, 0, 0);
            const count = mobileQuery.matches ? 55 : (width < 1100 ? 85 : 110);
            const frontCount = mobileQuery.matches ? 3 : 7;
            const crystalCount = mobileQuery.matches ? 5 : 12;
            const make = (length, kind) => Array.from({ length }, () => makeFlake(kind, true));
            // Far to near, so nearer flakes paint over distant ones.
            flakes = make(count, 'dust').sort((a, b) => a.radius - b.radius)
                .concat(make(crystalCount, 'crystal'), make(frontCount, 'front'));
        }

        function draw(time) {
            context.clearRect(0, 0, width, height);
            for (const flake of flakes) {
                const x = flake.x + Math.sin(flake.phase + time * 0.001 * flake.sway) * flake.drift;
                context.globalAlpha = flake.alpha;
                if (flake.kind === 'crystal') {
                    const size = flake.radius * 2.6;
                    // Tumbling: the flake foreshortens as it turns edge-on.
                    const tilt = 0.55 + 0.45 * Math.abs(Math.cos(flake.angle * 0.7));
                    context.save();
                    context.translate(x, flake.y);
                    context.rotate(flake.angle);
                    context.scale(1, tilt);
                    context.drawImage(crystal, -size / 2, -size / 2, size, size);
                    context.restore();
                    continue;
                }
                const size = flake.radius * (flake.kind === 'front' ? 2.2 : 3);
                context.drawImage(flake.kind === 'front' ? bokeh : crisp, x - size / 2, flake.y - size / 2, size, size);
            }
            context.globalAlpha = 1;
        }

        function step(now) {
            frame = 0;
            if (!shouldRun()) return;
            if (last && now - last < 1000 / 32) {
                frame = requestAnimationFrame(step);
                return;
            }
            const dt = last ? Math.min(0.05, (now - last) / 1000) : 0;
            last = now;
            time += dt * 1000;
            // Slow, layered gusts push every flake sideways; nearer flakes move more.
            const gust = Math.max(0, Math.sin(time * 0.00021) * 0.7 + Math.sin(time * 0.00067 + 1.3) * 0.3) * 34;
            for (const flake of flakes) {
                flake.y += flake.speed * dt;
                flake.x += (flake.wind + gust * flake.depth) * dt;
                flake.angle += flake.spin * dt;
                if (flake.y - flake.radius * 2 > height) Object.assign(flake, makeFlake(flake.kind, false));
                if (flake.x > width + 40) flake.x -= width + 80;
            }
            draw(time);
            frame = requestAnimationFrame(step);
        }

        function shouldRun() { return dark() && !paused && !reduced() && !parked(); }
        function sync() {
            if (shouldRun()) {
                if (!frame) {
                    last = 0;
                    frame = requestAnimationFrame(step);
                }
            } else if (frame) {
                cancelAnimationFrame(frame);
                frame = 0;
            }
        }
        function refresh() {
            queued = 0;
            resize();
            draw(time);
            sync();
        }
        refresh();
        window.addEventListener('resize', () => {
            if (!queued) queued = requestAnimationFrame(refresh);
        }, { passive: true });
        return sync;
    }

    function startSpill() {
        const canvas = document.createElement('canvas');
        canvas.className = 'xmas-light-spill';
        const context = canvas.getContext('2d');
        if (!context) return () => {};
        scene.appendChild(canvas);
        const sprites = new Map();
        function sprite(color) {
            if (!sprites.has(color)) {
                const image = document.createElement('canvas');
                image.width = image.height = 128;
                const ctx = image.getContext('2d');
                const light = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
                light.addColorStop(0, color);
                light.addColorStop(.28, `${color}9e`);
                light.addColorStop(.62, `${color}29`);
                light.addColorStop(1, `${color}00`);
                ctx.fillStyle = light;
                ctx.fillRect(0, 0, 128, 128);
                sprites.set(color, image);
            }
            return sprites.get(color);
        }
        function stamp(image, x, y, rx, ry, angle, alpha) {
            context.save();
            context.translate(x, y);
            context.rotate(angle);
            context.globalAlpha = alpha;
            context.drawImage(image, -rx, -ry, rx * 2, ry * 2);
            context.restore();
        }
        let current = null;
        function draw() {
            if (hidden()) return;
            if (!current || !dark() || !lightsQuery.matches) {
                if (canvas.width !== 1) canvas.width = canvas.height = 1;
                return;
            }
            const bounds = canvas.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return;
            const ratio = Math.min(1, Math.sqrt(850000 / (bounds.width * bounds.height)));
            const width = Math.max(1, Math.floor(bounds.width * ratio));
            const height = Math.max(1, Math.floor(bounds.height * ratio));
            if (canvas.width !== width) canvas.width = width;
            if (canvas.height !== height) canvas.height = height;
            context.setTransform(1, 0, 0, 1, 0, 0);
            context.clearRect(0, 0, width, height);

            const box = card.getBoundingClientRect();
            const sx = box.width / card.offsetWidth, sy = box.height / card.offsetHeight;
            const left = box.left - bounds.left + card.clientLeft * sx;
            const top = box.top - bounds.top + card.clientTop * sy;
            context.setTransform(width / bounds.width, 0, 0, height / bounds.height, 0, 0);
            for (const { position, color, emission } of current.bulbs) {
                const { x, y, nx, ny, angle } = position;
                const radians = angle * Math.PI / 180;
                const dx = Math.sin(radians), dy = -Math.cos(radians);
                const tipX = left + (x + dx * 10.5 * current.scale) * sx;
                const tipY = top + (y + dy * 10.5 * current.scale) * sy;
                const image = sprite(color);
                const outward = .72 + .28 * (1 + nx * dx + ny * dy) / 2;
                stamp(image, tipX + nx * 25, tipY + ny * 25 + 5, 83, 56,
                    Math.atan2(ny, nx), .21 * emission * outward);
                // A wider, shallow lobe suggests bounce in the snow below the card.
                if (ny > 0) stamp(image, tipX + dx * 9 + nx * 18, tipY + ny * 49, 65, 34,
                    nx * -.22, .30 * emission * ny * outward);
            }
        }
        let queued = 0;
        function queueDraw() {
            if (!current || !dark() || parked() || queued) return;
            queued = requestAnimationFrame(() => { queued = 0; draw(); });
        }
        window.addEventListener('scroll', queueDraw, { passive: true });
        controls.addEventListener('toggle', queueDraw);
        for (const type of ['animationend', 'transitionend']) {
            card.addEventListener(type, event => { if (event.target === card) queueDraw(); });
        }
        return next => { current = next; draw(); };
    }

    // Set page visibility before the shared renderer handles the same event.
    window.addEventListener('pagehide', () => { pageHidden = true; sync(); });
    window.addEventListener('pageshow', () => { pageHidden = false; sync(); });
    const syncSnow = startSnow();
    const invalidate = window.requestHubChristmasCardLights?.(card, {
        nudge,
        paused: () => paused,
        speed: () => flow.valueAsNumber,
        paint: startSpill(),
    });
    function sync() {
        body.classList.toggle('xmas-login-motion-paused', paused);
        const still = reduced();
        const hasLights = lightsQuery.matches && Boolean(invalidate);
        const description = still ? 'Reduced-motion or visual-effects settings keep this scene still.'
            : paused ? 'Motion is paused. Resume to animate the decorations.'
            : hasLights ? 'Adjust how fast the light travels around the card.' : '';
        const value = flow.valueAsNumber ? `${flow.valueAsNumber * 5}%` : 'Steady';
        flow.setAttribute('aria-valuetext', value);
        flowValue.textContent = value;
        flow.disabled = !hasLights || still || paused;
        pause.disabled = still;
        pause.textContent = paused ? 'Resume motion' : 'Pause motion';
        help.textContent = description;
        help.hidden = !description;
        syncSnow?.();
    }
    flow.addEventListener('input', () => { sync(); invalidate?.(); });
    pause.addEventListener('click', () => {
        paused = !paused;
        try { localStorage.setItem(pauseKey, paused ? '1' : '0'); } catch (_) {}
        sync();
        invalidate?.();
    });
    new MutationObserver(sync).observe(body, { attributes: true, attributeFilter: ['class'] });
    motionQuery.addEventListener('change', sync);
    lightsQuery.addEventListener('change', sync);
    document.addEventListener('visibilitychange', sync);
    sync();
    controls.hidden = false;
})();
