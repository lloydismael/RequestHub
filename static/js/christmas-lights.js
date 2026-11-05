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
                const [size, point] = segments[segments.length - 1];
                return point(size ? 1 : 0);
            },
        };
    }

    function wireOffsets(edge, scale) {
        const controls = PARTS.flatMap(part => WIRE[part].map((value, index) => [
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

    function bulbElement([part, u, relative, color], edge, offsetAt, scale) {
        const base = edge.point(part, u);
        const offset = offsetAt(edge.starts[part] + u * edge.lengths[part]);
        const angle = Math.atan2(base.ny, base.nx) * 180 / Math.PI + 90 + relative;
        const bulb = svgElement('g', {
            class: Math.abs(relative) > 100 ? 'seasonal-light-bulb seasonal-light-bulb--inward' : 'seasonal-light-bulb',
            transform: `translate(${round(base.x + base.nx * offset)} ${round(base.y + base.ny * offset)}) rotate(${round(angle)}) scale(${round(scale)})`,
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

    document.querySelectorAll('.navbar[data-nav-surface]').forEach(nav => {
        let overlay = null;
        let dimensions = '';
        let frame = null;
        function remove() {
            if (overlay) overlay.remove();
            overlay = null;
            dimensions = '';
            if (nav.classList.contains('seasonal-lights--active')) nav.classList.remove('seasonal-lights--active');
        }
        function build(width, height, radius, tablet) {
            overlay = svgElement('svg', {
                class: 'seasonal-lights-overlay', viewBox: `0 0 ${width} ${height}`,
                'aria-hidden': 'true', focusable: 'false', preserveAspectRatio: 'none',
            });
            const edge = perimeter(width, height, radius);
            const scale = Math.min(1.3, Math.max(0.8, height / REFERENCE_HEIGHT));
            const offsetAt = wireOffsets(edge, scale);
            const samples = [];
            for (const part of PARTS) {
                const count = Math.max(8, Math.ceil(edge.lengths[part] / 3));
                for (let index = 0; index < count; index++) {
                    const u = index / count;
                    samples.push({ ...edge.point(part, u), s: edge.starts[part] + u * edge.lengths[part] });
                }
            }
            for (const twist of [-1.1, 1.1]) {
                const points = samples.map(point => {
                    const offset = offsetAt(point.s) + twist * Math.sin(point.s / 9 * Math.PI * 2);
                    return { x: point.x + point.nx * offset, y: point.y + point.ny * offset };
                });
                overlay.appendChild(svgElement('path', {
                    class: twist < 0 ? 'seasonal-light-wire' : 'seasonal-light-wire seasonal-light-wire--twist',
                    d: closedSpline(points), 'vector-effect': 'non-scaling-stroke',
                }));
            }
            BULBS.filter((_, index) => !tablet || index % 3 !== 2)
                .forEach(bulb => overlay.appendChild(bulbElement(bulb, edge, offsetAt, scale)));
            nav.appendChild(overlay);
        }
        function update() {
            frame = null;
            const theme = nav.dataset.seasonalLightsTheme;
            const allowed = nav.classList.contains('seasonal-lights') && (!theme || document.body.dataset.accountTheme === theme)
                && (theme !== 'christmas' || !document.body.classList.contains('christmas-lights-off'));
            const { width, height } = nav.getBoundingClientRect();
            if (!allowed || window.innerWidth < 480 || width < 80 || height < 20) {
                remove();
                return;
            }
            const radius = Math.max(0, Math.min(parseFloat(window.getComputedStyle(nav).borderTopLeftRadius) || 0, width / 2, height / 2));
            const tablet = window.innerWidth < 1024;
            const nextDimensions = `${round(width)},${round(height)},${round(radius)},${tablet}`;
            if (dimensions !== nextDimensions) {
                remove();
                dimensions = nextDimensions;
                build(width, height, radius, tablet);
            }
            if (!nav.classList.contains('seasonal-lights--active')) nav.classList.add('seasonal-lights--active');
            const body = document.body;
            const paused = document.hidden || body.classList.contains('pref-reduce-motion') || body.classList.contains('pref-reduce-effects') || (theme === 'christmas' && !body.classList.contains('christmas-effects-on'));
            overlay.classList.toggle('seasonal-lights-overlay--paused', paused);
        }
        function schedule() {
            if (frame === null) frame = window.requestAnimationFrame(update);
        }
        new MutationObserver(schedule).observe(nav, { attributes: true, attributeFilter: ['class', 'data-seasonal-lights-theme'] });
        new MutationObserver(schedule).observe(document.body, { attributes: true, attributeFilter: ['class', 'data-account-theme'] });
        if (typeof ResizeObserver !== 'undefined') new ResizeObserver(schedule).observe(nav);
        window.addEventListener('resize', schedule, { passive: true });
        window.addEventListener('pageshow', schedule);
        document.addEventListener('visibilitychange', schedule);
        schedule();
    });
})();
