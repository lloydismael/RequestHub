/* Christmas login theme: falling snow behind the card, plus snow, a twisted
   light string and C7 bulbs wrapped around the card. Runs only when <html> has
   `login-christmas`; everything it draws is decorative and ignores pointer events. */
(() => {
    'use strict';
    const root = document.documentElement;
    if (!root.classList.contains('login-christmas')) return;

    const NS = 'http://www.w3.org/2000/svg';
    const PAD = 60;
    const BULBS = {
        red: { core: '#fff4e6', tint: '#ff9d86', main: '#f2251b', deep: '#6e020b', glow: '255, 62, 42' },
        green: { core: '#f0fff2', tint: '#8ef2b0', main: '#0fbf52', deep: '#024f22', glow: '52, 230, 120' },
        amber: { core: '#fffbe8', tint: '#ffe09a', main: '#ffa514', deep: '#8f4400', glow: '255, 184, 60' },
    };
    const COLORS = Object.keys(BULBS);
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const mobileQuery = window.matchMedia('(max-width: 520px)');
    const round = value => Math.round(value * 10) / 10;
    const onChange = (query, handler) => {
        if (query.addEventListener) query.addEventListener('change', handler);
        else if (query.addListener) query.addListener(handler);
    };

    function effectsPaused() {
        return motionQuery.matches || document.hidden || document.body.classList.contains('theme-switching');
    }

    function syncEffectState() {
        root.classList.toggle('xmas-effects-paused', effectsPaused());
    }

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

    function svg(tag, attributes, parent) {
        const element = document.createElementNS(NS, tag);
        Object.entries(attributes || {}).forEach(([key, value]) => element.setAttribute(key, value));
        if (parent) parent.appendChild(element);
        return element;
    }

    /* ---------- Falling snow ---------- */

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

    function startSnow(scene) {
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
        let gust = 0;

        // kind: 'dust' (distant, crisp), 'crystal' (mid-depth dendrite), 'front' (out-of-focus bokeh).
        function makeFlake(kind, anywhere) {
            const front = kind === 'front';
            const isCrystal = kind === 'crystal';
            const radius = front ? 6 + Math.random() * 7
                : isCrystal ? 3.5 + Math.random() * 3.5
                    : 0.8 + Math.pow(Math.random(), 1.8) * 3;
            return {
                kind,
                radius,
                depth: front ? 1.6 : isCrystal ? 1 : 0.35 + radius / 6,
                x: Math.random() * width,
                y: anywhere ? Math.random() * height : -radius * 3 - Math.random() * 40,
                speed: front ? 55 + Math.random() * 35 : isCrystal ? 28 + Math.random() * 18 : 14 + radius * 12 + Math.random() * 14,
                drift: front ? 18 + Math.random() * 22 : 5 + Math.random() * 16,
                sway: 0.35 + Math.random() * 0.8,
                phase: Math.random() * Math.PI * 2,
                wind: 3 + Math.random() * 9,
                angle: Math.random() * Math.PI * 2,
                spin: (Math.random() - 0.5) * 1.6,
                alpha: front ? 0.3 + Math.random() * 0.3 : isCrystal ? 0.55 + Math.random() * 0.35 : 0.4 + Math.random() * 0.55,
            };
        }

        function resize() {
            const ratio = Math.min(window.devicePixelRatio || 1, 2);
            width = window.innerWidth;
            height = window.innerHeight;
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
            const dt = last ? Math.min(0.05, (now - last) / 1000) : 0;
            last = now;
            // Slow, layered gusts push every flake sideways; nearer flakes move more.
            gust = Math.max(0, Math.sin(now * 0.00021) * 0.7 + Math.sin(now * 0.00067 + 1.3) * 0.3) * 34;
            for (const flake of flakes) {
                flake.y += flake.speed * dt;
                flake.x += (flake.wind + gust * flake.depth) * dt;
                flake.angle += flake.spin * dt;
                if (flake.y - flake.radius * 2 > height) Object.assign(flake, makeFlake(flake.kind, false));
                if (flake.x > width + 40) flake.x -= width + 80;
            }
            draw(now);
            // Parking here (rather than only in play) lets an in-flight loop stop
            // immediately instead of re-scheduling itself through the whole swap.
            frame = shouldRun() ? requestAnimationFrame(step) : 0;
        }

        // The theme swap snapshots the page; a full-viewport repaint per frame is
        // wasted work while that snapshot is being taken.
        function shouldRun() {
            return !effectsPaused();
        }

        function play() {
            if (frame || !shouldRun()) return;
            last = 0;
            frame = requestAnimationFrame(step);
        }

        function pause() {
            if (frame) cancelAnimationFrame(frame);
            frame = 0;
        }

        function refresh() {
            queued = 0;
            pause();
            resize();
            draw(0); // reduced motion keeps this static frame
            play();
        }

        refresh();
        window.addEventListener('resize', () => {
            if (!queued) queued = requestAnimationFrame(refresh);
        });
        document.addEventListener('visibilitychange', () => (document.hidden ? pause() : play()));
        // Nothing else restarts the loop after the swap, so watch for the flag clearing.
        new MutationObserver(play).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        onChange(motionQuery, refresh);
        onChange(mobileQuery, refresh);
    }

    /* ---------- Geometry helpers ---------- */

    // Rounded-rectangle perimeter, clockwise from the end of the top-left corner.
    function outline(width, height, radius) {
        const r = Math.max(0, Math.min(radius, width / 2, height / 2));
        const arc = Math.PI * r / 2;
        const corner = (cx, cy, start) => [arc, u => {
            const angle = start + u * Math.PI / 2;
            const nx = Math.cos(angle);
            const ny = Math.sin(angle);
            return { x: cx + r * nx, y: cy + r * ny, nx, ny };
        }];
        const segments = [
            [width - 2 * r, u => ({ x: r + u * (width - 2 * r), y: 0, nx: 0, ny: -1 })],
            corner(width - r, r, -Math.PI / 2),
            [height - 2 * r, u => ({ x: width, y: r + u * (height - 2 * r), nx: 1, ny: 0 })],
            corner(width - r, height - r, 0),
            [width - 2 * r, u => ({ x: width - r - u * (width - 2 * r), y: height, nx: 0, ny: 1 })],
            corner(r, height - r, Math.PI / 2),
            [height - 2 * r, u => ({ x: 0, y: height - r - u * (height - 2 * r), nx: -1, ny: 0 })],
            corner(r, r, Math.PI),
        ].filter(([size]) => size > 0);
        const length = segments.reduce((sum, [size]) => sum + size, 0);
        return {
            r,
            arc,
            length,
            at(distance) {
                let d = ((distance % length) + length) % length;
                for (const [size, point] of segments) {
                    if (d <= size) return point(d / size);
                    d -= size;
                }
                const [, point] = segments[segments.length - 1];
                return point(1);
            },
        };
    }

    // Top edge of a rounded box: left corner, flat run, right corner.
    function topEdge(x0, y0, width, height, radius) {
        const r = Math.max(0, Math.min(radius, width / 2, height / 2));
        const arc = Math.PI * r / 2;
        const run = width - 2 * r;
        const corner = (cx, s, start) => {
            const angle = start + (s / (arc || 1)) * Math.PI / 2;
            const nx = Math.cos(angle);
            const ny = Math.sin(angle);
            return { x: cx + r * nx, y: y0 + r + r * ny, nx, ny };
        };
        return {
            total: arc * 2 + run,
            at(s) {
                if (s < arc) return corner(x0 + r, s, Math.PI);
                if (s <= arc + run) return { x: x0 + r + s - arc, y: y0, nx: 0, ny: -1 };
                return corner(x0 + width - r, s - arc - run, -Math.PI / 2);
            },
        };
    }

    function smoothPath(points, closed) {
        const n = points.length;
        const at = index => (closed ? points[(index + n) % n] : points[Math.max(0, Math.min(n - 1, index))]);
        let d = `M${round(points[0].x)} ${round(points[0].y)}`;
        const last = closed ? n : n - 1;
        for (let index = 0; index < last; index++) {
            const p0 = at(index - 1);
            const p1 = at(index);
            const p2 = at(index + 1);
            const p3 = at(index + 2);
            d += `C${round(p1.x + (p2.x - p0.x) / 6)} ${round(p1.y + (p2.y - p0.y) / 6)} `
                + `${round(p2.x - (p3.x - p1.x) / 6)} ${round(p2.y - (p3.y - p1.y) / 6)} ${round(p2.x)} ${round(p2.y)}`;
        }
        return closed ? `${d}Z` : d;
    }

    const linePath = points => points.map((p, i) => `${i ? 'L' : 'M'}${round(p.x)} ${round(p.y)}`).join('');
    const bump = (s, center, spread) => Math.exp(-Math.pow((s - center) / spread, 2));

    // Wire hooked onto the card edge, sagging under gravity between hooks.
    function wirePoints(shape, random) {
        const hookCount = Math.max(12, Math.round(shape.length / 50));
        const step = shape.length / hookCount;
        const hooks = Array.from({ length: hookCount }, (_, index) => index * step + (random() - 0.5) * step * 0.4);
        const points = [];
        hooks.forEach((start, index) => {
            const end = index + 1 < hooks.length ? hooks[index + 1] : hooks[0] + shape.length;
            const middle = shape.at((start + end) / 2);
            const side = Math.abs(middle.nx) > 0.7;
            const sag = 1.5 + random() * 3.5;
            const bulge = side ? (random() - 0.4) * 5 : (random() - 0.4) * 2.5;
            for (let k = 0; k < 4; k++) {
                const u = k / 4;
                const point = shape.at(start + (end - start) * u);
                const lift = Math.sin(Math.PI * u);
                const out = (k === 0 ? (random() - 0.5) * 1.5 : 0) + bulge * lift;
                points.push({ x: point.x + point.nx * out, y: point.y + point.ny * out + sag * lift });
            }
        });
        return points;
    }

    function sampleWire(path, spacing) {
        const total = path.getTotalLength();
        const count = Math.max(8, Math.round(total / spacing));
        const points = [];
        for (let index = 0; index <= count; index++) {
            const s = (index / count) * total;
            const p = path.getPointAtLength(s % total);
            points.push({ x: p.x, y: p.y, s });
        }
        points.forEach((p, index) => {
            const a = points[Math.max(0, index - 1)];
            const b = points[Math.min(points.length - 1, index + 1)];
            const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
            p.tx = (b.x - a.x) / length;
            p.ty = (b.y - a.y) / length;
        });
        return { points, total };
    }

    /* ---------- SVG defs: snow material, frost, bulb glass, halos ---------- */

    function radial(defs, id, stops, attributes) {
        const gradient = svg('radialGradient', { id, ...(attributes || {}) }, defs);
        stops.forEach(([offset, color, opacity]) => svg('stop', { offset, 'stop-color': color, 'stop-opacity': opacity === undefined ? 1 : opacity }, gradient));
    }

    // The only two defs that depend on the light/dark palette; kept separate so a
    // theme change can retint them in place instead of rebuilding the whole scene.
    const PUFF_STOPS = dark => (dark
        ? [['0', '#f4f7ff'], ['0.5', '#dde6f7'], ['0.8', '#aebdd9'], ['1', '#8494b8']]
        : [['0', '#ffffff'], ['0.55', '#f7faff'], ['0.8', '#dce6f5'], ['1', '#b9c8e2']]);
    const HALO_STOPS = (glow, dark) => [
        ['0', `rgb(${glow})`, dark ? 0.95 : 0.85],
        ['0.12', `rgb(${glow})`, dark ? 0.75 : 0.62],
        ['0.32', `rgb(${glow})`, dark ? 0.32 : 0.24],
        ['0.6', `rgb(${glow})`, dark ? 0.1 : 0.06],
        ['1', `rgb(${glow})`, 0],
    ];
    // Light bounced onto the frosted glass (and whatever sits under it) around each bulb.
    const SPILL_STOPS = (glow, dark) => [
        ['0', `rgb(${glow})`, dark ? 0.46 : 0.3],
        ['0.35', `rgb(${glow})`, dark ? 0.2 : 0.12],
        ['0.7', `rgb(${glow})`, dark ? 0.05 : 0.03],
        ['1', `rgb(${glow})`, 0],
    ];

    function setStops(gradient, stops) {
        if (!gradient) return;
        gradient.replaceChildren(...stops.map(([offset, color, opacity]) =>
            svg('stop', { offset, 'stop-color': color, 'stop-opacity': opacity === undefined ? 1 : opacity })));
    }

    // Recolours just the palette-sensitive gradients; geometry and filters are untouched.
    function retint(root, dark) {
        if (!root) return;
        setStops(root.querySelector('#xmas-puff'), PUFF_STOPS(dark));
        COLORS.forEach(name => {
            setStops(root.querySelector(`#xmas-halo-${name}`), HALO_STOPS(BULBS[name].glow, dark));
            setStops(root.querySelector(`#xmas-spill-${name}`), SPILL_STOPS(BULBS[name].glow, dark));
        });
    }

    function blurFilter(defs, id, deviation) {
        const filter = svg('filter', { id, x: '-20%', y: '-40%', width: '140%', height: '180%' }, defs);
        svg('feGaussianBlur', { stdDeviation: String(deviation) }, filter);
    }

    function buildDefs(parent, dark, width, height, radius) {
        const defs = svg('defs', {}, parent);
        // Each snow puff is lit from above: bright crown, cool blue-grey underside.
        radial(defs, 'xmas-puff', PUFF_STOPS(dark),
        { cx: '0.42', cy: '0.3', r: '0.78', fx: '0.4', fy: '0.24' });

        // Crisp, crystalline edges, fine grain and a few glints over the puffs.
        const texture = svg('filter', { id: 'xmas-snow-tex', x: '-6%', y: '-30%', width: '112%', height: '160%', 'color-interpolation-filters': 'sRGB' }, defs);
        svg('feTurbulence', { type: 'fractalNoise', baseFrequency: '0.85', numOctaves: '2', seed: '5', result: 'edge' }, texture);
        svg('feDisplacementMap', { in: 'SourceGraphic', in2: 'edge', scale: '1.7', xChannelSelector: 'R', yChannelSelector: 'G', result: 'fuzzy' }, texture);
        svg('feTurbulence', { type: 'fractalNoise', baseFrequency: '1.25', numOctaves: '2', seed: '8', result: 'grain' }, texture);
        svg('feColorMatrix', { in: 'grain', type: 'matrix', values: '0 0 0 0.1 0.92  0 0 0 0.1 0.92  0 0 0 0.08 0.94  0 0 0 0 1', result: 'grainLum' }, texture);
        svg('feComposite', { in: 'fuzzy', in2: 'grainLum', operator: 'arithmetic', k1: '1', k2: '0', k3: '0', k4: '0', result: 'grainy' }, texture);
        svg('feTurbulence', { type: 'fractalNoise', baseFrequency: '1.6', numOctaves: '1', seed: '13', result: 'fine' }, texture);
        svg('feColorMatrix', { in: 'fine', type: 'matrix', values: '0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  15 0 0 0 -9.6', result: 'glints' }, texture);
        svg('feComposite', { in: 'glints', in2: 'fuzzy', operator: 'in', result: 'sparkle' }, texture);
        const merge = svg('feMerge', {}, texture);
        svg('feMergeNode', { in: 'grainy' }, merge);
        svg('feMergeNode', { in: 'sparkle' }, merge);

        // Feathery frost creeping in from the glass edges.
        const frost = svg('filter', { id: 'xmas-frost', x: '-10%', y: '-10%', width: '120%', height: '120%' }, defs);
        svg('feGaussianBlur', { in: 'SourceGraphic', stdDeviation: '7', result: 'band' }, frost);
        svg('feTurbulence', { type: 'fractalNoise', baseFrequency: '0.035 0.06', numOctaves: '4', seed: '19', result: 'patches' }, frost);
        svg('feColorMatrix', { in: 'patches', type: 'matrix', values: '0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 2.6 -0.75', result: 'mask' }, frost);
        svg('feComposite', { in: 'band', in2: 'mask', operator: 'in' }, frost);
        const clip = svg('clipPath', { id: 'xmas-card-clip' }, defs);
        svg('rect', { x: '0', y: '0', width, height, rx: radius }, clip);

        blurFilter(defs, 'xmas-soft', 2.6);
        blurFilter(defs, 'xmas-wire-blur', 1.3);
        // Moulded dark-green plastic socket, lit from above.
        const socket = svg('linearGradient', { id: 'xmas-socket-fill', x1: '0', y1: '0', x2: '0', y2: '1' }, defs);
        [['0', '#0d2a16'], ['0.25', '#3f7a4c'], ['0.42', '#1a4426'], ['0.8', '#08200f'], ['1', '#020a05']].forEach(([offset, color]) => svg('stop', { offset, 'stop-color': color }, socket));
        COLORS.forEach(name => {
            const c = BULBS[name];
            // Lit glass: almost white where the filament sits, colour concentrating towards the rim.
            radial(defs, `xmas-glass-${name}`, [['0', '#ffffff'], ['0.14', c.core], ['0.34', c.tint], ['0.62', c.main], ['0.9', c.deep], ['1', c.deep]], { cx: '0.4', cy: '0.5', r: '0.7', fx: '0.36', fy: '0.5' });
            radial(defs, `xmas-halo-${name}`, HALO_STOPS(c.glow, dark));
            radial(defs, `xmas-spill-${name}`, SPILL_STOPS(c.glow, dark));
        });
        radial(defs, 'xmas-core', [['0', '#ffffff', 1], ['0.5', '#fffbe9', 0.95], ['1', '#fff8dc', 0]]);
    }

    /* ---------- Snow ---------- */

    // Walks an edge and heaps round puffs to the requested snow height; lower puffs sit in front.
    function heap(puffs, at, from, to, heightAt, random, limits) {
        let s = from;
        while (s < to) {
            const p = at(s);
            const height = heightAt(s, p);
            if (height < 0.6) {
                s += 1.5;
                continue;
            }
            const r = Math.max(limits.min, Math.min(limits.max, height * (0.42 + random() * 0.22)));
            const jitter = (random() - 0.5) * r * 0.35;
            puffs.push({ x: p.x + p.nx * r * 0.25 - p.ny * jitter, y: p.y + p.ny * r * 0.25 + p.nx * jitter, r });
            let lift = r * 0.25;
            while (height - lift > r * 1.25) {
                lift += r * (0.7 + random() * 0.25);
                const upper = r * (0.72 + random() * 0.3);
                const side = (random() - 0.5) * r * 0.9;
                puffs.push({ x: p.x + p.nx * lift - p.ny * side, y: p.y + p.ny * lift + p.nx * side, r: upper });
            }
            s += r * (0.55 + random() * 0.45);
        }
    }

    function drawPuffs(group, puffs) {
        // Back (higher) puffs first so lower ones overlap them and form shaded crevices.
        puffs.sort((a, b) => a.y - b.y || a.x - b.x).forEach(puff => {
            svg('circle', { cx: round(puff.x), cy: round(puff.y), r: round(puff.r) }, group);
        });
    }

    function snowCap(puffs, shape, width, random) {
        const topRun = width - 2 * shape.r;
        const start = -shape.arc * 0.85;
        const end = topRun + shape.arc * 0.85;
        // Heavy clumps with shallow saddles between them, like snow settled on a ledge.
        const mounds = [];
        for (let s = start + 10 + random() * 16; s < end - 10; s += 26 + random() * 34) {
            mounds.push({ center: s, spread: 10 + random() * 12, height: 8 + random() * 10 });
        }
        heap(puffs, s => shape.at(s), start, end, s => {
            const fade = Math.max(0, Math.min(1, (s - start) / 20, (end - s) / 20));
            const mound = mounds.reduce((sum, m) => sum + m.height * bump(s, m.center, m.spread), 0);
            return (4.5 + mound) * Math.pow(fade, 0.7);
        }, random, { min: 2.2, max: 7.2 });
    }

    function clusterAlong(puffs, shape, s, length, size, random) {
        heap(puffs, d => shape.at(d), s - length / 2, s + length / 2, d => {
            const u = (d - (s - length / 2)) / length;
            return size * Math.sin(Math.PI * u) * (0.75 + random() * 0.5);
        }, random, { min: 1.2, max: 3.6 });
    }

    // Snow on the top edge of the logo box, a field or the button: piled at the ends.
    function dusting(puffs, crystals, box, random) {
        const edge = topEdge(box.x, box.y, box.width, box.height, box.radius);
        const piles = [
            { u: 0.07 + random() * 0.06, spread: 0.07 + random() * 0.05, height: 4.5 + random() * 3 },
            { u: 0.85 + random() * 0.08, spread: 0.07 + random() * 0.05, height: 4.2 + random() * 3.2 },
        ];
        if (random() < 0.6) piles.push({ u: 0.3 + random() * 0.4, spread: 0.03 + random() * 0.03, height: 1.6 + random() * 1.8 });
        const grains = [];
        for (let u = 0; u < 1; u += 0.02 + random() * 0.03) grains.push({ u, spread: 0.006 + random() * 0.01, height: random() * 1.4 });
        heap(puffs, s => edge.at(s), 0, edge.total, (s, p) => {
            const u = s / edge.total;
            const gravity = Math.pow(Math.max(0, -p.ny), 1.6);
            return gravity * (1.1 + piles.reduce((sum, m) => sum + m.height * bump(u, m.u, m.spread), 0)
                + grains.reduce((sum, m) => sum + m.height * bump(u, m.u, m.spread), 0));
        }, random, { min: 0.9, max: 3.4 });
        const count = Math.round(box.width / 5);
        for (let index = 0; index < count; index++) {
            const pile = piles[Math.floor(random() * 2)];
            const u = random() < 0.6 ? pile.u + (random() - 0.5) * pile.spread * 4 : random();
            const x = box.x + box.radius * 0.6 + Math.max(0, Math.min(1, u)) * (box.width - box.radius * 1.2);
            const y = box.y + 1.5 + Math.abs((random() + random() + random() - 1.5) * 5);
            svg('circle', { cx: round(x), cy: round(y), r: round(0.3 + random() * 0.6), opacity: round(0.45 + random() * 0.5) }, crystals);
        }
    }

    function offsetWithin(element, ancestor) {
        let x = 0;
        let y = 0;
        let node = element;
        while (node && node !== ancestor) {
            x += node.offsetLeft;
            y += node.offsetTop;
            node = node.offsetParent;
        }
        return node === ancestor ? { x, y } : null;
    }

    function dustTargets(card) {
        return Array.from(card.querySelectorAll('.login-card__logo, .login-form .form-control, .login-card__submit'))
            .map(element => {
                const offset = offsetWithin(element, card);
                if (!offset || !element.offsetWidth) return null;
                return {
                    x: offset.x,
                    y: offset.y,
                    width: element.offsetWidth,
                    height: element.offsetHeight,
                    radius: parseFloat(getComputedStyle(element).borderTopLeftRadius) || 12,
                };
            })
            .filter(Boolean);
    }

    /* ---------- Bulbs ---------- */

    function bulbAngle(point, width, height, r, random) {
        const cx = Math.max(r, Math.min(width - r, point.x));
        const cy = Math.max(r, Math.min(height - r, point.y));
        const outward = Math.atan2(point.y - cy, point.x - cx);
        if (Math.sin(outward) < -0.6) {
            // Top run: bulbs mostly hang under gravity, a few perk up through the snow.
            return random() < 0.25
                ? -Math.PI / 2 + (random() - 0.5) * 1.4
                : Math.PI / 2 + (random() - 0.5) * 1.1;
        }
        let toDown = Math.PI / 2 - outward;
        toDown = Math.atan2(Math.sin(toDown), Math.cos(toDown));
        return outward + toDown * 0.42 + (random() - 0.5) * 0.9;
    }

    function addBulb(layers, color, x, y, angle, scale) {
        const transform = `translate(${round(x)} ${round(y)}) rotate(${round(angle * 180 / Math.PI)}) scale(${scale})`;
        // Light leaves from the glass centre, not the socket.
        const gx = x + Math.cos(angle) * 11 * scale;
        const gy = y + Math.sin(angle) * 11 * scale;
        // Wide, faint bloom washing the snow scene behind the card.
        svg('circle', { class: 'xmas-bulb__bloom', cx: round(gx), cy: round(gy), r: round(30 * scale), fill: `url(#xmas-halo-${color})` }, layers.halos[color]);
        // Bounce light pooling on the frosted glass, fields and logo near the bulb.
        svg('circle', { cx: round(gx), cy: round(gy), r: round(54 * scale), fill: `url(#xmas-spill-${color})` }, layers.spills[color]);
        // Tight bloom that also colours the snow and wire right around the bulb.
        svg('circle', { class: 'xmas-bulb__halo', cx: '11', cy: '0', r: '17', fill: `url(#xmas-halo-${color})`, transform }, layers.halos[color]);

        const bulb = svg('g', { class: `xmas-bulb xmas-bulb--${color}`, transform }, layers.bulbs);
        svg('path', { d: 'M-0.4 -2.5Q-0.4 -3 0.2 -3H5Q5.7 -3 5.7 -2.4V2.4Q5.7 3 5 3H0.2Q-0.4 3 -0.4 2.5Z', fill: 'url(#xmas-socket-fill)' }, bulb);
        svg('path', { class: 'xmas-socket-ribs', d: 'M1.5 -2.9V2.9M3 -2.9V2.9M4.5 -2.9V2.9' }, bulb);
        // C9 "strawberry" glass: full shoulders tapering to a rounded tip.
        svg('path', { d: 'M5.2 -3.3C7.4 -5.6 11.6 -5.9 14.4 -4C16.6 -2.5 18.1 -1 18.3 0C18.1 1 16.6 2.5 14.4 4C11.6 5.9 7.4 5.6 5.2 3.3Z', fill: `url(#xmas-glass-${color})` }, bulb);
        svg('path', { class: 'xmas-bulb__facets', d: 'M6.8 -2.9Q11.6 -3.6 17 -0.7M6.8 2.9Q11.6 3.6 17 0.7M7 0H16.6' }, bulb);
        // Refracted light gathering on the far rim of the glass.
        svg('path', { class: 'xmas-bulb__rim', d: 'M8.5 4.5Q13.5 4.4 17.3 1' }, bulb);
        svg('ellipse', { class: 'xmas-bulb__core', cx: '9.6', cy: '0', rx: '3.8', ry: '2.1', fill: 'url(#xmas-core)' }, bulb);
        svg('path', { class: 'xmas-bulb__filament', d: 'M7.6 0.6L8.4 -0.6L9.2 0.6L10 -0.6L10.8 0.6L11.6 -0.6' }, bulb);
        svg('ellipse', { class: 'xmas-bulb__shine', cx: '10.2', cy: '-3', rx: '3', ry: '0.65', transform: 'rotate(-8 10.2 -3)' }, bulb);
        svg('circle', { class: 'xmas-bulb__glint', cx: '7.4', cy: '-2.6', r: '0.55' }, bulb);
    }

    /* ---------- Card decoration ---------- */

    function startCardDeco(card, deco) {
        let lastKey = '';
        let lastDark = null;
        deco.style.setProperty('--xmas-deco-pad', `${PAD}px`);

        function canvas(width, height, className) {
            return svg('svg', {
                class: className,
                width: width + PAD * 2,
                height: height + PAD * 2,
                viewBox: `${-PAD} ${-PAD} ${width + PAD * 2} ${height + PAD * 2}`,
                focusable: 'false',
            });
        }

        function render() {
            const width = card.offsetWidth;
            const height = card.offsetHeight;
            if (!width || !height) return;
            const mobile = mobileQuery.matches;
            const dark = document.body.classList.contains('dark-mode');
            const targets = dustTargets(card);
            // Only the palette follows the theme, so a theme flip retints the two
            // gradients that use it rather than rebuilding every canvas and filter.
            const key = JSON.stringify([width, height, mobile, targets]);
            if (key === lastKey) {
                if (dark !== lastDark) {
                    lastDark = dark;
                    retint(deco, dark);
                }
                return;
            }
            lastKey = key;
            lastDark = dark;

            const random = seeded(20261225);
            const radius = parseFloat(getComputedStyle(card).borderTopLeftRadius) || 24;
            const shape = outline(width, height, radius);
            const scene = canvas(width, height, 'xmas-deco-static');
            const glows = COLORS.map((name, index) => {
                const layer = canvas(width, height, 'xmas-deco-glows');
                layer.style.setProperty('--twinkle-duration', `${2.8 + index * 0.7}s`);
                layer.style.setProperty('--twinkle-delay', `${-index * 0.9}s`);
                return layer;
            });
            const lights = canvas(width, height, 'xmas-deco-lights');
            buildDefs(scene, dark, width, height, shape.r);
            deco.replaceChildren(scene, ...glows, lights);

            // Frost haze and crystals creeping in from the glass edges.
            const frostBand = svg('g', { class: 'xmas-frost', 'clip-path': 'url(#xmas-card-clip)' }, scene);
            svg('rect', { x: '0', y: '0', width, height, rx: shape.r, fill: 'none', stroke: '#ffffff', 'stroke-width': '18', filter: 'url(#xmas-frost)' }, frostBand);
            const frost = svg('g', { class: 'xmas-frost-specks' }, scene);
            const speckCount = Math.round(shape.length / (mobile ? 4 : 2.5));
            for (let index = 0; index < speckCount; index++) {
                const p = shape.at(random() * shape.length);
                const depth = 1 + Math.pow(random(), 2.4) * 26;
                svg('circle', {
                    cx: round(p.x - p.nx * depth), cy: round(p.y - p.ny * depth),
                    r: round(0.3 + random() * 0.8), opacity: round(0.3 + random() * 0.65),
                }, frost);
            }

            // Snow on the logo box, fields and button.
            const dustPuffs = [];
            const crystals = svg('g', { class: 'xmas-crystals' }, scene);
            targets.forEach(box => dusting(dustPuffs, crystals, box, random));
            drawPuffs(svg('g', { class: 'xmas-snow', filter: 'url(#xmas-snow-tex)' }, scene), dustPuffs);

            // Puffy snowpack on the top edge and over both top corners, with a contact shadow.
            const capPuffs = [];
            snowCap(capPuffs, shape, width, random);
            const shadow = svg('g', { class: 'xmas-cap-shadow', transform: 'translate(0 3.5)', filter: 'url(#xmas-soft)' }, scene);
            capPuffs.forEach(puff => svg('circle', { cx: round(puff.x), cy: round(puff.y), r: round(puff.r) }, shadow));
            drawPuffs(svg('g', { class: 'xmas-snow', filter: 'url(#xmas-snow-tex)' }, scene), capPuffs);

            // Twisted two-strand wire in front of the snowpack, shadowed onto the glass.
            const guide = svg('path', { d: smoothPath(wirePoints(shape, random), true), fill: 'none', stroke: 'none' }, scene);
            const { points, total } = sampleWire(guide, 1.5);
            const strand = phase => points.map(p => {
                const twist = 0.95 * Math.sin((p.s / 13) * Math.PI * 2 + phase);
                return { x: p.x - p.ty * twist, y: p.y + p.tx * twist };
            });
            const strandA = linePath(strand(0));
            const strandB = linePath(strand(Math.PI));
            svg('path', { class: 'xmas-wire-shadow', d: guide.getAttribute('d'), transform: 'translate(1.5 3)', filter: 'url(#xmas-wire-blur)' }, scene);
            svg('path', { class: 'xmas-wire xmas-wire--back', d: strandB }, scene);
            svg('path', { class: 'xmas-wire', d: strandA }, scene);
            svg('path', { class: 'xmas-wire-highlight', d: strandA, transform: 'translate(-0.3 -0.6)' }, scene);

            // Snow resting on the wire and clinging to the sides and lower corners.
            const tufts = [];
            const topWire = points.filter(p => p.y < shape.r * 0.8 && p.x > 0 && p.x < width);
            const tuftCount = Math.min(mobile ? 6 : 11, topWire.length);
            for (let index = 0; index < tuftCount; index++) {
                const p = topWire[Math.floor((index + 0.2 + random() * 0.6) * topWire.length / tuftCount)];
                const length = 8 + random() * 12;
                for (let d = -length / 2; d <= length / 2; d += 2.2 + random() * 1.5) {
                    const u = (d + length / 2) / length;
                    const r = 1.4 + Math.sin(Math.PI * u) * (1.6 + random() * 1.6);
                    tufts.push({ x: p.x + d, y: p.y - r * 0.55 - 0.6, r });
                }
            }
            const sideSpots = mobile ? [0.35, 0.72] : [0.28, 0.55, 0.82];
            const sideLength = height - shape.r * 2;
            const topRun = width - shape.r * 2;
            [topRun + shape.arc, shape.length - shape.arc - sideLength].forEach(sideStart => {
                sideSpots.forEach(spot => {
                    const s = sideStart + sideLength * (spot + (random() - 0.5) * 0.12);
                    clusterAlong(tufts, shape, s, 14 + random() * 18, 4 + random() * 3, random);
                });
            });
            const bottomLeft = shape.length - sideLength - shape.arc * 1.5;
            const bottomRight = topRun + sideLength + shape.arc * 1.5;
            [bottomLeft, bottomRight].forEach(s => clusterAlong(tufts, shape, s, 22 + random() * 10, 5 + random() * 2, random));
            drawPuffs(svg('g', { class: 'xmas-snow', filter: 'url(#xmas-snow-tex)' }, scene), tufts);

            // Animate cached halo layers; the filtered snow and glass bulbs stay static.
            const layers = {
                spills: Object.fromEntries(COLORS.map((name, index) => [name, svg('g', { class: 'xmas-spills', 'clip-path': 'url(#xmas-card-clip)' }, glows[index])])),
                halos: Object.fromEntries(COLORS.map((name, index) => [name, svg('g', { class: 'xmas-halos' }, glows[index])])),
                bulbs: svg('g', {}, lights),
            };
            const count = mobile ? 22 : 40;
            const spacing = total / count;
            const offset = random() * spacing;
            const scale = mobile ? 1.25 : 1.5;
            let previous = '';
            let placed = 0;
            const limit = mobile ? 18 : 34;
            for (let index = 0; index < count && placed < limit; index++) {
                const at = offset + (index + (random() - 0.5) * 0.5) * spacing;
                const point = guide.getPointAtLength(((at % total) + total) % total);
                // The bottom run is mostly bare wire, like a string hung from the top.
                if (point.y > height - shape.r * 0.6 && point.x > shape.r && point.x < width - shape.r && random() < 0.6) continue;
                const angle = bulbAngle(point, width, height, shape.r, random);
                const choices = COLORS.filter(color => color !== previous);
                const color = choices[Math.floor(random() * choices.length)];
                previous = color;
                addBulb(layers, color, point.x, point.y, angle, scale);
                placed++;
            }
        }

        render();
        if ('ResizeObserver' in window) {
            const observer = new ResizeObserver(render);
            observer.observe(card);
            card.querySelectorAll('.login-card__logo, .login-form .form-control, .login-card__submit').forEach(element => observer.observe(element));
        }
        window.addEventListener('resize', render);
        onChange(mobileQuery, render);
        new MutationObserver(render).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
        window.addEventListener('load', render);
    }

    function init() {
        syncEffectState();
        document.addEventListener('visibilitychange', syncEffectState);
        new MutationObserver(syncEffectState).observe(document.body, { attributes: true, attributeFilter: ['class'] });
        onChange(motionQuery, syncEffectState);
        const scene = document.querySelector('.xmas-scene');
        if (scene) startSnow(scene);
        const card = document.querySelector('.page-login .login-card');
        const deco = card && card.querySelector('[data-xmas-card-deco]');
        if (card && deco) startCardDeco(card, deco);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
