(() => {
    'use strict';
    const namespace = 'http://www.w3.org/2000/svg';
    const colors = { r: '#f83d51', g: '#20d779', b: '#258aff', y: '#ffc451', o: '#ff8736' };
    const PARTS = ['t', 'r', 'b', 'l'];
    // Measured from the approved reference: [edge, position along edge, angle from outward normal, colour].
    // Edges run clockwise: top leftâ†’right, right end topâ†’bottom, bottom rightâ†’left, left end bottomâ†’top.
    const BULBS = [
        ['t', 0.0226, 84, 'r'], ['t', 0.1162, -1, 'b'], ['t', 0.1853, -159, 'r'], ['t', 0.2535, 7, 'g'],
        ['t', 0.3558, 60, 'r'], ['t', 0.4045, -80, 'b'], ['t', 0.4571, 55, 'o'], ['t', 0.5117, -127, 'b'],
        ['t', 0.5711, -50, 'r'], ['t', 0.6383, 178, 'b'], ['t', 0.6968, -46, 'y'], ['t', 0.7728, 16, 'g'],
        ['t', 0.8449, -157, 'y'], ['t', 0.8984, -43, 'r'], ['t', 0.9744, -7, 'b'],
        ['r', 0.0821, 37, 'r'], ['r', 0.5674, 33, 'y'], ['r', 0.8786, 0, 'g'],
        ['b', 0.046, 14, 'o'], ['b', 0.1259, -135, 'r'], ['b', 0.198, -18, 'b'], ['b', 0.2711, -52, 'y'],
        ['b', 0.3227, 38, 'g'], ['b', 0.4123, -60, 'r'], ['b', 0.5029, -33, 'y'], ['b', 0.5672, 27, 'g'],
        ['b', 0.6763, -122, 'o'], ['b', 0.7192, 2, 'b'], ['b', 0.7845, -54, 'y'], ['b', 0.8673, 1, 'r'],
        ['b', 0.9413, -166, 'g'], ['b', 0.9618, -3, 'g'],
        ['l', 0.1152, -16, 'r'], ['l', 0.3828, -14, 'b'], ['l', 0.8021, -7, 'o'], ['l', 0.9881, -7, 'g'],
    ];
    // Reference wire distance outside the border in px; negative values sit on top of the navbar edge.
    const WIRE = {
        t: [5.5, 1.5, 3.5, 5.5, 3, 0, 1, 5.5, 3.5, 1, 0, 2, 5, 6.5, 7, 2, -2.5, -3.5, -3, -0.5, 1, -1, -2, -2.5, -1.5, 3, 5, 0.5, -1.5, -0.5, 2, -2, -2, -2, 2, 4.5, 4.5, 2.5, 1, 1.5],
        r: [1.5, 1, 0.5, -2, -2, -2, -2, -1, -1, 1.5, 6, 6],
        b: [6, 5, 9.5, 10.5, 10.5, 10.5, 8.5, -0.5, -2, 1.5, 4, 1.5, 1, 4.5, 6.5, 5, 1.5, -1, -1.5, -1, -1, -4.5, -5, -4.5, -2.5, 2, 3, 0.5, 0.5, 3, 6.5, 1, -1, 2.5, 4, 2.5, -0.5, 2, 3, 3],
        l: [5, 5, 0.5, 0, 0, 0, 0, 0, -0.5, -0.5, -0.5, 2],
    };
    const REFERENCE_HEIGHT = 71.5;
    const round = value => Math.round(value * 100) / 100;
    const random = (min, max) => min + Math.random() * (max - min);

    function svgElement(tag, attributes = {}) {
        const element = document.createElementNS(namespace, tag);
        Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
        return element;
    }

    function perimeter(width, height, radius) {
        const horizontal = width - 2 * radius;
        const vertical = height - 2 * radius;
        const arc = Math.PI * radius / 2;
        const curve = (cx, cy, angle) => {
            const nx = Math.cos(angle);
            const ny = Math.sin(angle);
            return { x: cx + radius * nx, y: cy + radius * ny, nx, ny };
        };
        const parts = {
            t: [[horizontal, u => ({ x: radius + u * horizontal, y: 0, nx: 0, ny: -1 })]],
            r: [
                [arc, u => curve(width - radius, radius, -Math.PI / 2 + u * Math.PI / 2)],
                [vertical, u => ({ x: width, y: radius + u * vertical, nx: 1, ny: 0 })],
                [arc, u => curve(width - radius, height - radius, u * Math.PI / 2)],
            ],
            b: [[horizontal, u => ({ x: width - radius - u * horizontal, y: height, nx: 0, ny: 1 })]],
            l: [
                [arc, u => curve(radius, height - radius, Math.PI / 2 + u * Math.PI / 2)],
                [vertical, u => ({ x: 0, y: height - radius - u * vertical, nx: -1, ny: 0 })],
                [arc, u => curve(radius, radius, Math.PI + u * Math.PI / 2)],
            ],
        };
        const lengths = {};
        const starts = {};
        let total = 0;
        for (const part of PARTS) {
            starts[part] = total;
            lengths[part] = parts[part].reduce((sum, [size]) => sum + Math.max(0, size), 0);
            total += lengths[part];
        }
        return {
            total, lengths, starts,
            point(part, u) {
                let distance = Math.min(1, Math.max(0, u)) * lengths[part];
                const segments = parts[part].filter(([size]) => size > 0);
                for (const [size, point] of segments) {
                    if (distance <= size) return point(distance / size);
                    distance -= size;
                }
                return (segments.at(-1) || parts[part][0])[1](1);
            },
        };
    }

    function wireOffsets(edge, scale) {
        const controls = PARTS.filter(part => edge.lengths[part] > 0).flatMap(part => WIRE[part].map((value, index) => [
            edge.starts[part] + (index + 0.5) / WIRE[part].length * edge.lengths[part], value * scale,
        ]));
        return distance => {
            const s = ((distance % edge.total) + edge.total) % edge.total;
            let next = controls.findIndex(([position]) => position >= s);
            if (next === -1) next = 0;
            const previous = (next - 1 + controls.length) % controls.length;
            const [from, a] = controls[previous];
            const [to, b] = controls[next];
            let span = to - from;
            let travelled = s - from;
            if (span <= 0) span += edge.total;
            if (travelled < 0) travelled += edge.total;
            const amount = span ? travelled / span : 0;
            const eased = (1 - Math.cos(amount * Math.PI)) / 2;
            return a + (b - a) * eased;
        };
    }

    function closedSpline(points) {
        const at = index => points[(index + points.length) % points.length];
        let path = `M${round(points[0].x)} ${round(points[0].y)}`;
        for (let index = 0; index < points.length; index++) {
            const [p0, p1, p2, p3] = [at(index - 1), at(index), at(index + 1), at(index + 2)];
            path += `C${round(p1.x + (p2.x - p0.x) / 6)} ${round(p1.y + (p2.y - p0.y) / 6)} `;
            path += `${round(p2.x - (p3.x - p1.x) / 6)} ${round(p2.y - (p3.y - p1.y) / 6)} ${round(p2.x)} ${round(p2.y)}`;
        }
        return path + 'Z';
    }

    function bulbElement([, , relative, color]) {
        const bulb = svgElement('g', {
            class: Math.abs(relative) > 100 ? 'seasonal-light-bulb seasonal-light-bulb--inward' : 'seasonal-light-bulb',
        });
        bulb.style.setProperty('--bulb-color', colors[color]);
        bulb.style.setProperty('--twinkle-duration', `${round(random(2, 4))}s`);
        bulb.style.setProperty('--twinkle-delay', `${round(random(-4, 0))}s`);
        const emitter = svgElement('g', { class: 'seasonal-light-emitter' });
        emitter.appendChild(svgElement('path', {
            class: 'seasonal-light-glass',
            d: 'M0 -19C-2.6 -17.4-4.2 -13.4-4.2 -10C-4.2 -6.4-2.4 -4.4 0 -4.4C2.4 -4.4 4.2 -6.4 4.2 -10C4.2 -13.4 2.6 -17.4 0 -19Z',
        }));
        emitter.appendChild(svgElement('ellipse', { class: 'seasonal-light-core', cx: '0', cy: '-10.5', rx: '2', ry: '3.8' }));
        emitter.appendChild(svgElement('ellipse', { class: 'seasonal-light-highlight', cx: '-1.3', cy: '-12.4', rx: '0.75', ry: '2.8' }));
        bulb.appendChild(emitter);
        bulb.appendChild(svgElement('rect', { class: 'seasonal-light-socket', x: '-2.3', y: '-5', width: '4.6', height: '5', rx: '0.8' }));
        return bulb;
    }

    const body = document.body;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    function attachLights(surface, login) {
        const interactive = login || surface.classList.contains('rh-primary-nav');
        const nudge = login ? login.nudge : document.getElementById('christmas-nudge-cord');
        const referenceEdge = login ? perimeter(1380, REFERENCE_HEIGHT, 28) : null;
        const theme = () => login ? 'christmas' : surface.dataset.seasonalLightsTheme;
        let overlay = null;
        let wires = [];
        let bulbs = [];
        let tabletLayout;
        let dimensions = '';
        let frame = null;
        let lastTime = null;
        let phase = 0;
        let cord = null;
        let scene = null;
        let pointer = null;
        let hits = [];
        let paused = true;
        let needsUpdate = true;
        let pageHidden = false;
        let speed = 0;
        let lastSpeed;
        let canopy = null;
        let opticsUnavailable = false;
        let opticalLeft;
        let ripple = null;
        function hideCanopy() {
            overlay?.classList.remove('seasonal-lights-overlay--rays');
            canopy?.dispose();
            canopy = ripple = null;
        }
        function loseCanopy() {
            opticsUnavailable = true;
            hideCanopy();
        }
        function paintCanopy(moving) {
            if (opticsUnavailable || !window.requestHubChristmasCanopy) return;
            try {
                canopy ??= window.requestHubChristmasCanopy(loseCanopy);
                const day = login ? !body.classList.contains('dark-mode') : surface.dataset.navSurface !== 'dark';
                const pixelBudget = login && moving ? 130000 : undefined;
                if (!canopy?.draw(scene, opticalLeft, day, ripple, pixelBudget)) {
                    loseCanopy();
                    return;
                }
                if (!canopy.canvas.isConnected) surface.appendChild(canopy.canvas);
                overlay.classList.add('seasonal-lights-overlay--rays');
            } catch (error) {
                loseCanopy();
                console.warn('Christmas lighting fell back to SVG.', error);
            }
        }
        function release() {
            if (!pointer) return;
            const { id, hit } = pointer;
            pointer = null;
            cord?.release();
            hit.classList.remove('is-grabbing');
            if (hit.hasPointerCapture(id)) hit.releasePointerCapture(id);
        }
        function clearHits() {
            release();
            hits.forEach(hit => hit.remove());
            hits = [];
        }
        function remove() {
            if (scene) login?.paint(null);
            hideCanopy();
            clearHits();
            cord = scene = null;
            phase = 0;
            lastTime = null;
            speed = 0;
            lastSpeed = undefined;
            if (nudge) nudge.disabled = true;
            if (overlay) overlay.remove();
            overlay = null;
            wires = [];
            bulbs = [];
            dimensions = '';
            if (!login && surface.classList.contains('seasonal-lights--active')) surface.classList.remove('seasonal-lights--active');
        }
        function draw(width, height, radius, tablet) {
            // Keep the existing emitters connected while the drawer changes height.
            if (!overlay || tablet !== tabletLayout) {
                if (overlay) overlay.remove();
                overlay = svgElement('svg', {
                    class: 'seasonal-lights-overlay',
                    'aria-hidden': 'true', focusable: 'false', preserveAspectRatio: 'none',
                });
                wires = ['seasonal-light-wire', 'seasonal-light-wire seasonal-light-wire--twist'].map(className => {
                    const wire = svgElement('path', { class: className, 'vector-effect': 'non-scaling-stroke' });
                    overlay.appendChild(wire);
                    return wire;
                });
                bulbs = BULBS.filter((_, index) => !tablet || index % 3 !== 2).map(spec => {
                    const bulb = bulbElement(spec);
                    overlay.appendChild(bulb);
                    return { reference: spec, bulb, emitter: bulb.firstElementChild };
                });
                tabletLayout = tablet;
                surface.appendChild(overlay);
            }
            overlay.setAttribute('viewBox', `0 0 ${width} ${height}`);
            const edge = perimeter(width, height, radius);
            const scale = login ? 1 : Math.min(1.3, Math.max(0.8, height / REFERENCE_HEIGHT));
            const reference = referenceEdge || edge;
            const offsetAt = wireOffsets(reference, scale);
            const ratio = reference.total / edge.total;
            const centerAt = (part, u) => {
                const { x, y, nx, ny } = edge.point(part, u);
                const s = edge.starts[part] + u * edge.lengths[part];
                const offset = offsetAt(s * ratio);
                return { x: x + nx * offset, y: y + ny * offset, nx, ny, s };
            };
            for (const lamp of bulbs) {
                let [part, u, relative, color] = lamp.reference;
                if (login) {
                    // Preserve spacing along the loop rather than squashing the navbar's long rows onto the card.
                    const distance = (reference.starts[part] + u * reference.lengths[part]) / ratio;
                    part = PARTS.findLast(key => distance >= edge.starts[key] && edge.lengths[key] > 0);
                    u = Math.min(1, (distance - edge.starts[part]) / edge.lengths[part]);
                }
                lamp.spec = [part, u];
                const rest = centerAt(part, u);
                lamp.rest = { ...rest, angle: Math.atan2(rest.ny, rest.nx) * 180 / Math.PI + 90 + relative };
                lamp.phase = rest.s / edge.total;
                lamp.color = colors[color];
            }
            release();
            cord = theme() === 'christmas' ? window.requestHubChristmasCord?.(edge, centerAt, bulbs) : null;
            for (const lamp of bulbs) lamp.binding = cord?.attachment(lamp.spec[0], lamp.spec[1]);
            const samples = PARTS.flatMap(part => {
                const count = Math.max(8, Math.ceil(edge.lengths[part] / 3));
                return Array.from({ length: count }, (_, i) => {
                    const u = i / count;
                    const point = centerAt(part, u);
                    return { point, binding: cord?.attachment(part, u), twist: 1.1 * Math.sin(point.s / 9 * Math.PI * 2) };
                });
            });
            scene = { width, height, radius, scale, samples, bulbs, paths: [] };
            paintGeometry();
        }
        function paintGeometry() {
            scene.paths = wires.map((wire, index) => {
                const direction = index === 0 ? -1 : 1;
                const path = closedSpline(scene.samples.map(({ point, binding, twist }) => {
                    const p = binding ? cord.shift(point, binding) : point;
                    return { x: p.x + p.nx * twist * direction, y: p.y + p.ny * twist * direction };
                }));
                wire.setAttribute('d', path);
                return path;
            });
            bulbs.forEach((lamp, index) => {
                const p = lamp.binding ? cord.shift(lamp.rest, lamp.binding, cord.angle(index)) : lamp.rest;
                lamp.position = p;
                lamp.bulb.setAttribute('transform', `translate(${round(p.x)} ${round(p.y)}) rotate(${round(p.angle)}) scale(${round(scene.scale)})`);
            });
        }
        function paintLight(speed) {
            bulbs.forEach(lamp => {
                lamp.emission = speed ? .26 + .74 * ((1 + Math.cos((lamp.phase - phase) * 2 * Math.PI)) / 2) ** 3 : 1;
                if (speed || theme() === 'christmas') lamp.emitter.style.opacity = String(lamp.emission);
                else lamp.emitter.style.removeProperty('opacity');
            });
        }
        function localPoint(event) {
            const box = surface.getBoundingClientRect();
            if (login) return [
                (event.clientX - box.left) * surface.offsetWidth / box.width - surface.clientLeft,
                (event.clientY - box.top) * surface.offsetHeight / box.height - surface.clientTop,
            ];
            return [event.clientX - box.left, event.clientY - box.top];
        }
        function installHits() {
            if (hits.length || !cord || !interactive) return;
            for (const side of [-1, 1]) {
                const hit = document.createElement('span');
                hit.className = `seasonal-cord-hit${side === 1 ? ' seasonal-cord-hit--lower' : ''}`;
                hit.setAttribute('aria-hidden', 'true');
                hit.addEventListener('pointerdown', event => {
                    if (event.button !== 0 || pointer || paused || !cord?.grab(side, ...localPoint(event))) return;
                    if (login) event.stopPropagation();
                    const id = event.pointerId;
                    hit.setPointerCapture(id);
                    pointer = { id, hit };
                    hit.classList.add('is-grabbing');
                    schedule();
                });
                hit.addEventListener('pointermove', event => {
                    if (pointer?.id !== event.pointerId || pointer.hit !== hit) return;
                    cord.move(...localPoint(event));
                });
                for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
                    hit.addEventListener(type, event => {
                        if (pointer?.id === event.pointerId && pointer.hit === hit) release();
                    });
                }
                surface.appendChild(hit);
                hits.push(hit);
            }
        }
        // The decoration class changes the navbar's sticky offset and margin, so its
        // border box (getBoundingClientRect) is not a stable cache key: every class
        // write looked like a resize and rebuilt the whole overlay in a loop.
        // offsetWidth/offsetHeight are the untransformed layout box and ignore both,
        // so the key stays stable unless the navbar genuinely changes size.
        function measure() {
            return login ? { width: surface.clientWidth, height: surface.clientHeight }
                : { width: surface.offsetWidth, height: surface.offsetHeight };
        }
        function reconcile() {
            const selectedTheme = theme();
            const christmas = selectedTheme === 'christmas';
            const allowed = login || (surface.classList.contains('seasonal-lights') && (!selectedTheme || body.dataset.accountTheme === selectedTheme)
                && (!christmas || !body.classList.contains('christmas-lights-off')));
            const { width, height } = measure();
            if (!allowed || window.innerWidth < 480 || width < 80 || height < 20) {
                remove();
                return false;
            }
            const outerRadius = parseFloat(window.getComputedStyle(surface).borderTopLeftRadius) || 0;
            const radius = Math.max(0, Math.min(outerRadius - (login ? surface.clientLeft : 0), width / 2, height / 2));
            const tablet = window.innerWidth < 1024;
            const nextDimensions = `${round(width)},${round(height)},${round(radius)},${tablet},${selectedTheme}`;
            if (dimensions !== nextDimensions) {
                dimensions = nextDimensions;
                draw(width, height, radius, tablet);
            }
            if (!login && !surface.classList.contains('seasonal-lights--active')) surface.classList.add('seasonal-lights--active');
            paused = pageHidden || document.hidden || reducedMotion.matches || body.classList.contains('pref-reduce-motion')
                || body.classList.contains('pref-reduce-effects') || body.classList.contains('theme-switching')
                || (login ? login.paused() : christmas && !body.classList.contains('christmas-effects-on'));
            const requested = login ? login.speed() : Number(body.dataset.christmasLightFlow);
            const valid = Number.isInteger(requested) && requested >= 0 && requested <= 20;
            speed = christmas && !paused && valid ? requested : 0;
            if (!requested) phase = 0;
            if (paused && cord?.active) {
                release();
                cord.reset();
                paintGeometry();
            }
            overlay.classList.toggle('seasonal-lights-overlay--paused', paused);
            overlay.classList.toggle('seasonal-lights-overlay--canopy', christmas);
            if (paused || !christmas || !interactive) hideCanopy();
            else if (!opticsUnavailable && window.requestHubChristmasCanopy) opticalLeft = surface.getBoundingClientRect().left + surface.clientLeft;
            if (!paused) installHits();
            if (!cord) clearHits();
            const canNudge = Boolean(cord && cord.canFlex && !paused);
            for (const hit of hits) hit.hidden = !canNudge;
            if (nudge) nudge.disabled = !canNudge;
            return true;
        }
        function update(now) {
            frame = null;
            const changed = needsUpdate;
            if (changed) {
                needsUpdate = false;
                if (!reconcile()) return;
            }
            const elapsed = lastTime === null ? 0 : now - lastTime;
            if (speed && speed === lastSpeed) phase = (phase + Math.min(elapsed, 100) * speed / 48000) % 1;
            lastSpeed = speed;
            const moved = !paused && cord?.advance(Math.min(elapsed || 1000 / 120, 36) / 1000);
            if (moved) paintGeometry();
            if (speed || changed) paintLight(speed);
            const rippling = ripple !== null;
            if (ripple) {
                ripple.age += Math.max(0, elapsed) / 1000;
                if (ripple.age >= 1.2) ripple = null;
            }
            const moving = Boolean(speed || (!paused && (cord?.active || ripple)));
            if (changed || speed || moved) login?.paint(scene);
            if (!paused && theme() === 'christmas' && interactive && (changed || speed || moved || rippling)) paintCanopy(moving);
            if (moving) {
                lastTime = now;
                schedule();
            } else lastTime = null;
        }
        function schedule() {
            if (frame === null) frame = window.requestAnimationFrame(update);
        }
        function invalidate() {
            needsUpdate = true;
            if (pageHidden || document.hidden) {
                if (frame !== null) window.cancelAnimationFrame(frame);
                update(0);
            } else schedule();
        }
        if (nudge && theme() === 'christmas') nudge.addEventListener('click', () => {
            if (paused || !cord) return;
            cord.nudge();
            schedule();
        });
        if (interactive) surface.addEventListener('pointerdown', event => {
            if (!canopy || event.button !== 0 || event.target.closest('a, button, input, label, select, textarea, summary, [role="button"], .seasonal-cord-hit')) return;
            let [x, y] = localPoint(event);
            if (!login) { x -= surface.clientLeft; y -= surface.clientTop; }
            if (x < 0 || y < 0 || x > scene.width || y > scene.height) return;
            ripple = { x, y, age: 0 };
            schedule();
        }, { passive: true });
        if (!login) new MutationObserver(invalidate).observe(surface, { attributes: true, attributeFilter: ['class', 'data-seasonal-lights-theme', 'data-nav-surface'] });
        new MutationObserver(invalidate).observe(body, { attributes: true, attributeFilter: ['class', 'data-account-theme', 'data-christmas-light-flow'] });
        if (typeof ResizeObserver !== 'undefined') new ResizeObserver(invalidate).observe(surface);
        window.addEventListener('resize', invalidate, { passive: true });
        window.addEventListener('pageshow', () => { pageHidden = false; invalidate(); });
        window.addEventListener('pagehide', () => { pageHidden = true; invalidate(); });
        document.addEventListener('visibilitychange', invalidate);
        reducedMotion.addEventListener('change', invalidate);
        invalidate();
        return invalidate;
    }
    document.querySelectorAll('.navbar[data-nav-surface]').forEach(nav => attachLights(nav));
    window.requestHubChristmasCardLights = attachLights;
})();
