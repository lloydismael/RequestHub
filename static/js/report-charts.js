/* The thumbnails and expanded Reports use the same renderer and authorized JSON payloads. */
(function () {
    'use strict';

    const charts = window.rhCharts;
    const { number } = window.rhChartValues;
    const requests = [['completed', 'Completed', 'positive'], ['ongoing', 'Ongoing', 'ongoing']];
    const hours = [['billable', 'Billable', 'ongoing'], ['non_billable', 'Not Billable', 'unassessed']];
    const totals = [['totals', 'Hours']];
    const definitions = {
        requestTrendChart: { type: 'line', fields: [['created', 'Created', 'accent'], ['completed', 'Completed', 'positive']], integer: true },
        engagementChart: { fields: requests, horizontal: true, ranked: true, integer: true },
        productChart: { fields: requests, horizontal: true, ranked: true, integer: true },
        engineerChart: { fields: requests, horizontal: true, integer: true },
        accountManagerChart: { fields: requests, horizontal: true, integer: true },
        activityMonthlyChart: { fields: hours, period: true },
        activityEngineerChart: { fields: hours, horizontal: true },
        activityTypeChart: { fields: hours, horizontal: true, ranked: true },
        activityAccountChart: { fields: hours, horizontal: true },
        activityBillableChart: { type: 'doughnut', fields: totals, colors: ['ongoing', 'unassessed'] },
        activityLocationChart: { fields: totals, horizontal: true, ranked: true }
    };

    function spec(config, payload) {
        const series = config.fields.map(([key, label, color]) => ({ label, color, values: payload[key] || [] }));
        const data = config.ranked ? charts.rank(payload.labels, series) : { labels: payload.labels, series };
        const totals = series.length > 1 && config.type !== 'line' ? data.labels.map((_, i) => {
            if (data.series.some(s => s.values[i] == null)) return null;
            return config.integer ? data.series.reduce((n, s) => n + Number(s.values[i]), 0)
                : data.series.reduce((n, s) => n + Math.round(Number(s.values[i]) * 100), 0) / 100;
        }) : null;
        const extraValues = totals ? [{ label: 'Total', values: totals }] : config.type === 'doughnut'
            ? [{ label: 'Share', values: data.series[0].values.map(v => charts.percent(v, data.series[0].values)), format: String }] : [];
        return { type: 'bar', ...config, ...data, totals, extraValues, format: number,
            unit: config.integer ? 'Requests' : 'Hours' };
    }

    function limitPreview(data, limit) {
        const take = values => values.slice(0, limit);
        const takeSeries = series => series.map(s => ({ ...s, values: take(s.values) }));
        return { ...data, labels: take(data.labels), series: takeSeries(data.series),
            totals: data.totals && take(data.totals), extraValues: takeSeries(data.extraValues) };
    }

    const shell = document.querySelector('[data-report-preview-limit]');
    if (!shell) return;
    const limit = Number(shell.dataset.reportPreviewLimit);
    const entries = new Map();
    shell.querySelectorAll('canvas[data-report-chart]').forEach(canvas => {
        const id = canvas.id;
        const config = definitions[id];
        const data = charts.read(id + '-data');
        if (!config || !data?.labels) return;
        const card = canvas.closest('.rpt-chart-card');
        const full = charts.read(id + '-full-data');
        const title = card.querySelector('.rpt-chart-title').textContent.trim();
        const fullSpec = spec(config, full?.labels?.length ? full : data);
        const capped = config.horizontal && card.closest('.rpt-chart-grid') && fullSpec.labels.length > limit;
        entries.set(id, { title, spec: fullSpec });
        charts.mount(canvas, capped ? limitPreview(fullSpec, limit) : fullSpec);
        if (capped) {
            const footer = card.querySelector('.rpt-card-more');
            footer.querySelector('.rpt-preview-count').textContent = `Showing ${limit} of ${fullSpec.labels.length}`;
            card.querySelector('.rpt-expand-btn').hidden = true;
            footer.hidden = false;
        }
    });

    const modal = document.getElementById('rptChartModal');
    if (!modal) return;
    const title = document.getElementById('rptChartModalTitle');
    const canvas = document.getElementById('rptModalCanvas');
    const closeButton = document.getElementById('rptChartModalClose');
    const download = document.getElementById('rptChartModalPng');
    let active, opener, previousOverflow;

    function open(button) {
        const entry = entries.get(button.dataset.expandChart);
        if (!entry || modal.classList.contains('is-open')) return;
        opener = button;
        previousOverflow = document.body.style.overflow;
        title.textContent = entry.title;
        canvas.setAttribute('aria-label', title.textContent);
        modal.querySelector('details').open = false;
        modal.classList.add('is-open');
        modal.inert = false;
        modal.setAttribute('aria-hidden', 'false');
        modal.querySelector('.rpt-chart-modal__inner').scrollTop = 0;
        document.body.style.overflow = 'hidden';
        active = charts.mount(canvas, entry.spec);
        download.disabled = !active;
        closeButton.focus();
    }
    function close() {
        modal.classList.remove('is-open');
        modal.inert = true;
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = previousOverflow;
        charts.remove(canvas);
        active = null;
        opener?.focus();
        opener = null;
    }
    document.querySelectorAll('[data-expand-chart]').forEach(button => button.addEventListener('click', () => open(button)));
    closeButton.addEventListener('click', close);
    modal.addEventListener('click', event => { if (event.target === modal) close(); });
    download.addEventListener('click', () => {
        if (!active) return;
        const link = document.createElement('a');
        link.download = title.textContent.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.png';
        link.href = charts.png(active);
        link.click();
    });
    document.addEventListener('keydown', event => {
        if (!opener) return;
        if (event.key === 'Escape') { event.preventDefault(); close(); }
        if (event.key === 'Tab') {
            const targets = [...modal.querySelectorAll('button:not([disabled]), summary, [tabindex="0"]')].filter(el => el.offsetParent);
            const first = targets[0], last = targets.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
}());
