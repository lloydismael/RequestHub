/* Native-first table scrolling plus optional page wheel easing.
    Wide tables keep native wheel, trackpad, touch, keyboard, and scrollbar input.
    Mouse drag-to-pan is frame-batched and stops immediately on release.

   Approach: wheel input accumulates a target offset; a single rAF loop eases the
   scroll position toward the target with exponential damping scaled by the real
   frame delta (dt), so motion speed is identical at 60Hz, 120Hz, 144Hz+, etc.

   - k = 1 - exp(-dt / TAU)  (frame-rate independent approach factor)
   - loop self-terminates when |remaining| < EPS (no idle CPU between scrolls)
   - drag fling: pointer velocity decays via v *= exp(-LAMBDA * dt)

   Guards: disabled under prefers-reduced-motion; ignores editable fields,
   Bootstrap modals, nested scrollers (Select2 etc.), and data-no-smooth subtrees.
   Style/guards mirror liquid-glass.js. */
(function () {
    'use strict';

    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
        (document.body && document.body.classList.contains('pref-reduce-motion'));
    if (reduceMotion) {
        return;
    }

    /* Time constant (seconds) for wheel approach: lower = snappier, higher = floatier. */
    var TAU = 0.085;
    /* Fling decay rate (1/seconds) for drag momentum. */
    var LAMBDA = 4.2;
    /* Stop threshold in px. */
    var EPS = 0.75;
    /* Cap per-wheel-tick delta so huge driver deltas don't fling the page. */
    var MAX_TICK = 900;
    /* Pointer must move this far before a drag is claimed (keeps clicks working). */
    var DRAG_THRESHOLD = 4;

    function clampTick(delta) {
        if (delta > MAX_TICK) return MAX_TICK;
        if (delta < -MAX_TICK) return -MAX_TICK;
        return delta;
    }

    /* One axis (x or y) of smooth scrolling for an element or the window. */
    function Axis(scroller) {
        this.scroller = scroller;      // { get(), set(v), max() }
        this.target = null;            // px target while animating
        this.raf = 0;
        this.lastTs = 0;
        this.velocity = 0;             // px/s, used by drag fling
        this.flinging = false;
        this.lastPosition = null;
        this.stalledFrames = 0;
        var self = this;
        this._step = function (ts) { self._frame(ts); };
    }

    Axis.prototype.current = function () {
        return this.scroller.get();
    };

    Axis.prototype.maxScroll = function () {
        return this.scroller.max();
    };

    /* Add a wheel delta to the target and start the loop if idle. */
    Axis.prototype.push = function (delta) {
        var base = this.target !== null ? this.target : this.current();
        var max = this.maxScroll();
        var next = base + delta;
        if (next < 0) next = 0;
        if (next > max) next = max;
        if (next === base && (base === 0 || base === max)) {
            return false; // already pinned at the edge — let the event bubble
        }
        this.target = next;
        this.flinging = false;
        this.velocity = 0;
        this._start();
        return true;
    };

    /* Kick off a fling with an initial velocity (px/s) after pointer release. */
    Axis.prototype.fling = function (velocity) {
        if (Math.abs(velocity) < 30) {
            return; // too slow to be worth animating
        }
        this.velocity = velocity;
        this.flinging = true;
        this.target = null;
        this._start();
    };

    /* Stop any in-flight animation (user grabbed the scrollbar, keyboard nav, etc.). */
    Axis.prototype.halt = function () {
        this.target = null;
        this.velocity = 0;
        this.flinging = false;
        this.lastPosition = null;
        this.stalledFrames = 0;
        if (this.raf) {
            cancelAnimationFrame(this.raf);
            this.raf = 0;
        }
    };

    Axis.prototype._start = function () {
        if (!this.raf) {
            this.lastTs = 0;
            this.raf = requestAnimationFrame(this._step);
        }
    };

    Axis.prototype._frame = function (ts) {
        this.raf = 0;
        var dt = this.lastTs ? Math.min((ts - this.lastTs) / 1000, 0.05) : 1 / 60;
        this.lastTs = ts;

        if (this.flinging) {
            // Exponential velocity decay — frame-rate independent momentum.
            this.velocity *= Math.exp(-LAMBDA * dt);
            var cur = this.current();
            var nextPos = cur + this.velocity * dt;
            var max = this.maxScroll();
            if (nextPos <= 0 || nextPos >= max) {
                nextPos = nextPos <= 0 ? 0 : max;
                this.scroller.set(nextPos);
                this.halt();
                return;
            }
            this.scroller.set(nextPos);
            if (Math.abs(this.velocity) < 25) {
                this.halt();
                return;
            }
            this.raf = requestAnimationFrame(this._step);
            return;
        }

        if (this.target === null) {
            return;
        }
        var pos = this.current();
        var maxTarget = this.maxScroll();
        if (this.target > maxTarget) this.target = maxTarget;
        if (this.target < 0) this.target = 0;
        var remaining = this.target - pos;
        if (Math.abs(remaining) <= EPS) {
            this.scroller.set(this.target);
            this.target = null;
            this.lastPosition = null;
            this.stalledFrames = 0;
            return;
        }
        var k = 1 - Math.exp(-dt / TAU);
        var step = remaining * k;
        // Never terminate solely from a sub-pixel frame step: at high refresh
        // rates that can leave a visibly large remaining distance. The bounded
        // no-progress guard below handles rounded scroll positions safely.
        if (this.lastPosition !== null && Math.abs(pos - this.lastPosition) < 0.01) {
            this.stalledFrames += 1;
        } else {
            this.stalledFrames = 0;
        }
        if (this.stalledFrames >= 2) {
            this.scroller.set(this.target);
            this.target = null;
            this.lastPosition = null;
            this.stalledFrames = 0;
            return;
        }
        this.lastPosition = pos;
        this.scroller.set(pos + step);
        this.raf = requestAnimationFrame(this._step);
    };

    /* ------------------------------------------------------------------ */

    function isEditable(node) {
        if (!node) return false;

        var current = node.nodeType === 1 ? node : node.parentElement;
        if (!current) return false;

        if (current.closest && current.closest(
            'input, textarea, select, [contenteditable="true"], [contenteditable=""]'
        )) {
            return true;
        }

        var active = document.activeElement;
        if (active && active !== document.body && active !== document.documentElement) {
            if (active === current || (active.contains && active.contains(current))) {
                return true;
            }

            if (active.closest && active.closest(
                'input, textarea, select, [contenteditable="true"], [contenteditable=""]'
            )) {
                return true;
            }
        }

        return false;
    }

    function editableHasOverflow(node) {
        var current = node && node.nodeType === 1 ? node : node && node.parentElement;
        while (current && current !== document.body && current !== document.documentElement) {
            if (current.matches && current.matches('textarea, [contenteditable="true"], [contenteditable=""]')) {
                return current.scrollHeight > current.clientHeight + 1 || current.scrollWidth > current.clientWidth + 1;
            }
            current = current.parentElement;
        }
        return false;
    }

    function inExcludedSubtree(node) {
        return !!(node && node.closest && node.closest(
            '[data-no-smooth], .modal.show, .modal-dialog, .select2-container--open, .select2-dropdown, .dropdown-menu.show'
        ));
    }

    /* Find the nearest ancestor (or self) that could natively scroll on the
       given axis; used so nested scroll regions keep native behaviour. */
    function nativeScrollerAt(node, axis, delta) {
        var el = node && node.nodeType === 1 ? node : node && node.parentElement;
        while (el && el !== document.body && el !== document.documentElement) {
            var style = window.getComputedStyle(el);
            var overflow = axis === 'x' ? style.overflowX : style.overflowY;
            if (/(auto|scroll)/.test(overflow)) {
                if (axis === 'y') {
                    var canY = el.scrollHeight > el.clientHeight + 1;
                    if (canY) {
                        var up = delta < 0 && el.scrollTop > 0;
                        var down = delta > 0 && el.scrollTop + el.clientHeight < el.scrollHeight - 1;
                        if (up || down) return el;
                    }
                } else {
                    var canX = el.scrollWidth > el.clientWidth + 1;
                    if (canX) {
                        var left = delta < 0 && el.scrollLeft > 0;
                        var right = delta > 0 && el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
                        if (left || right) return el;
                    }
                }
            }
            el = el.parentElement;
        }
        return null;
    }

    /* Per-target cache for nativeScrollerAt(). Wheel events fire in bursts
       aimed at the same element; the ancestor walk calls getComputedStyle()
       per ancestor, forcing a style recalc on every tick. Only the resolved
       scroller element is cached — the can-scroll direction checks stay live
       so edge behaviour (scroller pinned at top/bottom) is unchanged. */
    var scrollerCache = typeof WeakMap === 'function' ? new WeakMap() : null;

    function cachedNativeScrollerAt(node, axis, delta) {
        var el = node && node.nodeType === 1 ? node : node && node.parentElement;
        if (!el || !scrollerCache) return nativeScrollerAt(el, axis, delta);

        var entry = scrollerCache.get(el);
        if (entry === undefined) {
            var found = nativeScrollerAt(el, axis, 1);
            entry = { scroller: found || null };
            scrollerCache.set(el, entry);
        }
        var scroller = entry.scroller;
        if (!scroller) return null;
        if (axis === 'y') {
            var up = delta < 0 && scroller.scrollTop > 0;
            var down = delta > 0 && scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1;
            return (up || down) ? scroller : null;
        }
        var left = delta < 0 && scroller.scrollLeft > 0;
        var right = delta > 0 && scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1;
        return (left || right) ? scroller : null;
    }

    /* ---- Page (window) vertical wheel smoothing ---- */

    function attachPageScroll() {
        var axis = new Axis({
            get: function () {
                return window.pageYOffset || document.documentElement.scrollTop || 0;
            },
            set: function (v) {
                // behavior:'instant' is required — the root scroller computes to
                // scroll-behavior:smooth in some environments, which would
                // double-smooth every rAF frame and stall the animation loop.
                window.scrollTo({ left: window.pageXOffset || 0, top: v, behavior: 'instant' });
            },
            max: function () {
                var doc = document.documentElement;
                return Math.max(0, doc.scrollHeight - window.innerHeight);
            }
        });

        window.addEventListener('wheel', function (event) {
            if (event.ctrlKey) return;               // pinch-zoom gesture
            if (Math.abs(event.deltaY) === 0) return;
            if (event.deltaMode === 2) return;       // page-mode: leave native
            if (isEditable(event.target) && editableHasOverflow(event.target)) return;
            if (inExcludedSubtree(event.target)) return;

            var delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
            var inner = cachedNativeScrollerAt(event.target, 'y', delta);
            if (inner && inner.hasAttribute('data-smooth-x')) return; // handled by tracker wiring
            if (inner) return;                       // nested scroller consumes it natively

            if (axis.push(clampTick(delta))) {
                event.preventDefault();
            }
        }, { passive: false, capture: false });

        // Any direct position change (keyboard, anchor, scrollbar drag, polling)
        // cancels the animation so we never fight the user or app code.
        window.addEventListener('scroll', function () {
            if (axis.target !== null &&
                Math.abs(axis.current() - axis.target) > 120 &&
                !axis.raf) {
                axis.halt();
            }
        }, { passive: true });
    }

    /* ---- Wide table native scrolling + direct mouse drag-to-pan ---- */

    function attachHorizontalScroll(wrap) {
        if (wrap.hasAttribute('data-drag-scroll-attached')) return;
        wrap.setAttribute('data-drag-scroll-attached', '');
        wrap.setAttribute('data-smooth-x', '');
        var dragEnabled = wrap.matches('.sqr-tracker-wrap, .dashboard-table-wrapper');

        var geometry = { valid: false, max: 0 };
        var scrollIdleTimer = 0;
        var motionActive = false;
        var internalScrollWrite = false;

        function measureGeometry() {
            geometry.max = Math.max(0, wrap.scrollWidth - wrap.clientWidth);
            geometry.valid = true;
        }

        function invalidateGeometry() {
            geometry.valid = false;
            requestAnimationFrame(function () {
                if (!geometry.valid && wrap.isConnected) measureGeometry();
            });
        }

        function ensureGeometry() {
            if (!geometry.valid) measureGeometry();
            return geometry;
        }

        function beginMotion() {
            if (!motionActive) {
                motionActive = true;
                wrap.classList.add('is-scrolling-x');
                document.body.classList.add('table-scroll-active');
                wrap.dispatchEvent(new CustomEvent('requesthub:table-scroll-start', { bubbles: true }));
            }
            window.clearTimeout(scrollIdleTimer);
            scrollIdleTimer = window.setTimeout(function () {
                if (!dragState.active && !axis.raf) {
                    motionActive = false;
                    wrap.classList.remove('is-scrolling-x');
                    if (!document.querySelector('.is-scrolling-x')) {
                        document.body.classList.remove('table-scroll-active');
                    }
                    wrap.dispatchEvent(new CustomEvent('requesthub:table-scroll-end', { bubbles: true }));
                }
            }, 150);
        }

        wrap._requestHubInvalidateScrollGeometry = invalidateGeometry;

        var axis = new Axis({
            get: function () { return wrap.scrollLeft; },
            set: function (v) {
                // instant: same double-smoothing guard as the page scroller.
                internalScrollWrite = true;
                if (wrap.scrollTo) {
                    wrap.scrollTo({ left: v, behavior: 'instant' });
                } else {
                    wrap.scrollLeft = v;
                }
                requestAnimationFrame(function () { internalScrollWrite = false; });
            },
            max: function () { return ensureGeometry().max; }
        });

        if (typeof ResizeObserver === 'function') {
            new ResizeObserver(invalidateGeometry).observe(wrap);
        }

        /* Pointer drag-to-scroll. Mouse only — touch keeps native momentum. */
        var dragState = {
            active: false,
            pending: false,
            startX: 0,
            startY: 0,
            lastX: 0,
            lastTs: 0,
            velocity: 0,
            pointerId: null,
            dxAcc: 0,      // horizontal deltas accumulated between frames
            flushRaf: 0    // pending rAF that flushes dxAcc to scrollLeft
        };

        function interactiveTarget(node) {
            return !!(node && node.closest && node.closest(
                'a, button, input, textarea, select, label, [contenteditable], ' +
                '.resize-handle, .col-resize-handle, .column-resize-handle, ' +
                'th .th-resize, [draggable="true"], .sqr-inline-input'
            ));
        }

        wrap.addEventListener('pointerdown', function (event) {
            if (!dragEnabled) return;
            if (event.pointerType !== 'mouse' || event.button !== 0) return;
            if (interactiveTarget(event.target)) return;
            dragState.pending = true;
            dragState.active = false;
            dragState.startX = event.clientX;
            dragState.startY = event.clientY;
            dragState.lastX = event.clientX;
            dragState.lastTs = event.timeStamp;
            dragState.velocity = 0;
            dragState.pointerId = event.pointerId;
            axis.halt();
            // Do not capture on pointerdown. Immediate capture retargets the
            // following click onto the wrapper, so SQR inline cells never edit.
        });

        wrap.addEventListener('pointermove', function (event) {
            if (!dragState.pending && !dragState.active) return;
            if (event.pointerId !== dragState.pointerId) return;

            if (!dragState.active) {
                var movedX = Math.abs(event.clientX - dragState.startX);
                var movedY = Math.abs(event.clientY - dragState.startY);
                if (movedX < DRAG_THRESHOLD || movedX < movedY) {
                    if (movedY > DRAG_THRESHOLD * 2) {
                        dragState.pending = false; // vertical intent — not ours
                    }
                    return;
                }
                dragState.active = true;
                wrap.classList.add('is-dragging');
                beginMotion();
                wrap.dispatchEvent(new CustomEvent('requesthub:table-drag-start', { bubbles: true }));
                try { wrap.setPointerCapture(event.pointerId); } catch (e) { /* noop */ }
            }

            event.preventDefault();
            var now = event.timeStamp;
            var dx = event.clientX - dragState.lastX;
            // Accumulate and flush at most once per frame — high-rate mice fire
            // pointermove several times per frame, and each scrollLeft write is
            // a layout read+write on the main thread.
            dragState.dxAcc += dx;
            if (!dragState.flushRaf) {
                dragState.flushRaf = requestAnimationFrame(function () {
                    dragState.flushRaf = 0;
                    if (dragState.dxAcc) {
                        wrap.scrollLeft -= dragState.dxAcc;
                        dragState.dxAcc = 0;
                        beginMotion();
                    }
                });
            }

            var dt = Math.max(now - dragState.lastTs, 1);
            // Track velocity as px/s, smoothed to avoid one-frame spikes.
            var instant = (-dx / dt) * 1000;
            dragState.velocity = dragState.velocity * 0.65 + instant * 0.35;
            dragState.lastX = event.clientX;
            dragState.lastTs = now;
        });

        function endDrag(event, cancelled) {
            if (event && event.pointerId !== dragState.pointerId) return;
            var pointerId = dragState.pointerId;
            var wasActive = dragState.active;
            if (wasActive) {
                // Flush deltas still waiting for their frame, then stop. Direct
                // manipulation must not continue moving after pointer release.
                if (dragState.flushRaf) {
                    cancelAnimationFrame(dragState.flushRaf);
                    dragState.flushRaf = 0;
                    if (dragState.dxAcc) {
                        wrap.scrollLeft -= dragState.dxAcc;
                        dragState.dxAcc = 0;
                    }
                }
                wrap.classList.remove('is-dragging');
                axis.halt();
                beginMotion();
                // Suppress the click that follows a real drag so cells/links don't fire.
                dragState.justDragged = true;
                window.setTimeout(function () { dragState.justDragged = false; }, 0);
            }
            if (dragState.flushRaf) {
                cancelAnimationFrame(dragState.flushRaf);
                dragState.flushRaf = 0;
            }
            dragState.dxAcc = 0;
            dragState.pending = false;
            dragState.active = false;
            dragState.pointerId = null;
            if (pointerId !== null && wrap.hasPointerCapture && wrap.hasPointerCapture(pointerId)) {
                try { wrap.releasePointerCapture(pointerId); } catch (e) { /* noop */ }
            }
            if (cancelled) {
                axis.halt();
                window.clearTimeout(scrollIdleTimer);
                motionActive = false;
                wrap.classList.remove('is-dragging', 'is-scrolling-x');
                if (!document.querySelector('.is-scrolling-x')) {
                    document.body.classList.remove('table-scroll-active');
                }
                // A scroll event queued by the final frame can run after this
                // handler. Clear its transient motion state once more afterward.
                window.setTimeout(function () {
                    window.clearTimeout(scrollIdleTimer);
                    motionActive = false;
                    wrap.classList.remove('is-dragging', 'is-scrolling-x');
                    if (!document.querySelector('.is-scrolling-x')) {
                        document.body.classList.remove('table-scroll-active');
                    }
                }, 0);
            }
        }

        wrap.addEventListener('pointerup', function (event) { endDrag(event, false); });
        wrap.addEventListener('pointercancel', function (event) { endDrag(event, true); });
        wrap.addEventListener('lostpointercapture', function (event) { endDrag(event, true); });
        window.addEventListener('blur', function () { endDrag(null, true); });
        window.addEventListener('pagehide', function () { endDrag(null, true); });

        wrap.addEventListener('scroll', function () {
            if (!internalScrollWrite && axis.raf) axis.halt();
            beginMotion();
        }, { passive: true });

        window.addEventListener('resize', invalidateGeometry, { passive: true });

        wrap.addEventListener('click', function (event) {
            if (dragState.justDragged) {
                event.preventDefault();
                event.stopPropagation();
                dragState.justDragged = false;
            }
        }, true);
    }

    var pageScrollAttached = false;

    function init(root) {
        if (!pageScrollAttached) {
            // Dense table pages are paint-heavy. Keep their vertical wheel
            // scrolling native so sticky cells can use compositor scrolling.
            if (!document.body.matches('.sqr-page, .reports-page, .admin-dashboard, .requestor-dashboard, .engineer-dashboard')) {
                attachPageScroll();
            }
            pageScrollAttached = true;
        }

        var scope = root && root.querySelectorAll ? root : document;
        var wraps = scope.querySelectorAll(
            '.sqr-tracker-wrap, .dashboard-table-wrapper, [data-table-scroll-track]'
        );
        for (var i = 0; i < wraps.length; i += 1) {
            attachHorizontalScroll(wraps[i]);
        }

        if (scope.matches && scope.matches('.sqr-tracker-wrap, .dashboard-table-wrapper, [data-table-scroll-track]')) {
            attachHorizontalScroll(scope);
        }
    }

    window.initRequestHubSmoothScroll = init;
    window.invalidateRequestHubScrollGeometry = function (root) {
        var scope = root && root.querySelectorAll ? root : document;
        var wraps = scope.querySelectorAll('.sqr-tracker-wrap, .dashboard-table-wrapper, [data-table-scroll-track]');
        for (var i = 0; i < wraps.length; i += 1) {
            if (wraps[i]._requestHubInvalidateScrollGeometry) {
                wraps[i]._requestHubInvalidateScrollGeometry();
            }
        }
        if (scope.matches && scope.matches('.sqr-tracker-wrap, .dashboard-table-wrapper, [data-table-scroll-track]') &&
            scope._requestHubInvalidateScrollGeometry) {
            scope._requestHubInvalidateScrollGeometry();
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
