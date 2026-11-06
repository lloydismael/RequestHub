/* Liquid Glass interactions — light parallax, staggered entrance, and submit state. */
(function () {
    'use strict';

    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
        (document.body && (document.body.classList.contains('pref-reduce-motion') ||
            document.body.classList.contains('pref-reduce-effects')));
    var coarsePointer = window.matchMedia('(pointer: coarse)').matches;

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

    function primeEntrance() {
        // Reduce Motion (OS setting or the app's pref-reduce-motion class):
        // skip the stagger entirely — elements render visible with no animation.
        if (reduceMotion) {
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

    /* ---- Smoothed light vector; the pane stays stable while its optics move ---- */
    function bindCardMotion(card) {
        if (reduceMotion) {
            return;
        }

        var frame = null;
        var currentX = 0.5;
        var currentY = 0.18;
        var targetX = currentX;
        var targetY = currentY;
        var lastOrientationSample = 0;

        function render() {
            currentX += (targetX - currentX) * 0.16;
            currentY += (targetY - currentY) * 0.16;
            card.style.setProperty('--login-light-x', (currentX * 100).toFixed(1) + '%');
            card.style.setProperty('--login-light-y', (currentY * 100).toFixed(1) + '%');

            if (Math.abs(targetX - currentX) > 0.002 || Math.abs(targetY - currentY) > 0.002) {
                frame = requestAnimationFrame(render);
            } else {
                frame = null;
            }
        }

        function requestRender() {
            if (frame === null && !document.hidden) {
                frame = requestAnimationFrame(render);
            }
        }

        function setPointerTarget(event) {
            var rect = card.getBoundingClientRect();
            targetX = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
            targetY = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
            requestRender();
        }

        if (!coarsePointer) {
            card.addEventListener('pointermove', setPointerTarget, { passive: true });

            card.addEventListener('pointerleave', function () {
                targetX = 0.5;
                targetY = 0.18;
                requestRender();
            });
        }

        card.addEventListener('pointerdown', function () {
            card.classList.add('is-pressed');
        });

        ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (eventName) {
            card.addEventListener(eventName, function () {
                card.classList.remove('is-pressed');
            });
        });

        function handleOrientation(event) {
            if (!coarsePointer || event.gamma === null || event.beta === null) {
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
            if (document.hidden && frame !== null) {
                cancelAnimationFrame(frame);
                frame = null;
            } else if (!document.hidden) {
                requestRender();
            }
        });
    }

    /* ---- Error shake ---- */
    function bindErrorShake(card) {
        if (reduceMotion) {
            return;
        }
        if (!card.querySelector('.alert, .invalid-feedback')) {
            return;
        }
        requestAnimationFrame(function () {
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
