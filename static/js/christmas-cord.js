(() => {
    'use strict';
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const STEP = 1 / 120;
    const displace = (a, b, t, key, home) => (a[key] - a[home]) * (1 - t) + (b[key] - b[home]) * t;

    window.requestHubChristmasCord = function cord(edge, centerAt, bulbs) {
        const spans = new Map();
        const constraints = [];
        for (const [part, side] of [['t', -1], ['b', 1]]) {
            if (edge.lengths[part] < 36) continue;
            const count = Math.ceil(edge.lengths[part] / 18);
            const knots = Array.from({ length: count + 1 }, (_, index) => {
                const u = index / count;
                const { x, y } = centerAt(part, u);
                return { x, y, homeX: x, homeY: y, oldX: x, oldY: y, u, side, fixed: index === 0 || index === count };
            });
            spans.set(part, knots);
            for (let index = 1; index < knots.length; index++) {
                const a = knots[index - 1], b = knots[index];
                constraints.push({ a, b, length: Math.hypot(b.x - a.x, b.y - a.y) });
            }
        }
        const moving = [...spans.values()].flat().filter(knot => !knot.fixed);
        const attachment = (part, u) => {
            const knots = spans.get(part);
            if (!knots) return null;
            const position = u * (knots.length - 1);
            const index = Math.min(Math.floor(position), knots.length - 2);
            return { a: knots[index], b: knots[index + 1], t: position - index };
        };
        const pendulums = bulbs.map(({ spec: [part, u], rest }) => ({
            binding: attachment(part, u), tilt: rest.angle * Math.PI / 180, angle: 0, velocity: 0, speed: 0,
        }));
        let drag = null, active = false, accumulator = 0, quietTime = 0;
        const low = knot => knot.homeY - (knot.side < 0 ? 25 : 5);
        const high = knot => knot.homeY + (knot.side > 0 ? 25 : 5);

        function reset() {
            for (const k of moving) { k.x = k.oldX = k.homeX; k.y = k.oldY = k.homeY; }
            for (const bulb of pendulums) bulb.angle = bulb.speed = bulb.velocity = 0;
            drag = null;
            active = false;
            accumulator = quietTime = 0;
        }
        function step() {
            const held = drag?.knot;
            for (const k of moving) {
                const vx = (k.x - k.oldX) * .963, vy = (k.y - k.oldY) * .963;
                k.oldX = k.x;
                k.oldY = k.y;
                k.x += vx + (k.homeX - k.x) * 18 * STEP ** 2;
                k.y += vy + (k.homeY - k.y) * 28 * STEP ** 2;
            }
            for (let pass = 0; pass < 6; pass++) {
                if (held) {
                    held.x += (drag.x - held.x) * .65;
                    held.y += (drag.y - held.y) * .65;
                }
                for (const { a, b, length } of constraints) {
                    const wa = !a.fixed && a !== held ? 1 : 0, wb = !b.fixed && b !== held ? 1 : 0;
                    if (!wa && !wb) continue;
                    const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(.01, Math.hypot(dx, dy));
                    const correction = (distance - length) * .92 / (distance * (wa + wb));
                    a.x += dx * correction * wa;
                    a.y += dy * correction * wa;
                    b.x -= dx * correction * wb;
                    b.y -= dy * correction * wb;
                }
                for (const k of moving) {
                    k.x = clamp(k.x, k.homeX - 12, k.homeX + 12);
                    k.y = clamp(k.y, low(k), high(k));
                }
            }
            let energy = 0;
            for (const k of moving) {
                energy = Math.max(energy, Math.abs(k.y - k.homeY), Math.abs(k.x - k.homeX),
                    Math.abs(k.y - k.oldY) * 12, Math.abs(k.x - k.oldX) * 12);
            }
            for (const bulb of pendulums) {
                if (!bulb.binding) continue;
                const { a, b, t } = bulb.binding;
                const vx = displace(a, b, t, 'x', 'oldX'), vy = displace(a, b, t, 'y', 'oldY');
                const speed = (vx * Math.cos(bulb.tilt) + vy * Math.sin(bulb.tilt)) / STEP;
                const acceleration = clamp((speed - bulb.speed) / STEP, -1600, 1600);
                bulb.speed = speed;
                bulb.velocity += (-32 * Math.sin(bulb.angle) - 4.8 * bulb.velocity + acceleration * .013) * STEP;
                bulb.angle = clamp(bulb.angle + bulb.velocity * STEP, -.48, .48);
                energy = Math.max(energy, Math.abs(bulb.angle) * 14, Math.abs(bulb.velocity) * 2);
            }
            quietTime = !held && energy < .09 ? quietTime + STEP : 0;
        }
        return {
            attachment,
            shift(point, binding, angle = 0) {
                if (!binding) return point;
                const { a, b, t } = binding;
                return { ...point, x: point.x + displace(a, b, t, 'x', 'homeX'),
                    y: point.y + displace(a, b, t, 'y', 'homeY'), angle: (point.angle ?? 0) + angle };
            },
            angle(index) { return pendulums[index].angle * 180 / Math.PI; },
            advance(seconds) {
                if (!active) return false;
                accumulator += seconds;
                if (accumulator < STEP) return false;
                while (accumulator >= STEP) { step(); accumulator -= STEP; }
                if (quietTime > .18) reset();
                return true;
            },
            get active() { return active; },
            get canFlex() { return moving.length > 0; },
            reset,
            grab(side, x, y) {
                let knot, distance = 31;
                for (const candidate of moving) {
                    if (candidate.side !== side) continue;
                    const gap = Math.hypot(candidate.x - x, candidate.y - y);
                    if (gap < distance) { knot = candidate; distance = gap; }
                }
                if (!knot) return false;
                drag = { knot, x: knot.x, y: knot.y, dx: knot.x - x, dy: knot.y - y };
                active = true;
                quietTime = 0;
                return true;
            },
            move(x, y) {
                if (!drag) return;
                const k = drag.knot;
                drag.x = clamp(x + drag.dx, k.homeX - 12, k.homeX + 12);
                drag.y = clamp(y + drag.dy, low(k), high(k));
            },
            release() { drag = null; },
            nudge() {
                for (const k of moving) {
                    const weight = Math.sin(k.u * Math.PI) ** 1.8;
                    k.oldY -= k.side * weight * (k.side < 0 ? 3.4 : 2.1);
                    k.oldX -= k.side * weight * .92;
                }
                pendulums.forEach((bulb, index) => { if (bulb.binding) bulb.velocity += index % 2 ? 1.55 : -1.55; });
                active = moving.length > 0;
                quietTime = 0;
            },
        };
    };
})();
