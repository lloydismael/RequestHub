/* Classic uses the same URL state; row predicates/totals remain server-side. */
(function () {
    'use strict';
    var filters = window.RequestHubSqrFilters;
    var root = document.getElementById('sqr-classic-filters');
    if (!filters || !root) return;
    var panel = root.closest('[data-sqr-view-panel]');
    var defaults = root.dataset.defaultAssigned || 'all';
    var state = filters.fromPage(defaults), options = {};
    try { options = JSON.parse(document.getElementById(root.dataset.optionsId).textContent) || {}; } catch (error) { /* selected invalid values keep a notice */ }
    var widget = filters.mount(root, {state: state, options: options, onChange: function (next) { filters.write(next); }});
    widget.setCount(Number(root.dataset.initialCount || 0));
    var serverKey = filters.toQuery(filters.read(new URLSearchParams(root.dataset.serverQuery || ''), {defaultAssigned: defaults})).toString();
    var refreshPromise = null;
    window.applySqrTableFilter = function () {
        if (panel.hidden || refreshPromise) return refreshPromise;
        var params = filters.toQuery(widget.getState());
        var focus = new URLSearchParams(window.location.search).get('focus');
        if (focus) params.set('focus', focus);
        params.set('page', '1'); params.set('summary', '1');
        refreshPromise = fetch(root.dataset.summaryEndpoint + '?' + params.toString(), {credentials: 'same-origin', cache: 'no-store'})
            .then(function (response) { if (!response.ok) throw new Error('Unable to refresh filter counts.'); return response.json(); })
            .then(function (data) { if (data.ok && data.pipeline && data.pipeline.all) widget.setCount(data.pipeline.all.count); })
            .catch(function () {}).then(function () { refreshPromise = null; });
        return refreshPromise;
    };
    document.addEventListener('sqr:filters-changed', function (event) {
        state = event.detail.state; widget.setState(state);
        if (!panel.hidden) window.location.assign(window.location.href);
    });
    document.addEventListener('sqr:performance-view', function (event) {
        if (event.detail.view !== 'tracker') return;
        var current = filters.fromPage(defaults); widget.setState(current);
        if (filters.toQuery(current).toString() !== serverKey) filters.write(current, {resetPage: false, clearFocus: false});
    });
    document.addEventListener('sqr:performance-invalidated', window.applySqrTableFilter);
    document.querySelectorAll('#sqr-badge-filters .sqr-badge-filter').forEach(function (button) {
        button.addEventListener('click', function () {
            var current = widget.getState(), key = button.dataset.sqrFilter || '';
            if (!key) { current.assigned = 'all'; current.selected.status = []; current.selected.proposal_status = []; }
            else if (key === 'my_assigned') current.assigned = current.assigned === 'mine' ? 'all' : 'mine';
            else {
                var parts = key.split(':'), field = parts[0], value = parts[1], values = current.selected[field] || [];
                current.selected[field] = values.indexOf(value) === -1 ? values.concat([value]) : values.filter(function (item) { return item !== value; });
            }
            filters.write(current);
        });
    });
    var params = new URLSearchParams(window.location.search), focus = params.get('focus');
    var notice = document.getElementById('sqr-pm-tracker-focus');
    if (focus && notice && !panel.hidden) {
        notice.hidden = false;
        var target = panel.querySelector('[data-sqr-pk="' + focus.replace(/[^0-9]/g, '') + '"]');
        var label = document.getElementById('sqr-pm-tracker-focus-label');
        if (label) label.textContent = target ? 'Temporary focused SQR; your filters are retained.' : 'Focused SQR is unavailable in your authorized scope.';
    }
    var dismiss = document.getElementById('sqr-pm-tracker-focus-dismiss');
    if (dismiss) dismiss.addEventListener('click', function () { params.delete('focus'); window.location.assign(window.location.pathname + '?' + params.toString()); });
    window.focusSqrTrackerProject = function (pk) {
        var value = String(pk).trim();
        if (!/^\d+$/.test(value)) return false;
        value = value.replace(/^0+/, '');
        if (!value || value.length > 19 || (value.length === 19 && value > '9223372036854775807')) return false;
        var query = new URLSearchParams(window.location.search);
        query.set('view', 'tracker'); query.set('focus', value); query.delete('page');
        window.location.assign(window.location.pathname + '?' + query.toString());
        return true;
    };
    if (!panel.hidden && filters.toQuery(state).toString() !== serverKey) filters.write(state, {resetPage: false, clearFocus: false});
})();
