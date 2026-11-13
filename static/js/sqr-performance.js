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
    var controls = {};
    ['scope', 'search', 'manager', 'health', 'status', 'portfolio', 'rating', 'attention', 'mine'].forEach(function (key) {
        controls[key] = el(key);
    });
    var sharedFilters = window.RequestHubSqrFilters;
    var filterState = sharedFilters ? sharedFilters.fromPage(root.dataset.defaultMine === '1' ? 'mine' : 'all') : null;
    var filterWidget = null;
    if (sharedFilters) {
        var filterRoot = document.getElementById('sqr-ppt-filters');
        var filterOptionsNode = filterRoot && document.getElementById(filterRoot.dataset.optionsId);
        var initialOptions = {};
        try { initialOptions = JSON.parse(filterOptionsNode ? filterOptionsNode.textContent : '{}') || {}; } catch (error) { /* invalid selections retain a notice */ }
        if (filterRoot) filterWidget = sharedFilters.mount(filterRoot, {state: filterState, options: initialOptions,
            onChange: function (next) { sharedFilters.write(next); }});
    }
    var snapshot = null;
    var filtered = [];
    var pageNumber = 1;
    var pageSize = 24;
    var inFlight = null;
    var activeController = null;
    var revision = 0;
    var dirty = true;
    var renderFrame = 0;
    var searchTimer = 0;
    var requestState = '';
    var dateFormat = new Intl.DateTimeFormat(undefined, {year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'});
    var portfolioLabels = {closed: 'Closed', ongoing: 'Ongoing', pipeline: 'Pipeline', removed: 'Removed'};
    var ratingLabels = {good: 'Green', warning: 'Amber', danger: 'Red', unknown: 'N/R'};
    var indicatorLabels = [
        ['schedule', 'Schedule Performance', {good: 'On Track', warning: 'At Risk', danger: 'Delayed', unknown: 'N/R'}],
        ['budget_effort', 'Budget/Effort Performance', {good: 'Within Budget', warning: 'At Risk', danger: 'Over Budget', unknown: 'N/R'}],
        ['quality', 'Quality Performance', {good: 'Met', warning: 'At Risk', danger: 'Unmet', unknown: 'N/R'}]
    ];
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
            performance ? indicatorLabels.map(function (entry) { return entry[1] + ': ' + project.indicators[entry[0]].label; }).join(' · ')
            : project.reasons.map(function (reason) { return reason.label; }).join(' · ')
        );
        identity.append(node('p', 'sqr-pm-row-meta', detail));
        var badge = node('span', 'sqr-pm-tag ' + (performance ? 'sqr-pm-rating--' + rating : 'sqr-pm-state--' + project.portfolio_status),
            performance ? ratingLabels[rating] : portfolioLabels[project.portfolio_status] + (project.progress === null ? '' : ' · ' + project.progress + '%'));
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
        counts.missing_assessment = filtered.filter(function (project) { return project.missing_assessment; }).length;
        counts.closed_percent = filtered.length ? Math.round(statusCounts.closed / filtered.length * 100) + '%' : 'N/A';
        el('as-of').textContent = 'AS OF ' + formatDate(snapshot.as_of).toUpperCase();
        el('closed-coverage').textContent = statusCounts.closed + ' of ' + filtered.length + ' projects closed';
        el('rated-coverage').textContent = 'Of ' + (filtered.length - ratingCounts.unknown) + ' rated projects';
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
                description.push(indicator[2][state] + ': ' + count);
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
            cell.append(node('span', 'sqr-pm-table-project', project.project_name), node('small', 'sqr-pm-row-meta',
                (project.customer_name ? project.customer_name + ' · ' : '') + project.reference_code));
            return cell;
        }
        replaceList('status-rows', projects, function (project) {
            var row = node('tr');
            row.append(node('td', '', filtered.indexOf(project) + 1), titleCell(project), node('td', '', project.pm_name + (project.pm_fallback ? ' (reviewer)' : '')));
            var status = node('td', 'sqr-pm-table-status');
            status.append(node('span', '', project.status_label), node('small', 'sqr-pm-row-meta', 'Portfolio: ' + portfolioLabels[project.portfolio_status]));
            row.append(status);
            row.append(node('td', '', formatDate(project.start_date) + ' – ' + formatDate(project.actual_finish_date)));
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
            row.append(node('td', 'sqr-pm-indicator-cell sqr-pm-rating--' + project.overall_rating, ratingLabels[project.overall_rating]));
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
    function matchesReason(project, key) {
        if (!key) return true;
        if (key === 'attention') return project.needs_attention;
        if (key === 'missing_assessment') return project.missing_assessment;
        if (key === 'performance_risk') return indicatorLabels.some(function (entry) { return ['warning', 'danger'].includes(project.indicators[entry[0]].state); });
        var reasonKey = key === 'inconsistency' ? 'inconsistent_data' : key;
        return project.reasons.some(function (reason) { return reason.key === reasonKey; });
    }
    function render() {
        renderFrame = 0;
        if (!snapshot || !visible()) return;
        var query = controls.search.value.trim().toLowerCase();
        var counts = {total: 0, attention: 0};
        filtered = sharedFilters ? snapshot.projects.slice() : snapshot.projects.filter(function (project) {
            var matches = (controls.scope.value !== 'active' || project.active_delivery)
                && (!controls.mine.checked || project.approver_id === root.dataset.currentUserId)
                && (!controls.manager.value || project.pm_id === controls.manager.value)
                && (!controls.health.value || (controls.health.value === 'not_recorded' ? !project.indicators.schedule.value : project.indicators.schedule.value === controls.health.value))
                && (!controls.status.value || project.status_key === controls.status.value)
                && (!controls.portfolio.value || project.portfolio_status === controls.portfolio.value)
                && (!controls.rating.value || project.overall_rating === controls.rating.value)
                && matchesReason(project, controls.attention.value)
                && (!query || project.search_text.toLowerCase().includes(query));
            if (!matches) return false;
            counts.total++;
            if (project.needs_attention) counts.attention++;
            return true;
        });
        if (sharedFilters) { counts.total = filtered.length; counts.attention = filtered.filter(function (project) { return project.needs_attention; }).length; }
        if (filterWidget) filterWidget.setCount(filtered.length);
        renderPortfolio(counts);
        root.querySelectorAll('[data-pm-metric]').forEach(function (metric) { metric.textContent = counts[metric.dataset.pmMetric]; });
        el('content').hidden = false;
        el('empty').hidden = filtered.length > 0;
        el('empty').textContent = !snapshot.projects.length ? 'No SQR records are available yet.' : 'No matching projects. Reset filters or choose All SQRs to widen your view.';
        renderPage();
        if (!requestState) el('message').textContent = '';
    }
    function scheduleRender() {
        if (visible() && !renderFrame) renderFrame = requestAnimationFrame(render);
    }
    function updateManagers() {
        var selected = controls.manager.value;
        var managers = new Map();
        snapshot.projects.forEach(function (project) { if (project.pm_id) managers.set(project.pm_id, project.pm_name); });
        controls.manager.replaceChildren(new Option('All managers', ''));
        Array.from(managers).sort(function (a, b) { return a[1].localeCompare(b[1]); }).forEach(function (entry) {
            controls.manager.add(new Option(entry[1], entry[0]));
        });
        if (selected && !managers.has(selected)) controls.manager.add(new Option('Previously selected manager (no projects)', selected));
        controls.manager.value = selected;
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
        activeController = controller;
        var timeout = setTimeout(function () { controller.abort(); }, 20000);
        root.setAttribute('aria-busy', 'true');
        message('loading', snapshot ? 'Refreshing snapshot… Showing the previous snapshot until ready.' : 'Loading your project portfolio…');
        inFlight = (async function () {
            try {
                var url = root.dataset.endpoint;
                if (sharedFilters) {
                    var params = sharedFilters.toQuery(filterState);
                    var focus = new URLSearchParams(window.location.search).get('focus');
                    if (focus) params.set('focus', focus);
                    url += '?' + params.toString();
                }
                var response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: {Accept: 'application/json'}});
                if (!response.ok || response.redirected) throw new Error('Snapshot unavailable');
                var data = await response.json();
                if (!data.ok || !Array.isArray(data.projects) || !data.as_of || !data.fetched_at) throw new Error('Invalid snapshot');
                // A successful Tracker mutation invalidates even a still-in-flight response.
                if (version !== revision) return;
                snapshot = data;
                dirty = false;
                if (visible()) {
                    updateManagers();
                    if (filterWidget && data.filter_options) filterWidget.setOptions(data.filter_options);
                    message('', '');
                    scheduleRender();
                }
            } catch (error) {
                if (activeController === controller) {
                    dirty = true;
                    if (visible()) message(snapshot ? 'stale' : 'error', snapshot ? 'Refresh failed. Showing an older snapshot; figures may be out of date.' : 'Could not load the dashboard. Check your connection or sign-in, then retry.');
                }
            } finally {
                clearTimeout(timeout);
                if (activeController === controller) {
                    inFlight = null;
                    activeController = null;
                    root.setAttribute('aria-busy', 'false');
                }
            }
        })();
        return inFlight;
    }
    function invalidateSnapshot() {
        revision++;
        dirty = true;
        if (activeController) activeController.abort();
        activeController = null;
        inFlight = null;
        if (visible()) refresh();
    }
    Object.keys(controls).forEach(function (key) {
        controls[key].addEventListener(key === 'search' ? 'input' : 'change', function () {
            pageNumber = 1;
            clearTimeout(searchTimer);
            if (key === 'search') searchTimer = setTimeout(scheduleRender, 120);
            else scheduleRender();
        });
    });
    el('reset').addEventListener('click', function () {
        Object.keys(controls).forEach(function (key) { if (key !== 'mine') controls[key].value = key === 'scope' ? 'all' : ''; });
        controls.mine.checked = root.dataset.defaultMine === '1';
        pageNumber = 1;
        scheduleRender();
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
        if (snapshot) { updateManagers(); scheduleRender(); }
        refresh();
    });
    document.addEventListener('sqr:performance-invalidated', invalidateSnapshot);
    document.addEventListener('sqr:filters-changed', function (event) {
        if (!sharedFilters) return;
        filterState = event.detail.state;
        if (filterWidget) filterWidget.setState(filterState);
        pageNumber = 1;
        invalidateSnapshot();
    });
    document.addEventListener('visibilitychange', function () {
        // Recheck server-local date on return to this browser tab; no midnight timer or polling.
        if (!document.hidden && visible() && (dirty || snapshot)) refresh();
    });
    // Deferred scripts execute after the inline view switch restores localStorage.
    if (visible()) refresh();
})();