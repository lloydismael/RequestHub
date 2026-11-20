(() => {
    'use strict';

    function createPoller({sources, isVisible, schedule, cancel, load, update}) {
        const states = Object.entries(sources).map(([name, interval]) => ({name, interval, failures: 0}));
        let paused = false;
        let denied = false;

        function clear(state) {
            if (state.timer !== undefined) cancel(state.timer);
            state.timer = undefined;
            state.controller?.abort();
            state.controller = undefined;
        }

        async function run(state) {
            if (denied || !isVisible() || state.controller) return;
            clear(state);
            const controller = new AbortController();
            state.controller = controller;
            let result;
            try {
                result = await load(state.name, controller.signal);
            } catch {
                result = {status: 'unavailable'};
            }
            if (state.controller !== controller) return;
            state.controller = undefined;
            if (result.status === 'denied') {
                denied = true;
                states.forEach(clear);
            }
            update(state.name, result);
            if (!denied && !paused && isVisible()) {
                state.failures = result.status === 'ok' ? 0 : Math.min(state.failures + 1, 5);
                state.timer = schedule(() => run(state), Math.min(state.interval * 2 ** state.failures, 120000));
            }
        }

        function refresh() { states.forEach(run); }
        return {
            refresh,
            setPaused(value) {
                paused = value;
                if (paused) states.forEach(clear);
                else refresh();
            },
            visibilityChanged() {
                if (!isVisible()) states.forEach(clear);
                else if (!paused) refresh();
            },
        };
    }

    const finite = Number.isFinite;
    function addLatencySample(history, sample) {
        const time = Date.parse(sample.sampled_at);
        if (!finite(time)) return history;
        const sameWorker = history.at(-1)?.worker === sample.worker;
        return [
            ...(sameWorker ? history.filter(point => time - 300000 < point.time && point.time < time) : []),
            {time, worker: sample.worker, a: sample.p95_ms, aIsLowerBound: sample.p95_is_lower_bound === true, b: sample.average_ms},
        ].slice(-61);
    }

    function transactionRate(before, after) {
        if (!before || !finite(before.transactions) || !finite(after.transactions)
            || before.stats_reset_at !== after.stats_reset_at || after.transactions < before.transactions) return null;
        const seconds = (Date.parse(after.sampled_at) - Date.parse(before.sampled_at)) / 1000;
        return seconds > 0 && seconds <= 30 ? (after.transactions - before.transactions) / seconds : null;
    }

    if (typeof module === 'object' && module.exports) {
        module.exports = {createPoller, addLatencySample, transactionRate};
        return;
    }

    const root = document.getElementById('monitoring-pane');
    if (!root) return;
    const fields = Object.fromEntries([...root.querySelectorAll('[data-mon]')].map(node => [node.dataset.mon, node]));
    const pauseButton = root.querySelector('[data-monitor-pause]');
    const refreshButton = root.querySelector('[data-monitor-refresh]');
    const storageMeter = root.querySelector('[data-storage-meter]');
    const empty = '—';
    const timeFormat = new Intl.DateTimeFormat(undefined, {hour: '2-digit', minute: '2-digit', second: '2-digit'});
    const numbers = new Intl.NumberFormat(undefined, {maximumFractionDigits: 2});
    const timeText = time => finite(time) ? timeFormat.format(time) : empty;
    const number = (value, unit = '') => finite(value) ? numbers.format(value) + unit : empty;
    const bytes = value => {
        if (!finite(value)) return empty;
        const units = [' B', ' KiB', ' MiB', ' GiB', ' TiB', ' PiB'];
        let index = 0;
        while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
        return number(value, units[index]);
    };
    const ms = (value, lowerBound = false) => (lowerBound && finite(value) ? '>' : '') + number(value, ' ms');
    const percent = value => number(value, '%');
    let lastDatabase = null;
    let lastAppTime = null;
    let lastDatabaseTime = null;
    let latency = [];
    let paused = false;
    let blocked = false;
    let pageActive = true;
    let liveMode = 'loading';
    let platform = null;

    function set(key, value) {
        if (fields[key] && fields[key].textContent !== value) fields[key].textContent = value;
    }

    function badge(key, label, tone = 'neutral') {
        set(key, label);
        fields[key].dataset.tone = tone;
    }

    function updatePollBadge() {
        if (blocked) badge('poll-status', 'Session ended or access changed', 'warning');
        else if (paused) badge('poll-status', 'Updates paused');
        else if (liveMode === 'unavailable') badge('poll-status', 'Live endpoint unavailable · retrying', 'warning');
        else if (liveMode === 'current') badge('poll-status', 'Live · 5 s', 'good');
        else badge('poll-status', 'Waiting for live data');
    }

    function sampled(sample, kind = 'Sampled') {
        const time = Date.parse(sample);
        return finite(time) ? `${kind} ${timeText(time)}` : 'No sample yet';
    }

    function svgElement(tag, attributes, text) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function chart(name, points, end, windowMs, max, format, gap, noData) {
        const svg = root.querySelector(`[data-monitor-chart="${name}"]`);
        const table = root.querySelector(`[data-monitor-table="${name}"]`);
        const hasData = finite(end) && points.some(point => finite(point.a) || finite(point.b));
        fields[`${name}-chart-empty`].hidden = hasData;
        set(`${name}-chart-empty`, noData);
        set(`${name}-chart-end`, hasData ? `Ends ${timeText(end)}` : empty);
        svg.replaceChildren();
        table.replaceChildren();
        if (!hasData) {
            const cell = table.insertRow().insertCell();
            cell.colSpan = 3;
            cell.textContent = noData;
            return;
        }

        const x = time => 56 + ((time - end + windowMs) / windowMs) * 520;
        const y = value => 154 - Math.min(value, max) / max * 130;
        for (let division = 0; division <= 3; division += 1) {
            const value = max * division / 3;
            svg.append(svgElement('line', {x1: 56, x2: 576, y1: y(value), y2: y(value), class: 'monitor-gridline'}));
            svg.append(svgElement('text', {x: 48, y: y(value) + 4, 'text-anchor': 'end'}, String(Math.round(value))));
        }
        for (const key of ['a', 'b']) {
            let path = '';
            let previous = null;
            let last = null;
            for (const point of points) {
                if (!finite(point[key]) || (key === 'a' && point.aIsLowerBound)) { previous = null; continue; }
                const command = previous !== null && point.time - previous <= gap ? 'L' : 'M';
                path += `${command}${x(point.time).toFixed(2)},${y(point[key]).toFixed(2)} `;
                if (command === 'M') {
                    svg.append(svgElement('circle', {cx: x(point.time), cy: y(point[key]), r: 2, class: `monitor-series-${key}`}));
                }
                previous = point.time;
                last = point;
            }
            if (last) svg.append(svgElement('path', {d: path, class: `monitor-series-${key}`}));
        }
        for (const point of [...points].reverse()) {
            const row = table.insertRow();
            const header = document.createElement('th');
            header.scope = 'row';
            header.textContent = timeText(point.time);
            row.append(header);
            row.insertCell().textContent = format(point.a, point.aIsLowerBound);
            row.insertCell().textContent = format(point.b);
        }
    }

    function renderLive({app, database}, roundTrip) {
        lastAppTime = app.sampled_at;
        lastDatabaseTime = database.sampled_at;
        set('app-p95', ms(app.p95_ms, app.p95_is_lower_bound));
        set('app-sample', sampled(lastAppTime));
        set('app-requests', number(app.requests));
        set('app-average', ms(app.average_ms));
        set('app-errors', number(app.errors));
        set('app-roundtrip', ms(roundTrip));
        badge('app-status', 'Responding', 'good');

        const label = {reachable: 'Reachable', partial: 'Probe only', unreachable: 'Unreachable'}[database.status] || 'Unknown';
        badge('db-status', label, database.status === 'reachable' ? 'good' : 'warning');
        set('db-latency', ms(database.latency_ms));
        set('db-sample', sampled(lastDatabaseTime, `${label} · checked`));
        set('db-size', bytes(database.size_bytes));
        set('db-connections', number(database.connections));
        set('db-transactions', number(transactionRate(lastDatabase, database), ' tx/s'));
        set('db-locks', number(database.lock_waits));
        set('db-cache', percent(database.cache_hit_percent));
        lastDatabase = database;

        latency = addLatencySample(latency, app);
        const max = Math.max(30, Math.ceil(Math.max(0, ...latency.flatMap(point => [point.a, point.b]).filter(finite)) / 30) * 30);
        chart('app', latency, latency.at(-1)?.time, 300000, max, ms, 15000, 'No worker latency readings yet');
    }

    const platformWindowMs = 15 * 60 * 1000;
    const inactivePlatformStatuses = new Set(['unconfigured', 'invalid_configuration', 'access_denied']);
    const azureLabels = {
        unconfigured: 'Not configured', invalid_configuration: 'Check resource ID', loading: 'Fetching samples',
        current: 'Azure Monitor', partial: 'Partial data', delayed: 'Delayed data',
        no_data: 'No samples', unavailable: 'Azure unavailable', access_denied: 'Azure access denied',
    };

    function platformSource(incoming, previous, now) {
        const source = incoming || (inactivePlatformStatuses.has(previous?.status)
            ? previous : {status: 'unavailable', metrics: {}});
        if (inactivePlatformStatuses.has(source.status)) return {status: source.status, metrics: {}};
        const fallback = ['loading', 'unavailable'].includes(source.status);
        const metrics = {...source.metrics};
        let retained = false;
        for (const [name, metric] of Object.entries(previous?.metrics || {})) {
            const next = metrics[name];
            if (next?.status === 'access_denied' || (!fallback && next?.status !== 'unavailable')) continue;
            const time = Date.parse(metric.sampled_at);
            if (!finite(metric.value) || !finite(time) || time < now - platformWindowMs || time > now
                || time <= Date.parse(next?.sampled_at)) continue;
            metrics[name] = {...metric, status: 'stale'};
            retained = true;
        }
        return {status: fallback && retained ? 'delayed' : source.status, metrics};
    }
    const azureFormats = {
        cpu_percent: percent, memory_percent: percent, storage_used: bytes, backup_storage_used: bytes,
        iops: value => number(value, ' IOPS'), active_connections: number, connections_failed: number,
        Requests: number, Http5xx: number, HttpResponseTime: value => ms(finite(value) ? value * 1000 : null),
        AverageMemoryWorkingSet: bytes, CpuTime: value => number(value, ' CPU-s'),
    };

    function metricText(metric, fallback, stale) {
        if (!metric?.sampled_at) return fallback;
        const time = Date.parse(metric.sampled_at);
        const age = finite(time) ? Math.max(0, Math.floor((Date.now() - time) / 60000)) : null;
        const prefix = stale || metric.status === 'stale' || age >= 5 ? 'Delayed' : 'Sampled';
        return `${sampled(metric.sampled_at, prefix)}${age !== null ? ` · ${age < 1 ? '<1 min' : `${age} min`} old` : ''}`;
    }

    function renderPlatform(data, stale = false) {
        const postgres = data?.postgres;
        const site = data?.web_app;
        for (const [key, source] of [['server-status', postgres], ['site-status', site]]) {
            const status = source?.status || 'unavailable';
            const label = stale && !inactivePlatformStatuses.has(status) ? 'Refresh failed' : azureLabels[status];
            badge(key, label || 'Unknown', status === 'current' && !stale ? 'good' : 'warning');
        }
        const metrics = {...postgres?.metrics, ...site?.metrics};
        for (const [name, format] of Object.entries(azureFormats)) {
            const metric = metrics[name];
            const source = Object.hasOwn(site?.metrics || {}, name) || /^[A-Z]/.test(name) ? site : postgres;
            set(name, format(metric?.value));
            const fallback = azureLabels[metric?.status] || azureLabels[source?.status] || 'No sample';
            set(`${name}-sample`, metricText(metric, fallback, stale));
        }

        const used = metrics.storage_used;
        const free = metrics.storage_free;
        const usage = metrics.storage_percent;
        const details = [];
        if (finite(free?.value)) details.push(`${bytes(free.value)} free (${metricText(free, 'No sample', stale)})`);
        if (finite(usage?.value)) details.push(`${percent(usage.value)} used (${metricText(usage, 'No sample', stale)})`);
        if (finite(used?.value) && !finite(free?.value)) details.push('Free space not reported');
        set('storage-details', details.join(' · ') || 'Used and free server space');
        storageMeter.hidden = !finite(usage?.value);
        if (!storageMeter.hidden) storageMeter.value = usage.value;

        const points = new Map();
        for (const [name, key] of [['cpu_percent', 'a'], ['storage_percent', 'b']]) {
            for (const [timestamp, value] of metrics[name]?.history || []) {
                const time = Date.parse(timestamp);
                if (!finite(time)) continue;
                if (!points.has(time)) points.set(time, {time});
                points.get(time)[key] = value;
            }
        }
        const end = Date.now();
        const history = [...points.values()].filter(point => end - platformWindowMs <= point.time && point.time <= end).sort((a, b) => a.time - b.time);
        chart('platform', history, end, platformWindowMs, 100, percent, 90000, azureLabels[postgres?.status] || 'Azure samples unavailable');

        const notes = [];
        if (stale) notes.push('Azure refresh failed. Any displayed readings are from the last successful response.');
        if ([postgres, site].some(source => source?.status === 'unconfigured')) notes.push('Azure metrics are optional and must be configured on the server.');
        if ([postgres, site].some(source => source?.status === 'invalid_configuration')) notes.push('An Azure resource ID is invalid. Check the App Service settings.');
        if ([postgres, site].some(source => ['unavailable', 'access_denied'].includes(source?.status)
            || Object.values(source?.metrics || {}).some(metric => metric.status === 'access_denied'))) {
            notes.push('Check the app’s managed identity and Monitoring Reader access if this persists.');
        }
        fields['platform-note'].hidden = !notes.length;
        set('platform-note', notes.join(' '));
    }

    function update(name, result) {
        if (result.status === 'denied') {
            blocked = true;
            pauseButton.disabled = refreshButton.disabled = true;
            latency = [];
            lastDatabase = lastAppTime = lastDatabaseTime = platform = null;
            for (const key of Object.keys(fields)) set(key, empty);
            storageMeter.hidden = true;
            chart('app', [], null, 300000, 30, ms, 15000, 'Reopen Monitoring after signing in');
            renderPlatform(null);
            badge('app-status', 'Access unavailable', 'warning');
            badge('db-status', 'Access unavailable', 'warning');
            updatePollBadge();
            return;
        }
        if (name === 'platform') {
            const now = Date.now();
            const incoming = result.status === 'ok' ? result.payload : null;
            platform = Object.fromEntries(['postgres', 'web_app'].map(key => [key, platformSource(
                incoming?.[key], platform?.[key], now,
            )]));
            renderPlatform(platform, result.status !== 'ok');
            return;
        }
        liveMode = result.status === 'ok' ? 'current' : 'unavailable';
        if (liveMode === 'current') {
            renderLive(result.payload, result.roundTrip);
        } else {
            badge('app-status', lastAppTime ? 'Live data stale' : 'No live data', 'warning');
            badge('db-status', lastDatabaseTime ? 'Live data stale' : 'No live data', 'warning');
            set('app-sample', sampled(lastAppTime, 'Last sampled'));
            set('db-sample', sampled(lastDatabaseTime, 'Last checked'));
            lastDatabase = null;
        }
        updatePollBadge();
    }

    async function load(name, signal) {
        const start = performance.now();
        const response = await fetch(root.dataset[`${name}Url`], {
            headers: {Accept: 'application/json'}, cache: 'no-store', credentials: 'same-origin', redirect: 'manual',
            signal: AbortSignal.any([signal, AbortSignal.timeout(name === 'live' ? 8000 : 27000)]),
        });
        if ([401, 403].includes(response.status) || response.type === 'opaqueredirect') return {status: 'denied'};
        if (!response.ok) return {status: 'unavailable'};
        return {status: 'ok', payload: await response.json(), roundTrip: performance.now() - start};
    }

    const poller = createPoller({
        sources: {live: 5000, platform: 60000}, isVisible: () => pageActive && !document.hidden,
        schedule: (fn, delay) => setTimeout(fn, delay), cancel: id => clearTimeout(id), load, update,
    });
    pauseButton.disabled = refreshButton.disabled = false;
    pauseButton.addEventListener('click', () => {
        if (blocked) return;
        paused = !paused;
        pauseButton.textContent = paused ? 'Resume updates' : 'Pause updates';
        if (paused) lastDatabase = null;
        poller.setPaused(paused);
        updatePollBadge();
    });
    refreshButton.addEventListener('click', poller.refresh);
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) lastDatabase = null;
        poller.visibilityChanged();
    });
    window.addEventListener('pagehide', () => { pageActive = false; poller.visibilityChanged(); });
    window.addEventListener('pageshow', () => { pageActive = true; poller.visibilityChanged(); });
    poller.refresh();
})();
