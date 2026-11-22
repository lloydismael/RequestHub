/* Liquid Glass interactions — light parallax, staggered entrance, and submit state. */
(function () {
    'use strict';

    var motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
    var reduceAppMotion = document.body && (document.body.classList.contains('pref-reduce-motion') ||
        document.body.classList.contains('pref-reduce-effects'));
    var coarsePointer = window.matchMedia('(pointer: coarse)').matches;
    var phoneViewport = window.matchMedia('(max-width: 767.98px)');

    /** @returns {boolean} Whether the OS or app has disabled optional motion. */
    function motionIsReduced() {
        return motionPreference.matches || Boolean(reduceAppMotion);
    }

    /* ---- Staggered entrance (CSS-driven, no dependency) ---- */
    function normalizeDelaySeconds(raw, index) {
        var delay = parseFloat(raw);
        if (!Number.isFinite(delay)) {
            return Math.min(index * 0.01, 0.02);
        }
        // Login uses fractional seconds (0.12). Management/Reports use ms (40, 80…).
        if (delay > 5) {
            delay = delay / 1000;
        }
        return Math.min(0.02, Math.max(0, delay));
    }

    /** Mark page entrances ready, without staggering for reduced motion. */
    function primeEntrance() {
        if (motionIsReduced()) {
            document.body.classList.add('lg-enter-ready');
            return;
        }
        var items = document.querySelectorAll('.lg-enter');
        for (var i = 0; i < items.length; i += 1) {
            var delay = normalizeDelaySeconds(items[i].getAttribute('data-lg-delay'), i);
            items[i].style.setProperty('--lg-delay', delay + 's');
        }
        requestAnimationFrame(function () {
            document.body.classList.add('lg-enter-ready');
        });
    }

    /** Move only the card optics, and skip the effect on phones or with reduced app effects.
     * @param {HTMLElement} card
     */
    function bindCardMotion(card) {
        if (reduceAppMotion || phoneViewport.matches) {
            return;
        }

        var frame = null;
        var pendingPointer = null;
        var lightLayers = card.querySelectorAll('.login-card__lens, .login-card__sheen');
        var currentX = 0.5;
        var currentY = 0.18;
        var targetX = currentX;
        var targetY = currentY;
        var lastOrientationSample = 0;

        /** Update the optical layers without restyling the festive SVG descendants. */
        function paintLight() {
            for (var i = 0; i < lightLayers.length; i += 1) {
                lightLayers[i].style.setProperty('--login-light-x', (currentX * 100).toFixed(1) + '%');
                lightLayers[i].style.setProperty('--login-light-y', (currentY * 100).toFixed(1) + '%');
            }
        }

        /** Consume the latest pointer input and ease toward its light vector. */
        function render() {
            frame = null;
            if (motionIsReduced()) {
                return;
            }
            if (pendingPointer) {
                var rect = card.getBoundingClientRect();
                targetX = Math.min(1, Math.max(0, (pendingPointer.x - rect.left) / rect.width));
                targetY = Math.min(1, Math.max(0, (pendingPointer.y - rect.top) / rect.height));
                pendingPointer = null;
            }
            currentX += (targetX - currentX) * 0.16;
            currentY += (targetY - currentY) * 0.16;
            paintLight();

            if (Math.abs(targetX - currentX) > 0.002 || Math.abs(targetY - currentY) > 0.002) {
                requestRender();
            }
        }

        /** Schedule at most one frame, and only while motion is allowed. */
        function requestRender() {
            if (frame === null && !document.hidden && !motionIsReduced()) {
                frame = requestAnimationFrame(render);
            }
        }

        /** Park a queued frame without dropping its input. */
        function cancelRender() {
            if (frame !== null) {
                cancelAnimationFrame(frame);
                frame = null;
            }
        }

        /** Reset the optics immediately so no stale pointer input is replayed. */
        function syncMotionPreference() {
            if (!motionIsReduced()) {
                return;
            }
            cancelRender();
            pendingPointer = null;
            currentX = targetX = 0.5;
            currentY = targetY = 0.18;
            lastOrientationSample = 0;
            paintLight();
            card.classList.remove('is-pressed');
        }

        if (motionPreference.addEventListener) {
            motionPreference.addEventListener('change', syncMotionPreference);
        } else if (motionPreference.addListener) {
            motionPreference.addListener(syncMotionPreference);
        }

        /** @param {PointerEvent} event Latest pointer sample, consumed on the next frame. */
        function setPointerTarget(event) {
            if (!motionIsReduced()) {
                pendingPointer = { x: event.clientX, y: event.clientY };
                requestRender();
            }
        }

        if (!coarsePointer) {
            card.addEventListener('pointermove', setPointerTarget, { passive: true });

            card.addEventListener('pointerleave', function () {
                pendingPointer = null;
                targetX = 0.5;
                targetY = 0.18;
                requestRender();
            });
        }

        card.addEventListener('pointerdown', function () {
            if (!motionIsReduced()) {
                card.classList.add('is-pressed');
            }
        });

        ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (eventName) {
            card.addEventListener(eventName, function () {
                card.classList.remove('is-pressed');
            });
        });

        /** @param {DeviceOrientationEvent} event Tilt sample from a supported device. */
        function handleOrientation(event) {
            if (motionIsReduced() || phoneViewport.matches || !coarsePointer || event.gamma === null || event.beta === null) {
                return;
            }
            // Tilt is a slow input; sampling it every frame keeps the filtered
            // glass layers repainting continuously. Coalesce to ~4 Hz instead.
            var now = Date.now();
            if (now - lastOrientationSample < 250) {
                return;
            }
            lastOrientationSample = now;
            targetX = Math.min(0.82, Math.max(0.18, 0.5 + event.gamma / 90));
            targetY = Math.min(0.72, Math.max(0.12, 0.35 + event.beta / 180));
            requestRender();
        }

        /* Never request permission unexpectedly; listen only where access is already granted. */
        if (coarsePointer && 'DeviceOrientationEvent' in window &&
                typeof window.DeviceOrientationEvent.requestPermission !== 'function') {
            window.addEventListener('deviceorientation', handleOrientation, { passive: true });
        }

        document.addEventListener('visibilitychange', function () {
            if (document.hidden) {
                cancelRender();
            } else {
                requestRender();
            }
        });
    }

    /** @param {HTMLElement} card Login card whose errors may trigger a short shake. */
    function bindErrorShake(card) {
        if (motionIsReduced() || !card.querySelector('.alert, .invalid-feedback')) {
            return;
        }
        requestAnimationFrame(function () {
            if (motionIsReduced()) {
                return;
            }
            card.classList.add('login-card--shake');
            card.addEventListener('animationend', function handler(event) {
                if (event.animationName === 'lg-shake') {
                    card.classList.remove('login-card--shake');
                    card.removeEventListener('animationend', handler);
                }
            });
        });
    }

    /* ---- Submit spinner ---- */
    function bindSubmitState(form) {
        var button = form.querySelector('.login-card__submit');
        if (!button) {
            return;
        }
        form.addEventListener('submit', function () {
            if (form.checkValidity && !form.checkValidity()) {
                return;
            }
            button.setAttribute('data-loading', 'true');
            button.setAttribute('aria-busy', 'true');
        });
    }

    /* ---- Success overlay progress bar synced to the redirect delay ---- */
    function syncSuccessProgress() {
        var overlay = document.querySelector('[data-login-success-overlay]');
        if (!overlay) {
            return;
        }
        var delay = parseInt(overlay.getAttribute('data-redirect-delay'), 10);
        if (Number.isFinite(delay) && delay > 0) {
            overlay.style.setProperty('--lg-progress-duration', delay + 'ms');
        }
    }

    function init() {
        primeEntrance();
        syncSuccessProgress();

        var card = document.querySelector('.page-login .login-card');
        if (card) {
            bindCardMotion(card);
            bindErrorShake(card);
        }

        var form = document.querySelector('.login-form');
        if (form) {
            bindSubmitState(form);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}());
