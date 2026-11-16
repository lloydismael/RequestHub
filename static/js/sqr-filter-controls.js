(function (root) {
    'use strict';
    var dateLabels = {delivery_start_date: 'Start', delivery_actual_finish_date: 'Finish',
        post_svc_warranty_end_date: 'Warranty end', managed_support_start_date: 'Maintenance start',
        managed_support_end_date: 'Maintenance end'};
    var sequence = 0;
    function mount(container, config) {
        var doc = container.ownerDocument, core = root.SqrFilterCore, applied = config.state, appliedSnapshot = JSON.stringify(applied);
        var openPanel = null, lastTrigger = null, id = 'sqr-filters-' + (++sequence), focusNodes = {}, draftInputs = {};
        function focusTarget(el, key) { el.setAttribute('data-filter-focus', key); focusNodes[key] = el; }
        function node(tag, text, className) {
            var el = doc.createElement(tag);
            if (text != null) el.textContent = text;
            if (className) el.className = className;
            return el;
        }
        function button(text, callback, parent) {
            var el = node('button', text, 'sqr-fc-button'); el.type = 'button';
            focusTarget(el, 'button:' + text);
            el.addEventListener('click', callback); parent.appendChild(el); return el;
        }
        function input(label, type, parent, value) {
            var wrapper = node('label', null, 'sqr-fc-field'), el = node('input');
            wrapper.appendChild(node('span', label)); el.type = type;
            el.value = Object.prototype.hasOwnProperty.call(draftInputs, 'input:' + label) ? draftInputs['input:' + label].value : value || '';
            focusTarget(el, 'input:' + label);
            el.setAttribute('aria-label', label); wrapper.appendChild(el); parent.appendChild(wrapper); return el;
        }
        function dimensions() { return config.getDimensions ? config.getDimensions() : config.dimensions; }
        function copy(state) { return JSON.parse(JSON.stringify(state)); }
        function apply(next) { applied = next; appliedSnapshot = JSON.stringify(next); config.onApply(next); render(); }
        function close() {
            if (openPanel) openPanel.hidden = true;
            if (lastTrigger) lastTrigger.setAttribute('aria-expanded', 'false');
            openPanel = null;
        }
        function panel(label, toolbar, panels, suffix) {
            var body = node('section', null, 'sqr-fc-panel'); body.hidden = true;
            body.setAttribute('aria-label', label); body.id = id + '-' + suffix;
            var trigger = button(label, function () {
                var wasOpen = openPanel === body; close();
                lastTrigger = trigger;
                if (!wasOpen) { body.hidden = false; openPanel = body; trigger.setAttribute('aria-expanded', 'true'); }
            }, toolbar);
            trigger.setAttribute('aria-controls', body.id); trigger.setAttribute('aria-expanded', 'false');
            panels.appendChild(body); return body;
        }
        function render(preserveDraft) {
            var active = doc.activeElement, focusKey = active && active.getAttribute('data-filter-focus');
            var openId = preserveDraft && openPanel && openPanel.id;
            var triggerKey = openId && lastTrigger.getAttribute('data-filter-focus');
            draftInputs = {};
            Object.keys(focusNodes).forEach(function (key) {
                var el = focusNodes[key];
                if (!openId || el.tagName !== 'INPUT') return;
                for (var ancestor = el; ancestor; ancestor = ancestor.parentNode) {
                    if (ancestor === openPanel) { draftInputs[key] = {value: el.value, checked: el.checked}; break; }
                }
            });
            if (!Object.values(focusNodes).some(function (el) { return el === active; })) focusKey = null;
            if (!openId) for (var ancestor = active; ancestor; ancestor = ancestor.parentNode) {
                if (ancestor === openPanel && lastTrigger) { focusKey = lastTrigger.getAttribute('data-filter-focus'); break; }
            }
            close(); lastTrigger = null; focusNodes = {}; container.replaceChildren(); container.className = 'sqr-filter-controls';
            var toolbar = node('div', null, 'sqr-fc-toolbar'), panels = node('div');
            var chips = node('div', null, 'sqr-fc-chips'), count = node('p', null, 'sqr-fc-count');
            count.setAttribute('role', 'status'); count.setAttribute('aria-live', 'polite');
            count.textContent = String(config.getResultCount ? config.getResultCount() : config.resultCount || 0) + ' matching records';
            container.append(toolbar, panels, chips, count);
            dimensions().forEach(function (dimension) {
                var body = panel(dimension.label, toolbar, panels, dimension.key), draft;
                var search = input('Search ' + dimension.label + ' options', 'search', body);
                var options = (dimension.options || []).slice(), chosen = (applied.selections[dimension.key] || []).slice();
                chosen.forEach(function (value) {
                    if (!options.some(function (option) { return option.value === value; })) options.push({value: value, label: 'Unavailable: ' + value, count: 0});
                });
                draft = chosen.slice();
                var optionPrefix = 'option:' + dimension.key + ':';
                var draftKeys = Object.keys(draftInputs).filter(function (key) { return key.indexOf(optionPrefix) === 0; });
                if (draftKeys.length) {
                    draft = draftKeys.filter(function (key) { return draftInputs[key].checked; }).map(function (key) { return key.slice(optionPrefix.length); });
                    draft.forEach(function (value) {
                        if (!options.some(function (option) { return option.value === value; })) options.push({value: value, label: 'Unavailable: ' + value, count: 0});
                    });
                }
                var list = node('div', null, 'sqr-fc-options'); body.appendChild(list);
                var rows = options.map(function (option) {
                    var label = node('label', null, 'sqr-fc-option'), checkbox = node('input'); checkbox.type = 'checkbox';
                    checkbox.value = option.value; checkbox.checked = draft.indexOf(option.value) >= 0;
                    focusTarget(checkbox, 'option:' + dimension.key + ':' + option.value);
                    var name = option.label + ' (' + (option.count || 0) + ')';
                    checkbox.setAttribute('aria-label', name);
                    checkbox.addEventListener('change', function () {
                        draft = draft.filter(function (value) { return value !== option.value; });
                        if (checkbox.checked) draft.push(option.value);
                    });
                    label.append(checkbox, node('span', name)); list.appendChild(label);
                    return {label: label, name: option.label.toLowerCase()};
                });
                function filterOptions() {
                    var word = search.value.trim().toLowerCase();
                    rows.forEach(function (row) { row.label.hidden = row.name.indexOf(word) < 0; });
                }
                search.addEventListener('input', filterOptions); filterOptions();
                button('Apply ' + dimension.label, function () {
                    var next = copy(applied); next.selections[dimension.key] = draft.slice(); apply(next);
                }, body);
                button('Clear ' + dimension.label, function () {
                    var next = copy(applied); next.selections[dimension.key] = []; apply(next);
                }, body);
                chosen.forEach(function (value) {
                    var option = options.find(function (item) { return item.value === value; });
                    button('Remove ' + dimension.label + ': ' + option.label, function () {
                        var next = copy(applied); next.selections[dimension.key] = next.selections[dimension.key].filter(function (item) { return item !== value; }); apply(next);
                    }, chips);
                });
            });
            ['mine', 'attention'].forEach(function (key) {
                var label = key === 'mine' ? 'My Assigned' : 'Needs attention';
                var toggle = button(label, function () { var next = copy(applied); next[key] = !next[key]; apply(next); }, toolbar);
                toggle.setAttribute('aria-pressed', String(applied[key]));
            });
            var ranges = panel('Price and dates', toolbar, panels, 'ranges'), fields = {};
            fields.priceMin = input('Minimum price', 'number', ranges, applied.priceMin);
            fields.priceMax = input('Maximum price', 'number', ranges, applied.priceMax);
            [fields.priceMin, fields.priceMax].forEach(function (el) { el.min = '0'; el.step = 'any'; });
            core.dateKeys.forEach(function (key) {
                var range = applied.dateRanges[key] || {};
                fields[key] = {from: input(dateLabels[key] + ' from', 'date', ranges, range.from), to: input(dateLabels[key] + ' to', 'date', ranges, range.to)};
            });
            var error = node('p', '', 'sqr-fc-error'); error.setAttribute('role', 'alert'); ranges.appendChild(error);
            button('Apply ranges', function () {
                var next = copy(applied); next.priceMin = fields.priceMin.value; next.priceMax = fields.priceMax.value;
                if ((next.priceMin && !core.validPrice(next.priceMin)) || (next.priceMax && !core.validPrice(next.priceMax)) ||
                    (next.priceMin && next.priceMax && Number(next.priceMin) > Number(next.priceMax))) {
                    error.textContent = 'Enter valid prices; the minimum cannot exceed the maximum.'; return;
                }
                var invalid = false;
                core.dateKeys.forEach(function (key) {
                    var range = {from: fields[key].from.value, to: fields[key].to.value};
                    if ((range.from && !core.validDate(range.from)) || (range.to && !core.validDate(range.to)) ||
                        (range.from && range.to && range.from > range.to)) invalid = true;
                    next.dateRanges[key] = range;
                });
                if (invalid) { error.textContent = 'Enter valid dates; From cannot be after To.'; return; }
                apply(next);
            }, ranges);
            button('Clear ranges', function () { var next = copy(applied); next.priceMin = next.priceMax = ''; next.dateRanges = {}; apply(next); }, ranges);
            if (applied.priceMin || applied.priceMax || Object.keys(applied.dateRanges).some(function (key) { return applied.dateRanges[key].from || applied.dateRanges[key].to; })) {
                button('Remove price and date ranges', function () { var next = copy(applied); next.priceMin = next.priceMax = ''; next.dateRanges = {}; apply(next); }, chips);
            }
            if (applied.search) button('Remove search: ' + applied.search, function () { var next = copy(applied); next.search = ''; apply(next); }, chips);
            button('Clear all', function () { apply(core.normalizeFilters({}, dimensions().map(function (dimension) { return dimension.key; })).state); }, toolbar);
            if (openId) {
                openPanel = Array.from(panels.children).find(function (body) { return body.id === openId; });
                lastTrigger = focusNodes[triggerKey];
                if (openPanel && lastTrigger) { openPanel.hidden = false; lastTrigger.setAttribute('aria-expanded', 'true'); }
            }
            draftInputs = {};
            if (focusKey) (focusNodes[focusKey] || focusNodes[triggerKey] || focusNodes['button:Clear all']).focus();
        }
        function keydown(event) {
            if (event.key !== 'Escape' || !openPanel) return;
            event.preventDefault(); close(); if (lastTrigger) lastTrigger.focus();
        }
        container.addEventListener('keydown', keydown); render();
        return {setState: function (state) {
            var snapshot = JSON.stringify(state), preserveDraft = snapshot === appliedSnapshot;
            applied = state; appliedSnapshot = snapshot; render(preserveDraft);
        }, destroy: function () {
            container.removeEventListener('keydown', keydown); container.replaceChildren(); close();
        }};
    }
    root.SqrFilterControls = {mount: mount};
})(typeof window === 'undefined' ? globalThis : window);
