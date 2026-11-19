(function () {
    'use strict';

    const mounted = new Map();
    const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif';

    function token(name, fallback) {
        return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
    }
    const fmt = n => window.rhChartValues.number(n);

    function tokenColor(role) {
        const value = token('--rh-chart-' + role, '');
        if (/^#[\da-f]{3,8}$/i.test(value) || /^rgba?\(/.test(value)) return value;
        return { surface: '#fff', ink: '#15243c', muted: '#50617b', grid: '#d7e2ef', positive: '#167458',
            caution: '#946000', critical: '#a33951', unassessed: '#627087', secondary: '#107a74' }[role] || '#245ac7';
    }

    function read(id) {
        try { return JSON.parse(document.getElementById(id)?.textContent || 'null'); }
        catch (_) { return null; }
    }

    function rank(labels, series) {
        const order = labels.map((_, i) => i).sort((a, b) => {
            const sum = i => series.reduce((n, s) => n + (Number(s.values[i]) || 0), 0);
            return sum(b) - sum(a);
        });
        return { labels: order.map(i => labels[i]), series: series.map(s => ({ ...s, values: order.map(i => s.values[i]) })) };
    }

    const disclosure = canvas => canvas.closest('.rh-chart-card')?.querySelector('.rh-chart-values');
    const canvasTitle = canvas => canvas.getAttribute('aria-label') || 'Chart';
    function values(canvas, spec) {
        const details = disclosure(canvas);
        if (!details) return;
        const data = spec.values || spec;
        details.hidden = !data.labels.length;
        if (!details.hidden) window.rhChartValues.render(details.querySelector('div'), {
            ...data, series: [...data.series, ...(spec.extraValues || [])], format: spec.format,
            keyLabel: spec.type === 'line' || spec.period ? 'Month' : 'Category', title: 'Exact chart values'
        });
    }

    function state(canvas, text) {
        const plot = canvas.parentElement;
        const msg = canvas.closest('.rh-chart-card')?.querySelector('.rh-chart-state');
        plot.hidden = !!text;
        if (msg) { msg.hidden = !text; msg.textContent = text || ''; }
    }

    function colorFor(role, label) {
        return role === 'status' ? (label === 'Completed' ? 'positive' : 'ongoing')
            : role === 'priority' ? (label === 'High' ? 'critical' : 'unassessed') : role;
    }

    function legend(canvas, spec) {
        const el = canvas.closest('.rh-chart-card')?.querySelector('.rh-chart-legend');
        if (!el) return;
        const items = spec.type === 'doughnut' ? spec.labels.map((label, i) => ({
            label: `${label}: ${(spec.format || fmt)(spec.series[0].values[i])} h`, color: spec.colors[i]
        })) : spec.series.length > 1 ? spec.series : [];
        el.replaceChildren(...items.map((s, i) => {
            const li = document.createElement('li');
            li.textContent = s.label;
            li.style.setProperty('--rh-legend-key', `var(--rh-chart-${s.color || 'accent'})`);
            if (spec.type === 'line') li.className = 'rh-chart-key-line' + (i ? ' rh-chart-key-dashed' : '');
            return li;
        }));
        el.hidden = !items.length;
    }

    function wrapped(label, width, ctx) {
        const lines = [];
        let current = '';
        for (const word of String(label).split(/\s+/)) {
            if (current && ctx.measureText(current + ' ' + word).width > width) { lines.push(current); current = ''; }
            if (current) current += ' ';
            for (const ch of word) {
                if (current && ctx.measureText(current + ch).width > width) { lines.push(current); current = ''; }
                current += ch;
            }
        }
        if (current) lines.push(current);
        if (lines.length > 3) return [lines[0], lines[1], lines[2].slice(0, -1) + '…'];
        return lines;
    }

    function datasets(spec) {
        return spec.series.map((s, i) => {
            const colors = spec.colors || (spec.semantic ? spec.labels.map(label => colorFor(spec.semantic, label)) : null);
            const color = tokenColor(s.color || 'accent');
            return { label: s.label, data: s.values, backgroundColor: colors ? colors.map(tokenColor) : color,
                borderColor: spec.type === 'bar' && spec.series.length === 1 ? color : spec.type === 'line' ? color : tokenColor('surface'),
                borderWidth: spec.type === 'line' ? 2.5 : spec.type === 'doughnut' || spec.series.length > 1 ? 1.5 : 0,
                fill: false, tension: 0, spanGaps: false, borderDash: spec.type === 'line' && i ? [6, 4] : [],
                pointStyle: i ? 'rectRot' : 'circle', pointRadius: 3, pointHoverRadius: 5,
                borderRadius: spec.type === 'bar' && spec.series.length === 1 ? 4 : 0,
                borderSkipped: false, maxBarThickness: spec.horizontal ? 16 : 40 };
        });
    }

    function options(spec) {
        const horizontal = spec.horizontal, stacked = spec.type === 'bar' && spec.series.length > 1;
        const format = spec.format || fmt;
        const ink = tokenColor('ink'), muted = tokenColor('muted'), surface = tokenColor('surface');
        const font = { family: FONT, size: Number(token('--rh-chart-tick', '12')), weight: '600' };
        const category = { stacked, grid: { display: false }, border: { display: false }, ticks: {
            autoSkip: !horizontal, maxRotation: 0, color: ink, padding: 5, font,
            callback(n) {
                const label = this.getLabelForValue(n);
                if (horizontal) {
                    const ctx = this.chart.ctx;
                    ctx.save(); ctx.font = `${font.weight} ${font.size}px ${FONT}`;
                    const lines = wrapped(label, Math.max(48, Math.min(190, this.chart.width * .34) - 10), ctx);
                    ctx.restore();
                    return lines;
                }
                return label;
            }
        } };
        const numeric = { stacked, beginAtZero: true, grace: spec.type === 'bar' && !horizontal ? '18%' : '5%',
            grid: { color: tokenColor('grid'), drawTicks: false }, border: { display: false }, ticks: {
                color: muted, font, padding: 5, maxTicksLimit: horizontal ? 5 : 6, precision: spec.integer ? 0 : undefined,
                callback: spec.currency ? v => '₱' + new Intl.NumberFormat('en-PH', { notation: 'compact', maximumFractionDigits: 1 }).format(v) : fmt
            } };
        return { responsive: true, maintainAspectRatio: false, animation: false, color: ink, font,
            indexAxis: horizontal ? 'y' : 'x', interaction: spec.type === 'doughnut' ? { mode: 'nearest', intersect: true } : { mode: 'index', axis: horizontal ? 'y' : 'x', intersect: false },
            ...(spec.type === 'doughnut' ? { cutout: '70%' } : { scales: { x: horizontal ? numeric : category, y: horizontal ? category : numeric } }),
            plugins: { legend: { display: false }, tooltip: {
                backgroundColor: ink, titleColor: surface, bodyColor: surface, padding: 10, displayColors: spec.series.length > 1,
                callbacks: {
                    title: items => items.length ? spec.labels[items[0].dataIndex] : '',
                    label: item => {
                        const v = item.raw;
                        if (v == null) return 'No observation';
                        if (spec.type === 'doughnut') return `${item.label}: ${format(v)} h (${percent(v, spec.series[0].values)})`;
                        const share = spec.share ? ' (' + percent(v, spec.series[0].values) + ')' : '';
                        return `${item.dataset.label}: ${format(v)}${share}`;
                    }
                }
            } }
        };
    }

    function percent(value, values) {
        const total = values.reduce((a, n) => a + (Number(n) || 0), 0);
        return total ? (100 * value / total).toFixed(1).replace(/\.0$/, '') + '%' : '—';
    }

    function decorations(spec) {
        return { id: 'rhChartDecorations', beforeDraw(chart) {
            const ctx = chart.ctx;
            ctx.save(); ctx.globalCompositeOperation = 'destination-over';
            ctx.fillStyle = tokenColor('surface'); ctx.fillRect(0, 0, chart.width, chart.height); ctx.restore();
        }, afterDatasetsDraw(chart) {
            const { ctx, chartArea } = chart;
            if (!chartArea) return;
            ctx.save(); ctx.fillStyle = tokenColor('ink');
            const size = Number(token('--rh-chart-tick', '12'));
            const f = spec.format || fmt;
            if (spec.type === 'doughnut') {
                const arc = chart.getDatasetMeta(0).data[0];
                if (arc && arc.innerRadius > 25) {
                    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.font = `750 21px ${FONT}`;
                    ctx.fillText(percent(spec.series[0].values[0], spec.series[0].values), arc.x, arc.y - 6);
                    ctx.font = `600 ${size}px ${FONT}`;
                    ctx.fillText(spec.labels[0], arc.x, arc.y + 12);
                }
            } else if (spec.type === 'bar') {
                const meta = chart.getDatasetMeta(spec.series.length - 1);
                ctx.font = `700 ${size}px ${FONT}`;
                meta.data.forEach((el, i) => {
                    const raw = spec.totals ? spec.totals[i] : spec.series[0].values[i];
                    if (raw == null || !Number.isFinite(el.x + el.y)) return;
                    const label = f(raw), width = ctx.measureText(label).width;
                    if (spec.horizontal) {
                        const negative = Number(raw) < 0;
                        const x = negative ? el.x - 5 : el.x + 5;
                        ctx.textAlign = negative ? 'right' : 'left'; ctx.textBaseline = 'middle';
                        if (x >= chartArea.left && x <= chartArea.right && (negative ? x - width >= 0 : x + width <= chart.width - 2)) ctx.fillText(label, x, el.y);
                    } else if (width + 4 < chartArea.width / spec.labels.length) {
                        ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(label, el.x, el.y - 5);
                    }
                });
            }
            ctx.restore();
        } };
    }

    function resize() {
        mounted.forEach((entry, canvas) => {
            if (!canvas.isConnected) { remove(canvas); return; }
            if (canvas.offsetParent) entry.resize();
        });
    }

    function mount(canvas, spec) {
        if (!canvas) return null;
        if (mounted.has(canvas)) { const chart = mounted.get(canvas); chart.resize(); return chart; }
        canvas.setAttribute('role', 'img');
        if (!canvas.hasAttribute('aria-label')) canvas.setAttribute('aria-label', canvasTitle(canvas));
        values(canvas, spec);
        legend(canvas, spec);
        if (!spec.labels.length) { state(canvas, spec.empty || 'No data available for this chart.'); return null; }
        if (!window.Chart) {
            state(canvas, 'Chart unavailable. Exact values are available below.');
            return null;
        }
        state(canvas);
        canvas.parentElement.style.setProperty('--rh-plot-height', spec.horizontal ? `max(9rem, ${spec.labels.length * 3 + 2}rem)` : '15rem');
        try {
            if (!canvas.getContext('2d')) throw new Error('Canvas context unavailable');
            const chart = new window.Chart(canvas, { type: spec.type || 'bar', data: { labels: spec.labels, datasets: datasets(spec) },
                options: options(spec), plugins: [decorations(spec)] });
            if (!chart.canvas || !chart.ctx) {
                chart.destroy();
                throw new Error('Chart initialization failed');
            }
            chart.rhSpec = spec;
            mounted.set(canvas, chart);
            return chart;
        } catch (_) {
            window.Chart.getChart?.(canvas)?.destroy();
            state(canvas, 'Chart unavailable. Exact values are available below.');
            return null;
        }
    }

    function remove(canvas) {
        mounted.get(canvas)?.destroy();
        mounted.delete(canvas);
    }

    function refreshMounted() {
        mounted.forEach((chart, canvas) => {
            if (!canvas.isConnected) { remove(canvas); return; }
            const styledDatasets = datasets(chart.rhSpec);
            chart.data.datasets.forEach((dataset, i) => Object.assign(dataset, styledDatasets[i]));
            chart.options = options(chart.rhSpec);
            if (canvas.offsetParent) chart.update('none');
        });
    }

    function lazy(paneID, buttonID, render) {
        const pane = document.getElementById(paneID);
        const button = document.getElementById(buttonID);
        if (!pane) return;
        let rendered = false;
        function show() {
            if (!rendered) { render(); rendered = true; }
            else refreshMounted();
            resize();
        }
        if (pane.classList.contains('active') && pane.classList.contains('show')) show();
        button?.addEventListener('shown.bs.tab', show);
    }

    function png(chart) {
        const spec = chart.rhSpec;
        if (spec.series.length < 2 && spec.type !== 'doughnut') return chart.toBase64Image();
        const legend = chart.options.plugins.legend;
        legend.display = true;
        legend.labels.color = tokenColor('ink');
        legend.labels.usePointStyle = true;
        legend.labels.font = { family: FONT, size: Number(token('--rh-chart-tick', '12')) };
        try {
            chart.update('none');
            return chart.toBase64Image();
        } finally {
            legend.display = false;
            chart.update('none');
        }
    }

    window.rhCharts = { mount, remove, read, rank, lazy, percent, png };
    window.addEventListener('requesthub:theme-changed', refreshMounted);
}());
