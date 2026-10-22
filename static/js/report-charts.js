/* Reports page charts. Data comes from json_script nodes named "<canvasId>-data"
   (and optionally "<canvasId>-full-data" for the expanded view). Requires Chart.js and chart-theme.js. */
(function () {
    'use strict';

    if (typeof Chart === 'undefined') {
        return;
    }

    function hexToRgba(hex, alpha) {
        var h = (hex || '').replace('#', '').trim();
        if (h.length === 3) {
            h = h.split('').map(function (c) { return c + c; }).join('');
        }
        if (h.length !== 6) {
            return 'rgba(10, 132, 255, ' + alpha + ')';
        }
        return 'rgba(' + parseInt(h.slice(0, 2), 16) + ', ' + parseInt(h.slice(2, 4), 16) + ', ' + parseInt(h.slice(4, 6), 16) + ', ' + alpha + ')';
    }

    function token(name, fallback) {
        var bodyVal = getComputedStyle(document.body).getPropertyValue(name).trim();
        if (bodyVal) return bodyVal;
        var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }

    function readJson(id) {
        var node = document.getElementById(id);
        if (!node || !node.textContent) return null;
        try { return JSON.parse(node.textContent); } catch (_) { return null; }
    }

    function buildPalette() {
        var c = (window.lgChartPalette && window.lgChartPalette.length) ? window.lgChartPalette : [
            token('--lg-accent', '#0a84ff'), token('--lg-ok', '#30d158'), token('--lg-warn', '#ff9f0a'),
            token('--lg-crit', '#ff453a'), token('--lg-violet', '#bf5af0'), token('--lg-teal', '#40c8e0')
        ];
        function tone(hex, fallback) {
            return { bg: hexToRgba(hex || fallback, 0.55), b: hexToRgba(hex || fallback, 0.95), soft: hexToRgba(hex || fallback, 0.14) };
        }
        return {
            blue: tone(c[0], '#0a84ff'),
            green: tone(c[1], '#30d158'),
            amber: tone(c[2], '#ff9f0a'),
            rose: tone(c[3], '#ff453a'),
            violet: tone(c[4], '#bf5af0'),
            cyan: tone(c[5], '#40c8e0'),
            gray: { bg: 'rgba(100,116,139,0.45)', b: 'rgba(100,116,139,0.85)', soft: 'rgba(100,116,139,0.14)' }
        };
    }

    var P = buildPalette();
    var reducedMotion = !!window.lgReducedMotion;
    var registry = {};

    function ink() { return token('--lg-ink', '#0b1220'); }
    function inkSoft() { return token('--lg-ink-soft', 'rgba(11,18,32,0.62)'); }
    function divider() { return token('--lg-divider', 'rgba(11,18,32,0.09)'); }
    function tooltipBg() {
        return document.body.classList.contains('dark-mode') ? 'rgba(15, 23, 42, 0.96)' : 'rgba(255, 255, 255, 0.96)';
    }

    function fmt(value, unit) {
        var n = Number(value) || 0;
        return (unit === ' hrs' ? Math.round(n * 10) / 10 : n) + (unit || '');
    }

    // Totals above stacked bars.
    var totalLabelsPlugin = {
        id: 'totalLabels',
        afterDatasetsDraw: function (chart) {
            var opts = chart.options.plugins && chart.options.plugins.totalLabels;
            if (!opts || !opts.display) return;
            var meta = chart.getDatasetMeta(chart.data.datasets.length - 1);
            if (!meta) return;
            var ctx = chart.ctx;
            meta.data.forEach(function (el, i) {
                var val = opts.totals[i];
                if (val == null || !Number.isFinite(el.y)) return;
                ctx.save();
                ctx.fillStyle = ink();
                ctx.font = '700 11px ' + (Chart.defaults.font.family || 'system-ui');
                ctx.textAlign = 'center';
                ctx.textBaseline = 'bottom';
                ctx.fillText(val, el.x, el.y - 5);
                ctx.restore();
            });
        }
    };

    var donutCenterPlugin = {
        id: 'donutCenter',
        afterDraw: function (chart) {
            var opts = chart.options.plugins && chart.options.plugins.donutCenter;
            if (!opts || !opts.display) return;
            var meta = chart.getDatasetMeta(0);
            if (!meta || !meta.data.length) return;
            var total = (chart.data.datasets[0].data || []).reduce(function (a, b) { return a + (Number(b) || 0); }, 0);
            var ctx = chart.ctx;
            var el = meta.data[0];
            ctx.save();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = ink();
            ctx.font = '750 20px ' + (Chart.defaults.font.family || 'system-ui');
            ctx.fillText(Math.round(total * 10) / 10, el.x, el.y - 6);
            ctx.fillStyle = inkSoft();
            ctx.font = '600 10px ' + (Chart.defaults.font.family || 'system-ui');
            ctx.fillText(opts.label || 'TOTAL', el.x, el.y + 14);
            ctx.restore();
        }
    };

    function destroyExisting(target) {
        var existing = typeof Chart.getChart === 'function' ? Chart.getChart(target) : null;
        if (existing) existing.destroy();
    }

    function chartTitle(canvas) {
        var card = canvas.closest('.rpt-chart-card');
        var title = card && card.querySelector('.rpt-chart-title');
        return title ? title.textContent.trim() : '';
    }

    function tooltipBase() {
        return {
            backgroundColor: tooltipBg(), titleColor: ink(), bodyColor: ink(),
            borderColor: divider(), borderWidth: 1, displayColors: true, boxPadding: 4
        };
    }

    function axisScales(stacked, showAll) {
        return {
            x: {
                stacked: stacked,
                grid: { display: false },
                ticks: { color: ink(), autoSkip: !showAll, maxRotation: showAll ? 55 : 35, minRotation: 0, maxTicksLimit: showAll ? undefined : 16, font: { weight: 600 } }
            },
            y: {
                stacked: stacked,
                beginAtZero: true,
                grace: '10%',
                grid: { color: divider(), drawTicks: false },
                ticks: { color: ink(), precision: 0, maxTicksLimit: 6, font: { weight: 600 } }
            }
        };
    }

    // spec: { datasets: [{key, label, color}], unit, totals(payload) -> array }
    function stackedBar(payload, spec, fullPayload) {
        var build = function (target, override, showAll) {
            var data = override || payload;
            destroyExisting(target);
            var totals = spec.totals ? spec.totals(data) : null;
            var tooltip = tooltipBase();
            tooltip.callbacks = {
                label: function (ctx) { return ctx.dataset.label + ': ' + fmt(ctx.parsed.y, spec.unit); },
                afterBody: function (items) {
                    return items.length && totals ? 'Total: ' + fmt(totals[items[0].dataIndex], spec.unit) : '';
                }
            };
            return new Chart(target, {
                type: 'bar',
                data: {
                    labels: data.labels,
                    datasets: spec.datasets.map(function (d) {
                        return {
                            label: d.label, data: data[d.key] || [],
                            backgroundColor: P[d.color].bg, borderColor: P[d.color].b,
                            borderWidth: 0, borderRadius: 6, borderSkipped: false, stack: 's'
                        };
                    })
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: reducedMotion ? 0 : 600, easing: 'easeOutQuart' },
                    scales: axisScales(true, showAll),
                    plugins: {
                        legend: { position: 'top', align: 'end' },
                        tooltip: tooltip,
                        totalLabels: { display: !!totals, totals: totals || [] }
                    }
                },
                plugins: [totalLabelsPlugin]
            });
        };
        var hasFull = fullPayload && fullPayload.labels && fullPayload.labels.length;
        return {
            build: build,
            expand: function (target) { return build(target, hasFull ? fullPayload : null, true); },
            datasets: spec.datasets, payload: payload, fullPayload: fullPayload, unit: spec.unit
        };
    }

    function trendLine(payload) {
        var spec = [
            { key: 'created', label: 'Created', color: 'blue' },
            { key: 'completed', label: 'Completed', color: 'green' }
        ];
        var build = function (target) {
            destroyExisting(target);
            return new Chart(target, {
                type: 'line',
                data: {
                    labels: payload.labels,
                    datasets: spec.map(function (d) {
                        return {
                            label: d.label, data: payload[d.key] || [],
                            borderColor: P[d.color].b, backgroundColor: P[d.color].soft,
                            fill: true, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: P[d.color].b
                        };
                    })
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    animation: { duration: reducedMotion ? 0 : 600, easing: 'easeOutQuart' },
                    scales: axisScales(false, false),
                    plugins: { legend: { position: 'top', align: 'end' }, tooltip: tooltipBase() }
                }
            });
        };
        return { build: build, expand: build, datasets: spec, payload: payload, unit: '' };
    }

    function donut(payload, colors) {
        var build = function (target) {
            destroyExisting(target);
            var tooltip = tooltipBase();
            tooltip.callbacks = {
                label: function (ctx) {
                    var v = ctx.raw != null ? ctx.raw : ctx.parsed;
                    return ctx.label + ': ' + fmt(v, ' hrs');
                }
            };
            return new Chart(target, {
                type: 'doughnut',
                data: {
                    labels: payload.labels,
                    datasets: [{
                        data: payload.totals,
                        backgroundColor: colors.map(function (c) { return P[c].bg; }),
                        borderColor: colors.map(function (c) { return P[c].b; }),
                        borderWidth: 2, hoverOffset: 10, borderRadius: 5
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '62%',
                    animation: { duration: reducedMotion ? 0 : 650, easing: 'easeOutQuart', animateRotate: !reducedMotion },
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 8, boxHeight: 8, padding: 12 } },
                        tooltip: tooltip,
                        donutCenter: { display: true, label: 'HOURS' }
                    }
                },
                plugins: [donutCenterPlugin]
            });
        };
        return { build: build, expand: build, datasets: [{ key: 'totals', label: 'Hours' }], payload: payload, unit: ' hrs' };
    }

    // Accessible text alternative: fills the "View chart values" table.
    function fillValues(id, entry) {
        var holder = document.querySelector('[data-chart-values="' + id + '"]');
        if (!holder) return;
        var table = document.createElement('table');
        table.className = 'table table-sm align-middle mb-0 rpt-values-table';
        var head = document.createElement('tr');
        ['Label'].concat(entry.datasets.map(function (d) { return d.label; })).forEach(function (text) {
            var th = document.createElement('th');
            th.scope = 'col';
            th.textContent = text;
            head.appendChild(th);
        });
        var thead = document.createElement('thead');
        thead.appendChild(head);
        table.appendChild(thead);
        var tbody = document.createElement('tbody');
        var hasFull = entry.fullPayload && entry.fullPayload.labels && entry.fullPayload.labels.length;
        var source = hasFull ? entry.fullPayload : entry.payload;
        source.labels.forEach(function (label, i) {
            var tr = document.createElement('tr');
            var th = document.createElement('th');
            th.scope = 'row';
            th.textContent = label;
            tr.appendChild(th);
            entry.datasets.forEach(function (d) {
                var td = document.createElement('td');
                td.textContent = fmt((source[d.key] || [])[i], entry.unit);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        holder.replaceChildren(table);
    }

    function requestSpec(secondColor) {
        return {
            datasets: [{ key: 'completed', label: 'Completed', color: 'green' }, { key: 'ongoing', label: 'Ongoing', color: secondColor }],
            unit: '',
            totals: function (d) { return d.totals; }
        };
    }

    function hourSpec(a, b) {
        return {
            datasets: [{ key: 'billable', label: 'Billable', color: a }, { key: 'non_billable', label: 'Not Billable', color: b }],
            unit: ' hrs',
            totals: function (d) {
                return d.labels.map(function (_, i) { return Math.round(((d.billable[i] || 0) + (d.non_billable[i] || 0)) * 10) / 10; });
            }
        };
    }

    var FACTORIES = {
        engineerChart: function (p, f) { return stackedBar(p, requestSpec('violet'), f); },
        accountManagerChart: function (p, f) { return stackedBar(p, requestSpec('violet'), f); },
        engagementChart: function (p) { return stackedBar(p, requestSpec('blue')); },
        productChart: function (p) { return stackedBar(p, requestSpec('amber')); },
        requestTrendChart: function (p) { return trendLine(p); },
        activityEngineerChart: function (p) { return stackedBar(p, hourSpec('green', 'gray')); },
        activityTypeChart: function (p) { return stackedBar(p, hourSpec('blue', 'amber')); },
        activityAccountChart: function (p) { return stackedBar(p, hourSpec('green', 'gray')); },
        activityMonthlyChart: function (p) { return stackedBar(p, hourSpec('green', 'gray')); },
        activityBillableChart: function (p) { return donut(p, ['green', 'gray']); },
        activityLocationChart: function (p) { return donut(p, ['blue', 'green', 'amber', 'violet', 'cyan', 'rose', 'gray']); }
    };

    function init() {
        document.querySelectorAll('canvas[data-report-chart]').forEach(function (canvas) {
            var id = canvas.id;
            var payload = readJson(id + '-data');
            var factory = FACTORIES[id];
            if (!factory || !payload || !payload.labels || !payload.labels.length) return;
            var entry = factory(payload, readJson(id + '-full-data'));
            entry.title = chartTitle(canvas);
            entry.build(canvas);
            registry[id] = entry;
            fillValues(id, entry);
        });
    }

    function refreshAll() {
        P = buildPalette();
        Object.keys(registry).forEach(function (id) {
            var canvas = document.getElementById(id);
            if (!canvas) return;
            var old = registry[id];
            var fresh = FACTORIES[id](old.payload, old.fullPayload);
            fresh.title = old.title;
            registry[id] = fresh;
            fresh.build(canvas);
        });
    }

    function initModal() {
        var modal = document.getElementById('rptChartModal');
        if (!modal) return;
        var titleEl = document.getElementById('rptChartModalTitle');
        var closeBtn = document.getElementById('rptChartModalClose');
        var pngBtn = document.getElementById('rptChartModalPng');
        var activeChart = null;
        var returnFocus = null;

        function open(id, trigger) {
            var entry = registry[id];
            if (!entry) return;
            returnFocus = trigger || document.activeElement;
            titleEl.textContent = entry.title;
            if (activeChart) { activeChart.destroy(); activeChart = null; }
            modal.classList.add('is-open');
            modal.setAttribute('aria-hidden', 'false');
            document.body.style.overflow = 'hidden';
            requestAnimationFrame(function () {
                var canvas = document.getElementById('rptModalCanvas');
                if (canvas) activeChart = entry.expand(canvas);
                if (closeBtn) closeBtn.focus();
            });
        }

        function close() {
            modal.classList.remove('is-open');
            modal.setAttribute('aria-hidden', 'true');
            document.body.style.overflow = '';
            if (activeChart) { activeChart.destroy(); activeChart = null; }
            if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
            returnFocus = null;
        }

        document.querySelectorAll('[data-expand-chart]').forEach(function (btn) {
            btn.addEventListener('click', function () { open(btn.dataset.expandChart, btn); });
        });
        if (pngBtn) {
            pngBtn.addEventListener('click', function () {
                if (!activeChart) return;
                var link = document.createElement('a');
                link.download = (titleEl.textContent || 'chart').toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.png';
                link.href = activeChart.toBase64Image();
                link.click();
            });
        }
        if (closeBtn) closeBtn.addEventListener('click', close);
        modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
        document.addEventListener('keydown', function (e) {
            if (!modal.classList.contains('is-open')) return;
            if (e.key === 'Escape') { close(); return; }
            if (e.key === 'Tab') {
                var focusable = Array.prototype.slice.call(modal.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
                if (!focusable.length) return;
                var first = focusable[0];
                var last = focusable[focusable.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            }
        });
    }

    init();
    initModal();
    if (typeof window.lgChartOnTheme === 'function') {
        window.lgChartOnTheme(refreshAll);
    }
}());
