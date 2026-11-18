/* Read-only SQR snapshot controller. No chart dependency, polling or per-row requests. */
(function () {
    'use strict';
    var root = document.getElementById('sqr-performance-dashboard');
    if (!root || root.dataset.initialized) return;
    root.dataset.initialized = 'true';

    function el(id) { return document.getElementById('sqr-pm-' + id); }
    function node(tag, className, text) {
        var result = document.createElement(tag);
        if (className) result.className = className;
        if (text !== undefined) result.textContent = text;
        return result;
    }
    var core = window.SqrFilterCore;
    var filterLabels = {stage: 'Stage', status: 'SQR Status', proposal_status: 'Proposal Status', overall_status: 'Overall Status',
        pm_id: 'Project Manager', sse_id: 'SSE', schedule_performance: 'Schedule Performance', budget_effort_performance: 'Budget/Effort Performance',
        quality_performance: 'Quality Performance', scope: 'Scope', billing_type: 'Billing Type', billing_state: 'Billing Status',
        linked_request_id: 'Linked Request', group: 'Group/Client', portfolio_status: 'Portfolio Status', overall_rating: 'Overall Rating'};
    var filterKeys = Object.keys(filterLabels);
    var storeKey = 'requestHub.sqr.ppt.filters.v1';
    var filterControls = null;
    var preferenceNotices = [];
    var filters = core.normalizeFilters({mine: root.dataset.defaultMine === '1'}, filterKeys).state;
    var snapshot = null;
    var filtered = [];
    var pageNumber = 1;
    var pageSize = 24;
    var inFlight = null;
    var revision = 0;
    var dirty = true;
    var renderFrame = 0;
    var searchTimer = 0;
    var requestState = '';
    var dateFormat = new Intl.DateTimeFormat(undefined, {year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'});
    var portfolioLabels = {closed: 'Closed', ongoing: 'Ongoing', pipeline: 'Pipeline', removed: 'Removed'};
    var ratingLabels = {good: 'Healthy', warning: 'At Risk', danger: 'Action needed', unknown: 'Incomplete / Not assessed'};
    var indicatorLabels = [['schedule_performance', 'Schedule'], ['budget_effort_performance', 'Budget/Effort'], ['quality_performance', 'Quality']];
    var assessmentLabels = {
        schedule_performance: {on_track: 'On Track', at_risk: 'At Risk', delayed: 'Delayed', __not_assessed__: 'Not assessed'},
        budget_effort_performance: {within_budget: 'Within Budget', at_risk: 'At Risk', over_budget: 'Over Budget', __not_assessed__: 'Not assessed'},
        quality_performance: {met: 'Met', at_risk: 'At Risk', unmet: 'Unmet', __not_assessed__: 'Not assessed'}
    };
    function filterNotice(text) {
        if (preferenceNotices.indexOf(text) < 0) preferenceNotices.push(text);
        el('filter-notice').textContent = preferenceNotices.join(' ');
        el('filter-notice').hidden = !preferenceNotices.length;
    }
    function loadPreferences() {
        try {
            var saved = sessionStorage.getItem(storeKey);
            if (saved) {
                var normalized = core.normalizeFilters(JSON.parse(saved), filterKeys);
                filters = normalized.state;
                normalized.notices.forEach(filterNotice);
            }
        } catch (error) { filterNotice('Filter preferences could not be restored; use the filters for this visit.'); }
        var params = new URLSearchParams(window.location.search);
        var queryKeys = filterKeys.concat(['manager', 'health', 'portfolio', 'rating', 'search', 'q', 'mine', 'attention',
            'priceMin', 'priceMax', 'price_min', 'price_max']);
        core.dateKeys.forEach(function (key) { queryKeys.push(key + '_from', key + '_to'); });
        if (queryKeys.some(function (key) { return params.has(key); })) {
            var query = core.fromQuery(params, filterKeys, 'ppt');
            filters = query.state;
            query.notices.forEach(filterNotice);
        }
        el('search').value = filters.search;
    }
    function savePreferences() {
        try { sessionStorage.setItem(storeKey, JSON.stringify(filters)); }
        catch (error) { filterNotice('Filter preferences could not be saved; your current filters still work.'); }
    }
    function optionValue(project, key) {
        var value = project[key] == null ? '' : String(project[key]);
        if (!value && ['pm_id', 'sse_id', 'linked_request_id'].includes(key)) return '__unassigned__';
        if (!value && assessmentLabels[key]) return '__not_assessed__';
        return value;
    }
    function optionLabel(project, key, value) {
        if (value === '__unassigned__') return 'Unassigned';
        if (assessmentLabels[key]) return assessmentLabels[key][value];
        if (key === 'portfolio_status') return portfolioLabels[value];
        if (key === 'overall_rating') return ratingLabels[value];
        if (key === 'pm_id') return project.pm_name;
        if (key === 'sse_id') return project.sse_name;
        if (key === 'linked_request_id') return 'Request #' + value;
        return value.replace(/_/g, ' ').replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
    }
    function filterDimensions() {
        var projects = snapshot ? snapshot.projects : [];
        return filterKeys.map(function (key) {
            var options = new Map();
            var seeds = assessmentLabels[key] || (key === 'portfolio_status' ? portfolioLabels : key === 'overall_rating' ? ratingLabels : {});
            Object.keys(seeds).forEach(function (value) { options.set(value, {value: value, label: seeds[value], count: 0}); });
            var otherFilters = Object.assign({}, filters, {selections: Object.assign({}, filters.selections)});
            otherFilters.selections[key] = [];
            projects.forEach(function (project) {
                var value = optionValue(project, key);
                if (!value) return;
                var option = options.get(value) || {value: value, label: optionLabel(project, key, value), count: 0};
                if (core.matchesRecord(project, otherFilters, root.dataset.currentUserId)) option.count++;
                options.set(value, option);
            });
            return {key: key, label: filterLabels[key], options: Array.from(options.values()).sort(function (a, b) { return a.label.localeCompare(b.label); })};
        });
    }
    function renderFilters() {
        if (filterControls) { filterControls.setState(filters); return; }
        filterControls = window.SqrFilterControls.mount(el('filters'), {
            state: filters, getDimensions: filterDimensions,
            getResultCount: function () { return core.filterRecords(snapshot ? snapshot.projects : [], filters, root.dataset.currentUserId).length; },
            onApply: function (next) {
                var normalized = core.normalizeFilters(next, filterKeys);
                filters = normalized.state; normalized.notices.forEach(filterNotice);
                el('search').value = filters.search;
                pageNumber = 1; savePreferences(); scheduleRender();
            }
        });
    }
    loadPreferences();
    function visible() { return !root.hidden; }
    function formatDate(value) {
        return value ? dateFormat.format(new Date(value + 'T00:00:00Z')) : 'Not reported';
    }
    function progressBlock(project) {
        var block = node('div');
        block.append(node('div', 'sqr-pm-progress-label', project.progress === null ? 'Progress not reported' : project.progress + '% complete'));
        if (project.progress !== null) {
            var track = document.createElement('progress');
            track.className = 'sqr-pm-native-progress';
            track.max = 100;
            track.value = project.progress;
            track.setAttribute('aria-label', 'Reported completion for ' + project.project_name);
            track.textContent = project.progress + '%';
            block.append(track);
        }
        return block;
    }
    function replaceList(id, items, builder, emptyText) {
        var fragment = document.createDocumentFragment();
        items.forEach(function (item) { fragment.append(builder(item)); });
        if (!items.length && emptyText) fragment.append(node('p', 'sqr-pm-empty', emptyText));
        el(id).replaceChildren(fragment);
    }
    function attentionRow(project, performance) {
        var rating = performance ? project.overall_rating : (
            project.overall_rating === 'danger' ? 'danger' : 'warning'
        );
        var row = node('article', 'sqr-pm-attention-row sqr-pm-tone--' + rating);
        var identity = node('div');
        identity.append(node('h5', '', project.project_name));
        var detail = project.pm_name + ' · ' + (
            performance ? indicatorLabels.map(function (indicator) { return project.indicators[indicator[0]].label; }).join(' · ') + ' · ' + project.assessed_count + '/3 assessed'
            : project.reasons.map(function (reason) { return reason.label; }).join(' · ')
        );
        identity.append(node('p', 'sqr-pm-row-meta', detail));
        var badge = node('span', 'sqr-pm-tag ' + (performance ? 'sqr-pm-rating--' + rating : 'sqr-pm-state--' + project.portfolio_status),
            performance ? project.overall_label : portfolioLabels[project.portfolio_status] + (project.progress === null ? '' : ' · ' + project.progress + '%'));
        row.append(identity, badge);
        return row;
    }
    function renderPortfolio(counts) {
        var statusCounts = {closed: 0, ongoing: 0, pipeline: 0, removed: 0};
        var ratingCounts = {good: 0, warning: 0, danger: 0, unknown: 0};
        var managers = new Map();
        filtered.forEach(function (project) {
            statusCounts[project.portfolio_status]++;
            ratingCounts[project.overall_rating]++;
            var manager = managers.get(project.pm_id) || {name: project.pm_name, count: 0};
            manager.count++;
            managers.set(project.pm_id, manager);
        });
        Object.keys(statusCounts).forEach(function (key) { counts[key] = statusCounts[key]; });
        Object.keys(ratingCounts).forEach(function (key) { counts['rating_' + key] = ratingCounts[key]; });
        counts.closed_percent = filtered.length ? Math.round(statusCounts.closed / filtered.length * 100) + '%' : 'N/A';
        el('as-of').textContent = 'AS OF ' + formatDate(snapshot.as_of).toUpperCase();
        el('closed-coverage').textContent = statusCounts.closed + ' of ' + filtered.length + ' projects closed';
        el('rated-coverage').textContent = filtered.filter(function (project) { return project.assessed_count === 3; }).length + ' of ' + filtered.length + ' projects fully assessed';
        var owners = Array.from(managers.values()).sort(function (a, b) { return b.count - a.count || a.name.localeCompare(b.name); });
        var maximum = owners.length ? owners[0].count : 0;
        replaceList('manager-chart', owners, function (manager) {
            var row = node('div', 'sqr-pm-health-row');
            var label = node('div');
            label.append(node('span', '', manager.name), node('strong', '', manager.count + ' projects'));
            var bar = node('div', 'sqr-pm-manager-bar');
            bar.setAttribute('aria-hidden', 'true');
            var fill = node('span', 'sqr-pm-manager-fill');
            fill.style.width = (maximum ? manager.count / maximum * 100 : 0) + '%';
            bar.append(fill);
            row.append(label, bar);
            return row;
        }, 'No project managers in this view.');
        replaceList('status-queue', filtered.filter(function (project) { return project.needs_attention; }).slice(0, 5),
            function (project) { return attentionRow(project, false); }, 'No attention flags in this view.');
        var attention = filtered.filter(function (project) { return ['danger', 'warning'].includes(project.overall_rating); })
            .sort(function (a, b) { return (a.overall_rating === 'danger' ? 0 : 1) - (b.overall_rating === 'danger' ? 0 : 1); });
        replaceList('rating-queue', attention.slice(0, 5), function (project) { return attentionRow(project, true); }, 'No rated projects need attention in this view.');
        if (counts.attention > 5) el('status-queue').append(node('p', 'sqr-pm-row-meta', 'Showing 5 of ' + counts.attention));
        if (attention.length > 5) el('rating-queue').append(node('p', 'sqr-pm-row-meta', 'Showing 5 of ' + attention.length));
        replaceList('indicator-chart', indicatorLabels, function (indicator) {
            var row = node('div', 'sqr-pm-indicator-chart-row');
            row.append(node('span', 'sqr-pm-row-title', indicator[1]));
            var bar = node('div', 'sqr-pm-stacked-bar');
            var totals = {good: 0, warning: 0, danger: 0, unknown: 0};
            filtered.forEach(function (project) { totals[project.indicators[indicator[0]].state]++; });
            var description = [];
            Object.keys(totals).forEach(function (state) {
                var count = totals[state];
                description.push(Object.values(assessmentLabels[indicator[0]])[Object.keys(totals).indexOf(state)] + ': ' + count);
                if (!count) return;
                var segment = node('span', 'sqr-pm-rating--' + state, count);
                segment.style.width = count / filtered.length * 100 + '%';
                segment.setAttribute('aria-hidden', 'true');
                bar.append(segment);
            });
            bar.setAttribute('role', 'img');
            bar.setAttribute('aria-label', indicator[1] + ' — ' + description.join('; '));
            bar.title = description.join('; ');
            row.append(bar);
            return row;
        });
    }
    function renderTrackerTables(projects) {
        function titleCell(project) {
            var cell = node('th');
            cell.setAttribute('scope', 'row');
            cell.append(node('span', 'sqr-pm-table-project', project.project_name), node('small', 'sqr-pm-row-meta', project.reference_code + ' · ' + (project.customer_name || 'No customer')));
            return cell;
        }
        replaceList('status-rows', projects, function (project) {
            var row = node('tr');
            row.append(node('td', '', filtered.indexOf(project) + 1), titleCell(project), node('td', '', project.pm_name + (project.pm_fallback ? ' (reviewer)' : '')));
            row.append(node('td', 'sqr-pm-table-status sqr-pm-state--' + project.portfolio_status, portfolioLabels[project.portfolio_status]));
            row.append(node('td', '', formatDate(project.delivery_start_date) + ' – ' + formatDate(project.delivery_actual_finish_date)));
            var progress = node('td');
            progress.append(progressBlock(project));
            row.append(progress, node('td', 'sqr-pm-table-notes', project.updates_risks || '—'));
            return row;
        }, '');
        replaceList('indicator-rows', projects, function (project) {
            var row = node('tr');
            row.append(titleCell(project));
            indicatorLabels.forEach(function (indicator) {
                var value = project.indicators[indicator[0]];
                row.append(node('td', 'sqr-pm-indicator-cell sqr-pm-rating--' + value.state, value.label));
            });
            row.append(node('td', 'sqr-pm-indicator-cell sqr-pm-rating--' + project.overall_rating, project.overall_label),
                node('td', '', project.assessed_count + '/3'));
            return row;
        }, '');
    }
    function renderPage() {
        var pages = Math.max(1, Math.ceil(filtered.length / pageSize));
        pageNumber = Math.min(pageNumber, pages);
        var projects = filtered.slice((pageNumber - 1) * pageSize, pageNumber * pageSize);
        renderTrackerTables(projects);
        el('page').textContent = 'Page ' + pageNumber + ' of ' + pages + ' · ' + filtered.length + ' projects';
        el('prev').disabled = pageNumber <= 1;
        el('next').disabled = pageNumber >= pages;
    }
    function render() {
        renderFrame = 0;
        if (!snapshot || !visible()) return;
        filtered = core.filterRecords(snapshot.projects, filters, root.dataset.currentUserId);
        var counts = {total: filtered.length, attention: filtered.filter(core.needsAttention).length};
        renderFilters();
        renderPortfolio(counts);
        root.querySelectorAll('[data-pm-metric]').forEach(function (metric) { metric.textContent = counts[metric.dataset.pmMetric]; });
        el('content').hidden = false;
        el('empty').hidden = filtered.length > 0;
        el('empty').textContent = !snapshot.projects.length ? 'No SQR records are available yet.' : 'No matching projects. Clear filters to widen your view.';
        renderPage();
        if (!requestState) el('message').textContent = '';
    }
    function scheduleRender() {
        if (visible() && !renderFrame) renderFrame = requestAnimationFrame(render);
    }
    function message(state, text) {
        requestState = state;
        el('message').dataset.state = state;
        el('message').replaceChildren(node('span', '', text));
        if (state === 'error' || state === 'stale') {
            var retry = node('button', 'btn btn-outline-primary ms-2', 'Retry');
            retry.type = 'button';
            retry.dataset.pmRetry = 'true';
            el('message').append(retry);
        }
    }
    async function refresh() {
        if (!visible()) { dirty = true; return; }
        if (inFlight) return inFlight;
        var version = revision;
        var controller = new AbortController();
        var timeout = setTimeout(function () { controller.abort(); }, 20000);
        root.setAttribute('aria-busy', 'true');
        message('loading', snapshot ? 'Refreshing snapshot… Showing the previous snapshot until ready.' : 'Loading your project portfolio…');
        inFlight = (async function () {
            try {
                var response = await fetch(root.dataset.endpoint, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: {Accept: 'application/json'}});
                if (!response.ok || response.redirected) throw new Error('Snapshot unavailable');
                var data = await response.json();
                if (!data.ok || !Array.isArray(data.projects) || !data.as_of || !data.fetched_at) throw new Error('Invalid snapshot');
                // A successful Tracker mutation invalidates even a still-in-flight response.
                if (version !== revision) return;
                snapshot = data;
                dirty = false;
                if (visible()) {
                    message('', '');
                    scheduleRender();
                }
            } catch (error) {
                dirty = true;
                if (visible()) message(snapshot ? 'stale' : 'error', snapshot ? 'Refresh failed. Showing an older snapshot; figures may be out of date.' : 'Could not load the dashboard. Check your connection or sign-in, then retry.');
            } finally {
                clearTimeout(timeout);
                inFlight = null;
                root.setAttribute('aria-busy', 'false');
                if (version !== revision && visible()) refresh();
            }
        })();
        return inFlight;
    }
    el('search').addEventListener('input', function () {
        filters.search = el('search').value.trim();
        pageNumber = 1;
        clearTimeout(searchTimer);
        savePreferences();
        searchTimer = setTimeout(scheduleRender, 120);
    });
    root.addEventListener('click', function (event) {
        var button = event.target.closest('button');
        if (!button) return;
        if (button.dataset.pmRetry) refresh();
    });
    ['prev', 'next'].forEach(function (direction) {
        el(direction).addEventListener('click', function () {
            pageNumber += direction === 'next' ? 1 : -1;
            renderPage();
            el('status-tracker-title').setAttribute('tabindex', '-1');
            el('status-tracker-title').focus();
        });
    });
    document.addEventListener('sqr:performance-view', function (event) {
        if (event.detail.view !== 'performance') {
            if (renderFrame) cancelAnimationFrame(renderFrame);
            renderFrame = 0;
            return;
        }
        if (snapshot) scheduleRender();
        refresh();
    });
    document.addEventListener('sqr:performance-invalidated', function () {
        revision++;
        dirty = true;
        if (visible()) refresh();
    });
    document.addEventListener('visibilitychange', function () {
        // Recheck server-local date on return to this browser tab; no midnight timer or polling.
        if (!document.hidden && visible() && (dirty || snapshot)) refresh();
    });
    // Deferred scripts execute after the inline view switch restores localStorage.
    if (visible()) refresh();
})();