(function () {
    const overlay = document.getElementById('aloOverlay');
    if (!overlay) return;
    const countEl = document.getElementById('aloCountdown');
    const stayBtn = document.getElementById('aloStayBtn');
    const config = overlay.dataset;
    const warningMs = 60000;
    const activeMs = 360000;
    const storageKey = `reqHubIdleTimeout:${config.user}`;
    let enabled = config.enabled === 'true';
    let idleTimer;
    let countTimer;
    let deadline;
    let nextActivity = 0;
    let nextAudit = 0;
    let preferenceVersion = 0;
    let lastAppliedRequest = 0;
    let requestId = 0;

    function updateWarning(remaining) {
        clearTimeout(idleTimer);
        clearInterval(countTimer);
        overlay.classList.remove('is-open');
        countEl.classList.remove('alo-countdown--warn');
        if (!enabled) return;
        deadline = Date.now() + remaining;
        idleTimer = setTimeout(showWarning, Math.max(0, remaining - warningMs));
    }

    function showWarning() {
        if (!enabled) return;
        overlay.classList.add('is-open');
        countTimer = setInterval(updateCountdown, 1000);
        updateCountdown();
    }

    function updateCountdown() {
        const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
        countEl.textContent = remaining;
        if (remaining <= 20) countEl.classList.add('alo-countdown--warn');
        if (!remaining) {
            clearInterval(countTimer);
            contactServer('deadline');
        }
    }

    function contactServer(kind) {
        const started = Date.now();
        const id = ++requestId;
        const preference = preferenceVersion;
        const checking = kind === 'deadline';
        const headers = {'X-CSRFToken': config.csrfToken, 'Accept': 'application/json'};
        if (kind === 'activity') headers['X-Request-Hub-Activity'] = 'interactive';
        const options = {method: 'POST', credentials: 'same-origin', headers, keepalive: true, redirect: 'error'};
        if (checking) {
            options.body = 'reason=idle';
            headers['Content-Type'] = 'application/x-www-form-urlencoded';
        }

        function retry() {
            if (preference !== preferenceVersion || id < lastAppliedRequest) return;
            if (kind === 'activity') nextActivity = 0;
            if (checking && enabled) idleTimer = setTimeout(() => contactServer('deadline'), 5000);
        }

        fetch(checking ? config.logoutUrl : config.heartbeatUrl, options).then(response => {
            if (response.status === 401) {
                window.location.assign(config.loginUrl);
                return;
            }
            if (response.status !== 204) {
                retry();
                return;
            }
            if (preference !== preferenceVersion || id < lastAppliedRequest) return;
            const remaining = response.headers.get('X-Request-Hub-Idle-Remaining');
            if (remaining === null) {
                if (checking) {
                    lastAppliedRequest = id;
                    applyPreference(false);
                }
            } else if (enabled && Number(remaining) > 0) {
                lastAppliedRequest = id;
                const adjusted = Number(remaining) - (Date.now() - started);
                updateWarning(Math.max(checking ? 1000 : 0, adjusted));
            }
        }).catch(retry);
    }

    function sendHeartbeat(interactive, force = false) {
        const now = Date.now();
        if (interactive ? !force && now < nextActivity : now < nextAudit) return;
        nextAudit = now + warningMs;
        if (interactive) nextActivity = now + 10000;
        contactServer(interactive ? 'activity' : 'audit');
    }

    function recordActivity(event) {
        if (!event.isTrusted) return;
        if (overlay.classList.contains('is-open')) {
            if (event.key === 'Escape') sendHeartbeat(true, true);
            return;
        }
        sendHeartbeat(enabled);
    }

    function applyPreference(value) {
        ++preferenceVersion;
        enabled = value;
        nextActivity = 0;
        updateWarning(activeMs);
    }

    ['mousedown', 'keydown', 'input', 'touchstart', 'click', 'mousemove', 'wheel'].forEach(event => {
        document.addEventListener(event, recordActivity, {passive: true, capture: true});
    });
    stayBtn.addEventListener('click', event => {
        if (event.isTrusted && enabled) sendHeartbeat(true, true);
    });
    document.addEventListener('requesthub:idle-timeout-changed', event => {
        if (typeof event.detail.enabled !== 'boolean') return;
        applyPreference(event.detail.enabled);
        try {
            localStorage.setItem(storageKey, String(enabled));
        } catch (error) {
            console.warn('Could not sync idle timeout across tabs; the server will enforce the saved preference.', error);
        }
    });
    window.addEventListener('storage', event => {
        if (event.key === storageKey && (event.newValue === 'true' || event.newValue === 'false')) {
            applyPreference(event.newValue === 'true');
        }
    });

    updateWarning(Number(config.remaining || activeMs));
    sendHeartbeat(false);
})();
