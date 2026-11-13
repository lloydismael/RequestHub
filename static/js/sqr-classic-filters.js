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
    var refreshPromise = null, refreshController = null, refreshVersion = 0;
    window.applySqrTableFilter = function (event) {
        if (panel.hidden) return refreshPromise;
        if (refreshController) refreshController.abort();
        refreshController = new AbortController();
        var version = ++refreshVersion;
        var mutation = event && event.type === 'sqr:performance-invalidated';
        var params = filters.toQuery(widget.getState()), key = params.toString();
        var query = new URLSearchParams(window.location.search), focus = query.get('focus');
        if (focus) params.set('focus', focus);
        params.set('page', query.get('page') || '1'); params.set('page_size', '50');
        refreshPromise = fetch(root.dataset.summaryEndpoint + '?' + params.toString(), {credentials: 'same-origin', cache: 'no-store', signal: refreshController.signal})
            .then(function (response) { if (!response.ok) throw new Error('Unable to refresh filter counts.'); return response.json(); })
            .then(function (data) {
                if (version !== refreshVersion || key !== filters.toQuery(widget.getState()).toString()) return;
                if (!data.ok || !data.pagination) throw new Error('Unable to refresh the filtered tracker page.');
                widget.setCount(data.pagination.total);
                if (mutation && !panel.hidden) {
                    var undo = typeof window.getSqrPendingUndo === 'function' ? window.getSqrPendingUndo() : null;
                    var retained = true;
                    try { if (undo) window.sessionStorage.setItem('sqr_tracker_pending_undo_v1', JSON.stringify(undo)); } catch (error) { retained = false; }
                    query.set('page', data.pagination.page);
                    if (!retained) {
                        var warning = document.createElement('p'); warning.className = 'sqr-filter-notice'; warning.setAttribute('role', 'alert');
                        warning.textContent = 'The change is saved. Browser storage is unavailable: use Undo, then reload to refresh the filtered rows. ';
                        var reload = document.createElement('button'); reload.type = 'button'; reload.textContent = 'Reload results';
                        reload.addEventListener('click', function () { window.location.assign(window.location.pathname + '?' + query.toString()); });
                        warning.append(reload); root.append(warning); return;
                    }
                    window.location.assign(window.location.pathname + '?' + query.toString());
                }
            }).catch(function (error) {
                if (version !== refreshVersion || error.name === 'AbortError') return;
                var notice = document.createElement('p'); notice.className = 'sqr-filter-notice'; notice.setAttribute('role', 'alert');
                notice.textContent = error.message + ' Showing the last loaded rows. Reload to retry.'; root.append(notice);
            }).then(function () { if (version === refreshVersion) { refreshPromise = null; refreshController = null; } });
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
    if (!panel.hidden && filters.toQuery(state).toString() !== serverKey) filters.write(state, {resetPage: false, clearFocus: false});
})();
