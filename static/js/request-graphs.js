(function () {
    'use strict';

    const charts = window.rhCharts;

    function draw(id, data, { label = 'Requests', semantic, trend = false, field = 'values', share = false, empty } = {}) {
        const series = [{ label, values: data?.[field] || [] }];
        const labels = data?.labels || [];
        const spec = trend ? { labels, series } : charts.rank(labels, series);
        if (share) spec.extraValues = [{ label: 'Share', values: spec.series[0].values.map(v => charts.percent(v, series[0].values)), format: String }];
        charts.mount(document.getElementById(id), { ...spec, type: trend ? 'line' : 'bar', horizontal: !trend,
            integer: label !== 'Hours', semantic, share, empty });
        return spec;
    }

    const requestor = charts.read('requestor-report-data');
    if (requestor) charts.lazy('request-report-graph-pane', 'request-report-graph-tab', () => {
        draw('requestor-status-chart', requestor.request_status, { semantic: 'status' });
        draw('requestor-priority-chart', requestor.request_priority, { semantic: 'priority' });
        draw('requestor-engagement-chart', requestor.request_engagement);
        draw('requestor-monthly-chart', requestor.request_monthly, { trend: true });
    });

    const activity = charts.read('activity-report-data');
    if (activity) charts.lazy('activity-graph-pane', 'activity-graph-tab', () => {
        draw('request-status-chart', activity.request_status, { semantic: 'status' });
        draw('request-priority-chart', activity.request_priority, { semantic: 'priority' });
        draw('activity-type-chart', activity.activity_type, { label: 'Activities' });
        draw('activity-monthly-chart', activity.monthly, { label: 'Hours', field: 'hours', trend: true });
    });

    const engineer = charts.read('engineer-report-data');
    if (engineer) {
        const { labels, series: [{ values }] } = draw('engineer-requestor-chart', engineer.requests_by_requestor,
            { share: true, empty: 'No assigned requests yet. Once requests are assigned to you, they will appear here grouped by requestor.' });
        const summary = document.getElementById('engineer-requestor-chart-summary');
        if (!summary) return;
        summary.querySelector('[data-summary="total"]').textContent = 'Total: ' + values.reduce((n, v) => n + Number(v || 0), 0);
        summary.querySelector('[data-summary="requestors"]').textContent = 'Requestors: ' + labels.length;
        summary.querySelector('[data-summary="top"]').textContent = labels.length ? `Top: ${labels[0]} (${values[0]})` : 'Top: —';
        summary.setAttribute('aria-hidden', 'false');
    }
}());
