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
    ['scope', 'search', 'manager', 'health', 'status', 'attention', 'mine'].forEach(function (key) {
        controls[key] = el(key);
    });
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
    var healthOptions = [
        ['on_track', 'On Track'], ['at_risk', 'At Risk'], ['off_track', 'Off Track'],
        ['completed', 'Completed'], ['cancelled', 'Cancelled'], ['not_set', 'Not set']
    ];
    function visible() { return !root.hidden; }
    function formatDate(value) {
        return value ? dateFormat.format(new Date(value + 'T00:00:00Z')) : 'Not reported';
    }
    function deadline(project) {
        if (!project.target_date) return 'Target not reported';
        var suffix = '';
        if (project.overdue) suffix = ' · ' + Math.abs(project.days_to_target) + ' days overdue';
        else if (project.active_delivery && project.days_to_target === 0) suffix = ' · Due today';
        else if (project.active_delivery && project.days_to_target > 0) suffix = ' · In ' + project.days_to_target + ' days';
        return 'Target: ' + formatDate(project.target_date) + suffix;
    }
    function healthClass(key) {
        return healthOptions.some(function (option) { return option[0] === key; }) ? key : 'not_set';
    }
    function openButton(project) {
        var button = node('button', 'sqr-pm-open', 'View in Tracker');
        button.type = 'button';
        button.dataset.pmOpen = String(project.pk);
        button.setAttribute('aria-label', 'View ' + project.reference_code + ' in Tracker');
        return button;
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
    function projectCard(project, compact) {
        var card = node('article', 'sqr-pm-project');
        var head = node('div', 'sqr-pm-project-head');
        var identity = node('div');
        identity.append(node('h4', 'sqr-pm-project-title', project.project_name));
        identity.append(node('p', 'sqr-pm-row-meta', project.reference_code + ' · ' + project.customer_name));
        head.append(identity, node('span', 'sqr-pm-tag sqr-pm-tag--' + healthClass(project.health_key), project.health_label));
        card.append(head);
        card.append(node('p', 'sqr-pm-row-meta', project.pm_name + (project.pm_fallback ? ' · Reviewer fallback' : '') + ' · ' + project.status_label));
        if (project.reasons.length) {
            var alerts = node('ul', 'sqr-pm-alerts');
            alerts.setAttribute('aria-label', 'Attention reasons');
            project.reasons.forEach(function (reason) { alerts.append(node('li', 'sqr-pm-alert', reason.label)); });
            card.append(alerts);
        }
        card.append(progressBlock(project), node('p', 'sqr-pm-row-meta', deadline(project)));
        if (compact && project.updates_risks) {
            card.append(node('p', 'sqr-pm-row-meta', 'Updates / risks: ' + project.updates_risks.slice(0, 180) + (project.updates_risks.length > 180 ? '…' : '')));
        }
        if (!compact) {
            var details = node('details', 'sqr-pm-details');
            details.append(node('summary', '', 'Delivery details & notes'));
            var list = node('dl');
            [
                ['Assigned SSE', project.sse_name], ['Start', formatDate(project.start_date)],
                ['Target', formatDate(project.target_date)], ['Actual finish', formatDate(project.actual_finish_date)],
                ['Completion signed', formatDate(project.completion_signed_date)],
                ['Missing data', project.missing_data.join(', ') || 'None'],
                ['Inconsistencies', project.inconsistencies.join('; ') || 'None'],
                ['Updates / risks', project.updates_risks || 'No notes recorded']
            ].forEach(function (entry) { list.append(node('dt', '', entry[0]), node('dd', '', entry[1])); });
            details.append(list);
            card.append(details);
        }
        card.append(openButton(project));
        return card;
    }
    function replaceList(id, items, builder, emptyText) {
        var fragment = document.createDocumentFragment();
        items.forEach(function (item) { fragment.append(builder(item)); });
        if (!items.length) fragment.append(node('p', 'sqr-pm-empty', emptyText));
        el(id).replaceChildren(fragment);
    }
    function renderDirectory() {
        var pages = Math.max(1, Math.ceil(filtered.length / pageSize));
        pageNumber = Math.min(pageNumber, pages);
        replaceList('directory', filtered.slice((pageNumber - 1) * pageSize, pageNumber * pageSize), function (project) {
            return projectCard(project, false);
        }, 'No projects match this view. Try clearing filters or choosing All SQRs.');
        el('page').textContent = 'Page ' + pageNumber + ' of ' + pages + ' · ' + filtered.length + ' projects';
        el('prev').disabled = pageNumber <= 1;
        el('next').disabled = pageNumber >= pages;
    }
    function matchesReason(project, key) {
        if (!key) return true;
        if (key === 'attention') return project.needs_attention;
        var reasonKey = key === 'inconsistency' ? 'inconsistent_data' : key;
        return project.reasons.some(function (reason) { return reason.key === reasonKey; });
    }
    function render() {
        renderFrame = 0;
        if (!snapshot || !visible()) return;
        el('snapshot').textContent = 'Retrieved ' + new Date(snapshot.fetched_at).toLocaleString() + ' · Server date ' + formatDate(snapshot.as_of);
        var query = controls.search.value.trim().toLowerCase();
        var counts = {total: 0, attention: 0, overdue: 0, due_soon: 0, missing_data: 0};
        var healthCounts = {};
        var known = 0;
        var totalProgress = 0;
        filtered = snapshot.projects.filter(function (project) {
            var matches = (controls.scope.value !== 'active' || project.active_delivery)
                && (!controls.mine.checked || project.pm_id === root.dataset.currentUserId)
                && (!controls.manager.value || project.pm_id === controls.manager.value)
                && (!controls.health.value || project.health_key === controls.health.value)
                && (!controls.status.value || project.status_key === controls.status.value)
                && matchesReason(project, controls.attention.value)
                && (!query || project.search_text.toLowerCase().includes(query));
            if (!matches) return false;
            counts.total++;
            ['attention', 'overdue', 'due_soon'].forEach(function (key) {
                if (project[key === 'attention' ? 'needs_attention' : key]) counts[key]++;
            });
            if (project.missing_data.length) counts.missing_data++;
            healthCounts[project.health_key] = (healthCounts[project.health_key] || 0) + 1;
            if (project.progress !== null) { known++; totalProgress += project.progress; }
            return true;
        });
        counts.average = known ? (Math.round(totalProgress / known * 10) / 10) + '%' : 'N/A';
        root.querySelectorAll('[data-pm-metric]').forEach(function (metric) { metric.textContent = counts[metric.dataset.pmMetric]; });
        root.querySelectorAll('[data-pm-filter]').forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.pmFilter === controls.attention.value));
        });
        el('coverage').textContent = known + ' / ' + filtered.length + ' reported · Unweighted';
        el('count').textContent = filtered.length + ' of ' + snapshot.projects.length + ' SQRs · ' + (controls.scope.value === 'active' ? 'Active delivery' : 'All SQRs') + (controls.mine.checked ? ' · My assigned' : '');
        el('content').hidden = false;
        el('empty').hidden = filtered.length > 0;
        el('empty').textContent = !snapshot.projects.length ? 'No SQR records are available yet.' : 'No matching projects. Reset filters or choose All SQRs to widen your view.';
        var attention = filtered.filter(function (project) { return project.needs_attention; });
        el('queue-count').textContent = 'Showing ' + Math.min(8, attention.length) + ' of ' + attention.length + ' · Ordered by urgency';
        replaceList('queue', attention.slice(0, 8), function (project) { return projectCard(project, true); }, 'No attention flags in this view.');
        var options = healthOptions.slice();
        Object.keys(healthCounts).forEach(function (key) {
            if (!options.some(function (option) { return option[0] === key; })) options.push([key, key]);
        });
        replaceList('health-chart', options, function (option) {
            var row = node('div', 'sqr-pm-health-row');
            var label = node('div');
            var count = healthCounts[option[0]] || 0;
            label.append(node('span', '', option[1]), node('strong', '', count + ' / ' + filtered.length));
            var bar = node('div', 'sqr-pm-bar');
            bar.setAttribute('aria-hidden', 'true');
            var fill = node('div', 'sqr-pm-bar-fill sqr-pm-tag--' + healthClass(option[0]), '');
            fill.style.width = (filtered.length ? count / filtered.length * 100 : 0) + '%';
            bar.append(fill);
            row.append(label, bar);
            return row;
        });
        el('progress-count').textContent = 'First ' + Math.min(12, filtered.length) + ' of ' + filtered.length + ' · Attention, then target date';
        replaceList('progress-chart', filtered.slice(0, 12), function (project) {
            var row = node('div', 'sqr-pm-row');
            row.append(node('p', 'sqr-pm-row-title', project.project_name), node('p', 'sqr-pm-row-meta', project.reference_code), progressBlock(project));
            return row;
        }, 'No completion data in this view.');
        var upcoming = filtered.filter(function (project) {
            return project.active_delivery && project.days_to_target !== null && project.days_to_target >= 0 && project.days_to_target <= 30;
        }).sort(function (a, b) { return a.days_to_target - b.days_to_target || a.pk - b.pk; });
        el('timeline-count').textContent = 'Showing ' + Math.min(12, upcoming.length) + ' of ' + upcoming.length + ' upcoming targets';
        replaceList('timeline', upcoming.slice(0, 12), function (project) {
            var item = node('div', 'sqr-pm-timeline-item');
            item.append(node('p', 'sqr-pm-date', formatDate(project.target_date)), node('p', 'sqr-pm-row-title', project.project_name), node('p', 'sqr-pm-row-meta', project.reference_code + ' · ' + project.pm_name), progressBlock(project));
            return item;
        }, 'No open targets within the next 30 days in this view.');
        el('timeline').prepend(node('p', 'sqr-pm-today', 'Today · ' + formatDate(snapshot.as_of)));
        renderDirectory();
        if (!requestState) el('message').textContent = filtered.length + ' matching projects. All metrics reflect this filtered view.';
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
        var timeout = setTimeout(function () { controller.abort(); }, 20000);
        root.setAttribute('aria-busy', 'true');
        el('refresh').disabled = true;
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
                    updateManagers();
                    el('snapshot').textContent = 'Retrieved ' + new Date(data.fetched_at).toLocaleString() + ' · Server date ' + formatDate(data.as_of);
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
                el('refresh').disabled = false;
                if (version !== revision && visible()) refresh();
            }
        })();
        return inFlight;
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
        Object.keys(controls).forEach(function (key) { if (key !== 'mine') controls[key].value = key === 'scope' ? 'active' : ''; });
        controls.mine.checked = root.dataset.defaultMine === '1';
        pageNumber = 1;
        scheduleRender();
    });
    el('refresh').addEventListener('click', refresh);
    root.addEventListener('click', function (event) {
        var button = event.target.closest('button');
        if (!button) return;
        if (button.hasAttribute('data-pm-filter')) {
            var reason = button.dataset.pmFilter;
            controls.attention.value = reason === controls.attention.value ? '' : reason;
            pageNumber = 1;
            scheduleRender();
            if (!button.classList.contains('sqr-pm-kpi')) el('directory-title').focus();
        }
        if (button.dataset.pmRetry) refresh();
        if (button.dataset.pmOpen && (!window.focusSqrTrackerProject || !window.focusSqrTrackerProject(button.dataset.pmOpen))) {
            message('stale', 'This project is not in the loaded Tracker. Reload the page to update Tracker records.');
        }
    });
    ['prev', 'next'].forEach(function (direction) {
        el(direction).addEventListener('click', function () {
            pageNumber += direction === 'next' ? 1 : -1;
            renderDirectory();
            el('directory-title').focus();
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