/* Shared, text-only SQR/PPT predicates. Authorization remains server-side. */
(function () {
    'use strict';
    var SELECTED = ['status', 'proposal_status', 'overall_status', 'pm', 'approver', 'assigned_sse', 'group', 'stage',
        'schedule_performance', 'budget_effort_performance', 'quality_performance', 'revenue_source', 'delivery_health', 'portfolio', 'rating', 'scope', 'attention'];
    var FLAGS = ['linked', 'sse', 'billed', 'needs_attention', 'missing_assessment', 'overdue', 'due_soon'];
    var RANGES = ['date_from', 'date_to', 'amount_min', 'amount_max'];
    var PPT = ['portfolio', 'rating', 'scope', 'attention', 'needs_attention', 'missing_assessment'];
    var LABELS = {status: 'SQR Status', proposal_status: 'Proposal Status', overall_status: 'Delivery Status', pm: 'Project Manager',
        approver: 'Approver', assigned_sse: 'Assigned SSE', group: 'Group contains', stage: 'Stage', schedule_performance: 'Schedule Performance',
        budget_effort_performance: 'Budget/Effort Performance', quality_performance: 'Quality Performance', revenue_source: 'Billing Type',
        delivery_health: 'Legacy terminal evidence', portfolio: 'Portfolio Status', rating: 'Overall Performance', scope: 'PPT Scope', attention: 'PPT Attention'};
    var LEGACY = {on_track: 'on_track', at_risk: 'at_risk', off_track: 'delayed', not_set: 'not_recorded'};
    function unique(values) { return values.map(function (value) { return String(value).trim(); }).filter(function (value, index, list) { return value && list.indexOf(value) === index; }); }
    function read(search, config) {
        config = config || {defaultAssigned: 'all'};
        var state = {selected: {}, q: (search.get('q') || '').trim().slice(0, 200),
            assigned: search.has('assigned') ? search.get('assigned') : (config.defaultAssigned || 'all'), flags: {}, ranges: {}};
        SELECTED.forEach(function (key) { state.selected[key] = unique(search.getAll(key)); });
        state.selected.stage = state.selected.stage.filter(function (value) { return value !== 'all'; });
        state.selected.scope = state.selected.scope.filter(function (value) { return value !== 'all'; });
        if (!search.has('schedule_performance')) {
            var legacy = state.selected.delivery_health;
            state.selected.schedule_performance = unique(legacy.filter(function (value) { return LEGACY[value]; }).map(function (value) { return LEGACY[value]; }));
            state.selected.delivery_health = legacy.filter(function (value) { return !LEGACY[value]; });
        } else state.selected.delivery_health = [];
        FLAGS.forEach(function (key) { state.flags[key] = search.get(key) === '1'; });
        RANGES.forEach(function (key) { state.ranges[key] = search.get(key) || ''; });
        return state;
    }
    function toQuery(state) {
        var result = new URLSearchParams();
        SELECTED.forEach(function (key) { unique(state.selected[key] || []).forEach(function (value) { result.append(key, value); }); });
        result.set('assigned', state.assigned);
        if (state.q) result.set('q', state.q);
        FLAGS.forEach(function (key) { if (state.flags[key]) result.set(key, '1'); });
        RANGES.forEach(function (key) { if (state.ranges[key]) result.set(key, state.ranges[key]); });
        return result;
    }
    function clone(state) { return JSON.parse(JSON.stringify(state)); }
    function fromPage(defaultAssigned) {
        var search = new URLSearchParams(window.location ? window.location.search : '');
        if (SELECTED.concat(FLAGS, RANGES, ['q', 'assigned']).some(function (key) { return search.has(key); })) return read(search, {defaultAssigned: defaultAssigned});
        try {
            var saved = window.sessionStorage && window.sessionStorage.getItem('requestHub.sqr.filters.v1');
            if (saved) return read(toQuery(JSON.parse(saved)), {defaultAssigned: defaultAssigned});
            var previous = window.sessionStorage && window.sessionStorage.getItem('requestHub.sqr.prototype');
            if (previous) {
                var old = JSON.parse(previous);
                if (old.search) search.set('q', old.search);
                if (old.stage && old.stage !== 'all') search.set('stage', old.stage);
            }
        } catch (error) { search.set('stage', '__invalid_saved_filters__'); }
        return read(search, {defaultAssigned: defaultAssigned});
    }
    function write(state, config) {
        config = config || {};
        var params = new URLSearchParams(window.location.search);
        SELECTED.concat(FLAGS, RANGES, ['q', 'assigned']).forEach(function (key) { params.delete(key); });
        toQuery(state).forEach(function (value, key) { params.append(key, value); });
        if (config.resetPage !== false) params.set('page', '1');
        if (config.clearFocus !== false) params.delete('focus');
        window.history.replaceState(null, '', window.location.pathname + '?' + params.toString());
        try { window.sessionStorage.setItem('requestHub.sqr.filters.v1', JSON.stringify(state)); } catch (error) { /* storage unavailable */ }
        document.dispatchEvent(new CustomEvent('sqr:filters-changed', {detail: {state: clone(state)}}));
    }
    function element(tag, attrs, text) {
        var node = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (key) {
            var value = attrs[key];
            if (key === 'value') node.value = String(value);
            else if (key === 'checked') node.checked = !!value;
            else node.setAttribute(key, String(value));
        });
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function mount(root, config) {
        var state = clone(config.state), options = config.options || {}, count = null, live, searchTimer = 0;
        var isPpt = root.dataset.ppt === 'true';
        function change() { render(); config.onChange(clone(state)); }
        function labelFor(key, value) {
            var option = (options[key] || []).filter(function (item) { return item.value === value; })[0];
            return option ? option.label : value;
        }
        function chip(parent, label, remove) {
            var button = element('button', {type: 'button', class: 'sqr-filter-chip', 'aria-label': 'Remove ' + label}, label + ' ×');
            button.addEventListener('click', function () { remove(); change(); }); parent.append(button);
        }
        function input(parent, key, label, kind, value, callback) {
            var id = root.id + '-' + key;
            var wrap = element('div', {class: 'sqr-filter-input'}), title = element('label', {for: id}, label);
            var control = element('input', {id: id, type: kind, value: value || '', 'aria-label': label});
            control.addEventListener('change', function () { callback(control.value); change(); });
            wrap.append(title, control); parent.append(wrap); return control;
        }
        function check(parent, key, label, checked, callback) {
            var control = element('input', {id: root.id + '-' + key, type: 'checkbox', value: key, checked: checked});
            var title = element('label', {class: 'sqr-filter-check'}); title.append(control, document.createTextNode(label));
            control.addEventListener('change', function () { callback(control.checked); change(); }); parent.append(title);
        }
        function render() {
            var open = Array.prototype.slice.call(root.querySelectorAll('details[data-sqr-filter-key]')).filter(function (node) { return node.open; }).map(function (node) { return node.getAttribute('data-sqr-filter-key'); });
            var active = document.activeElement && root.contains(document.activeElement) ? document.activeElement.id : null;
            var moreOpen = Array.prototype.some.call(root.querySelectorAll('details'), function (node) { return node.getAttribute('class') === 'sqr-filter-more' && node.open; });
            root.replaceChildren();
            var toolbar = element('div', {class: 'sqr-filter-toolbar'});
            var search = input(toolbar, 'q', 'Search SQRs', 'search', state.q, function (value) { state.q = value.slice(0, 200); });
            search.setAttribute('maxlength', '200');
            search.addEventListener('input', function () {
                state.q = search.value.slice(0, 200);
                if (searchTimer) clearTimeout(searchTimer);
                searchTimer = setTimeout(function () { searchTimer = 0; change(); }, 180);
            });
            if (root.dataset.canMine !== 'false') check(toolbar, 'assigned', 'My Assigned SQR (reviewer)', state.assigned === 'mine', function (value) { state.assigned = value ? 'mine' : 'all'; });
            var clear = element('button', {id: root.id + '-clear-all', type: 'button', class: 'btn btn-outline-secondary'}, 'Clear all');
            clear.addEventListener('click', function () { state = read(new URLSearchParams('assigned=all')); change(); }); toolbar.append(clear);
            root.append(toolbar);
            var categories = element('div', {class: 'sqr-filter-categories'}), notices = [];
            SELECTED.forEach(function (key) {
                var values = state.selected[key] || [];
                if (PPT.indexOf(key) !== -1 && !isPpt) return;
                var available = options[key] || [];
                if (key === 'stage' && root.dataset.canMine === 'false') available = available.filter(function (item) { return item.value !== 'mine'; });
                if (!available.length || key === 'delivery_health') return;
                var details = element('details', {'data-sqr-filter-key': key, class: 'sqr-filter-category'});
                var summary = element('summary', {id: root.id + '-' + key + '-summary', 'aria-expanded': 'false'}, LABELS[key] + ' (' + values.length + ')');
                var box = element('fieldset', {class: 'sqr-filter-options'}); box.append(element('legend', {class: 'visually-hidden'}, LABELS[key]));
                available.forEach(function (option, index) {
                    var id = root.id + '-' + key + '-' + index;
                    var control = element('input', {id: id, type: 'checkbox', value: option.value, checked: values.indexOf(option.value) !== -1});
                    var label = element('label', {for: id, class: 'sqr-filter-check'}); label.append(control, document.createTextNode(option.label));
                    control.addEventListener('change', function () {
                        state.selected[key] = control.checked ? unique(values.concat([option.value])) : values.filter(function (value) { return value !== option.value; }); change();
                    }); box.append(label);
                });
                var categoryClear = element('button', {id: root.id + '-' + key + '-clear', type: 'button', class: 'sqr-filter-category-clear'}, 'Clear ' + LABELS[key]);
                categoryClear.addEventListener('click', function () { state.selected[key] = []; change(); }); box.append(categoryClear);
                details.addEventListener('toggle', function () {
                    if (!root.contains(details)) return;
                    summary.setAttribute('aria-expanded', details.open ? 'true' : 'false');
                    if (details.open) root.querySelectorAll('details[data-sqr-filter-key]').forEach(function (other) { if (other !== details) other.open = false; });
                });
                details.addEventListener('keydown', function (event) { if (event.key === 'Escape') { details.open = false; summary.setAttribute('aria-expanded', 'false'); summary.focus(); } });
                details.append(summary, box); details.open = open.indexOf(key) !== -1; summary.setAttribute('aria-expanded', details.open ? 'true' : 'false'); categories.append(details);
            }); root.append(categories);
            var more = element('details', {class: 'sqr-filter-more'});
            var moreSummary = element('summary', {id: root.id + '-more-summary', 'aria-expanded': moreOpen ? 'true' : 'false'}, 'More filters');
            more.open = moreOpen; more.append(moreSummary);
            more.addEventListener('toggle', function () { moreSummary.setAttribute('aria-expanded', more.open ? 'true' : 'false'); });
            more.addEventListener('keydown', function (event) { if (event.key === 'Escape') { more.open = false; moreSummary.setAttribute('aria-expanded', 'false'); moreSummary.focus(); } });
            var extra = element('div', {class: 'sqr-filter-extra'});
            [['linked', 'Linked request'], ['sse', 'Assigned SSE present'], ['billed', 'Billed'], ['needs_attention', 'PPT needs attention'], ['missing_assessment', 'PPT incomplete assessment']].forEach(function (entry) {
                if (PPT.indexOf(entry[0]) !== -1 && !isPpt) return;
                check(extra, entry[0], entry[1], state.flags[entry[0]], function (value) { state.flags[entry[0]] = value; });
            });
            [['date_from', 'Created from', 'date'], ['date_to', 'Created through', 'date'], ['amount_min', 'Minimum amount', 'number'], ['amount_max', 'Maximum amount', 'number']].forEach(function (entry) {
                input(extra, entry[0], entry[1], entry[2], state.ranges[entry[0]], function (value) { state.ranges[entry[0]] = value; });
            }); more.append(extra); root.append(more);
            var chips = element('div', {class: 'sqr-filter-chips', 'aria-label': 'Selected filters'});
            if (state.assigned === 'mine') chip(chips, 'My Assigned SQR (reviewer)', function () { state.assigned = 'all'; });
            else if (state.assigned !== 'all') notices.push('Unavailable assignment filter. Clear all to reset.');
            SELECTED.forEach(function (key) {
                (state.selected[key] || []).forEach(function (value) {
                    var inactive = PPT.indexOf(key) !== -1 && !isPpt;
                    chip(chips, (inactive ? 'PPT only: ' : '') + LABELS[key] + ': ' + labelFor(key, value), function () { state.selected[key] = state.selected[key].filter(function (item) { return item !== value; }); });
                    if (key === 'attention' && ['overdue', 'due_soon'].indexOf(value) !== -1) notices.push('Retired deadline preference: remove it or Clear all.');
                    else if (!inactive && key !== 'group' && key !== 'delivery_health' && (options[key] || []).every(function (item) { return item.value !== value; })) notices.push('Unavailable ' + LABELS[key] + ' option: ' + value + '. Remove it or Clear all.');
                });
            });
            if (state.q) chip(chips, 'Search: ' + state.q, function () { state.q = ''; });
            FLAGS.forEach(function (key) { if (state.flags[key]) { chip(chips, (PPT.indexOf(key) !== -1 && !isPpt ? 'PPT only: ' : '') + key.replace(/_/g, ' '), function () { state.flags[key] = false; }); if (key === 'overdue' || key === 'due_soon') notices.push('Retired deadline preference: remove it or Clear all.'); } });
            RANGES.forEach(function (key) { if (state.ranges[key]) chip(chips, key.replace(/_/g, ' ') + ': ' + state.ranges[key], function () { state.ranges[key] = ''; }); }); root.append(chips);
            if (notices.length) root.append(element('p', {class: 'sqr-filter-notice', role: 'alert'}, unique(notices).join(' ')));
            root.append(element('p', {class: 'sqr-filter-help'}, 'OR within a category; AND across categories. PM uses Assigned PM, then reviewer fallback. Group matches partial names.'));
            live = element('p', {'data-sqr-filter-count': '', class: 'sqr-filter-count', role: 'status', 'aria-live': 'polite'}, count === null ? 'Results not loaded' : count + ' matching SQRs'); root.append(live);
            if (active) { var focus = root.querySelector('[id="' + active + '"]'); if (focus) focus.focus(); }
        }
        function outside(event) { if (!root.contains(event.target)) root.querySelectorAll('details').forEach(function (node) { node.open = false; }); }
        document.addEventListener('click', outside); render();
        return {getState: function () { return clone(state); }, setState: function (value) { state = clone(value); render(); },
            setOptions: function (value) { options = value; render(); }, setCount: function (value) { count = value; live.textContent = value + ' matching SQRs'; },
            destroy: function () { if (searchTimer) clearTimeout(searchTimer); document.removeEventListener('click', outside); root.replaceChildren(); }};
    }
    window.RequestHubSqrFilters = {read: read, toQuery: toQuery, fromPage: fromPage, write: write, mount: mount};
})();
