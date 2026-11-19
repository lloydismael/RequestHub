(function () {
    'use strict';

    const root = document.getElementById('sqr-reports-root');
    if (!root) return;
    const charts = window.rhCharts;
    const definitions = [
        ['sqrFunnelChart', 'funnel', 'Submissions'],
        ['sqrDealStageChart', 'deal', 'Deals'],
        ['sqrRevenueOverviewChart', 'revenue', 'Value (PHP)'],
        ['sqrMonthlyTrendChart', 'monthly', 'Submissions'],
        ['sqrGroupDistributionChart', 'group', 'Submissions']
    ];
    const months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');

    function withMissingMonths(data) {
        const labels = [], values = [];
        let previous;
        data.labels.forEach((label, i) => {
            const [month, year] = label.split(' ');
            const monthIndex = months.indexOf(month);
            const current = monthIndex >= 0 && /^\d{4}$/.test(year) ? Number(year) * 12 + monthIndex : undefined;
            for (let skipped = previous + 1; skipped < current; skipped++) {
                labels.push(`${months[skipped % 12]} ${Math.floor(skipped / 12)}`);
                values.push(null);
            }
            labels.push(label);
            values.push(data.totals[i]);
            previous = current;
        });
        return { labels, totals: values };
    }

    function render() {
        definitions.forEach(([id, key, label]) => {
            const source = charts.read(`sqr-reports-${key}-chart-data`) || { labels: [], totals: [] };
            const trend = key === 'monthly', currency = key === 'revenue';
            const color = currency ? 'positive' : 'accent';
            const data = trend ? withMissingMonths(source) : source;
            const series = totals => [{ label, values: totals, color }];
            charts.mount(document.getElementById(id), {
                labels: data.labels, series: series(data.totals), type: trend ? 'line' : 'bar', horizontal: !trend,
                values: trend ? { labels: source.labels, series: series(source.totals) } : undefined,
                currency, integer: !currency, fitCategoryLabels: true, format: currency ? window.rhChartValues.pesos : undefined,
                empty: 'No data available for this chart.'
            });
        });
    }

    function replaceReports(html) {
        const open = new Set();
        let focusedId, focusedSelector;
        root.querySelectorAll('canvas').forEach(canvas => {
            const details = canvas.closest('.rh-chart-card').querySelector('details');
            if (details.open) open.add(canvas.id);
            if (details.contains(document.activeElement)) {
                focusedId = canvas.id;
                focusedSelector = document.activeElement.matches('summary') ? 'summary' : '[data-chart-values]';
            }
            charts.remove(canvas);
        });
        root.innerHTML = html;
        render();
        open.forEach(id => {
            const details = document.getElementById(id)?.closest('.rh-chart-card').querySelector('details');
            if (details && !details.hidden) details.open = true;
        });
        if (focusedId) {
            const details = document.getElementById(focusedId)?.closest('.rh-chart-card').querySelector('details');
            if (!details) return;
            const target = details.hidden ? details.closest('.rh-chart-card').querySelector('h3') : details.querySelector(focusedSelector);
            if (details.hidden) target.tabIndex = -1;
            target.focus({ preventScroll: true });
            if (target.getBoundingClientRect().top < 0 || target.getBoundingClientRect().bottom > window.innerHeight) {
                target.scrollIntoView({ block: 'center', behavior: 'instant' });
            }
        }
    }

    render();
    if (new URLSearchParams(window.location.search).get('tab') !== 'reports') return;
    const refreshUrl = root.dataset.refreshUrl;
    let inFlight = false, lastHtml;

    function refreshReports() {
        if (!refreshUrl || inFlight || document.hidden || !root.isConnected) return;
        const style = window.getComputedStyle(root);
        if (style.display === 'none' || style.visibility === 'hidden') return;
        inFlight = true;
        return fetch(refreshUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(response => response.json())
            .then(data => {
                if (!data.ok || !data.html || data.html === lastHtml) return;
                replaceReports(data.html);
                lastHtml = data.html;
            })
            .catch(() => {})
            .finally(() => { inFlight = false; });
    }

    window.refreshSqrReports = refreshReports;
    window.setInterval(refreshReports, 15000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshReports(); });
}());
