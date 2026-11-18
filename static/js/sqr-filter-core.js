(function (root) {
    'use strict';
    var DATE_KEYS = ['delivery_start_date', 'delivery_actual_finish_date', 'post_svc_warranty_end_date',
        'managed_support_start_date', 'managed_support_end_date'];
    var ID_KEYS = ['pm_id', 'sse_id', 'linked_request_id'];
    var ASSESSMENT_CHOICES = {schedule_performance: ['on_track', 'at_risk', 'delayed', '__not_assessed__'],
        budget_effort_performance: ['within_budget', 'at_risk', 'over_budget', '__not_assessed__'],
        quality_performance: ['met', 'at_risk', 'unmet', '__not_assessed__']};
    var ASSESSMENT_KEYS = Object.keys(ASSESSMENT_CHOICES);
    function validDate(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
        var date = new Date(value + 'T00:00:00Z');
        return !isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
    }
    function validPrice(value) {
        return typeof value === 'string' && /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) && isFinite(Number(value));
    }
    function normalizeFilters(raw, keys) {
        var notices = [], state = {selections: {}, search: '', mine: false, attention: false,
            priceMin: '', priceMax: '', dateRanges: {}};
        function discard() { if (!notices.length) notices.push('Some saved filters were invalid or retired and have been cleared.'); }
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { if (raw != null) discard(); raw = {}; }
        var selections = raw.selections;
        if (!selections || typeof selections !== 'object' || Array.isArray(selections)) {
            if (selections != null) discard(); selections = {};
        }
        keys.forEach(function (key) {
            var values = selections[key];
            state.selections[key] = [];
            if (values == null) return;
            if (!Array.isArray(values)) { discard(); return; }
            values.forEach(function (value) {
                if (typeof value === 'number' && isFinite(value)) value = String(value);
                var valid = typeof value === 'string' && value.length > 0 && value.length <= 300;
                if (valid && ID_KEYS.indexOf(key) >= 0) valid = /^(?:[1-9]\d*|__unassigned__)$/.test(value);
                if (valid && ASSESSMENT_KEYS.indexOf(key) >= 0) valid = ASSESSMENT_CHOICES[key].indexOf(value) >= 0;
                if (!valid) { discard(); return; }
                if (state.selections[key].indexOf(value) < 0) state.selections[key].push(value);
            });
        });
        Object.keys(selections).forEach(function (key) { if (keys.indexOf(key) < 0) discard(); });
        if (typeof raw.search === 'string') state.search = raw.search.trim();
        else if (raw.search != null) discard();
        ['mine', 'attention'].forEach(function (key) {
            if (typeof raw[key] === 'boolean') state[key] = raw[key];
            else if (raw[key] != null) discard();
        });
        ['priceMin', 'priceMax'].forEach(function (key) {
            var value = raw[key];
            if (value == null || value === '') return;
            if (validPrice(value)) state[key] = value; else discard();
        });
        if (state.priceMin && state.priceMax && Number(state.priceMin) > Number(state.priceMax)) {
            state.priceMin = state.priceMax = ''; discard();
        }
        var ranges = raw.dateRanges;
        if (ranges != null && (typeof ranges !== 'object' || Array.isArray(ranges))) { discard(); ranges = null; }
        Object.keys(ranges || {}).forEach(function (key) {
            if (DATE_KEYS.indexOf(key) < 0) { discard(); return; }
            var rawRange = ranges[key], range = {from: '', to: ''};
            if (!rawRange || typeof rawRange !== 'object' || Array.isArray(rawRange)) { discard(); return; }
            ['from', 'to'].forEach(function (bound) {
                if (!rawRange[bound]) return;
                if (validDate(rawRange[bound])) range[bound] = rawRange[bound]; else discard();
            });
            if (range.from && range.to && range.from > range.to) { range.from = range.to = ''; discard(); }
            state.dateRanges[key] = range;
        });
        Object.keys(raw).forEach(function (key) { if (!Object.prototype.hasOwnProperty.call(state, key)) discard(); });
        return {state: state, notices: notices};
    }
    function fromQuery(params, keys, mode) {
        var raw = {selections: {}, search: params.get('search') || params.get('q') || '',
            mine: ['1', 'true'].indexOf(params.get('mine')) >= 0,
            attention: ['1', 'true'].indexOf(params.get('attention')) >= 0,
            priceMin: params.get('priceMin') || params.get('price_min') || '',
            priceMax: params.get('priceMax') || params.get('price_max') || '', dateRanges: {}};
        var aliases = {pm_id: 'manager'};
        if (mode === 'ppt') Object.assign(aliases, {overall_status: 'status', portfolio_status: 'portfolio', overall_rating: 'rating'});
        keys.forEach(function (key) {
            var name = params.has(key) ? key : aliases[key];
            raw.selections[key] = name ? params.getAll(name).filter(Boolean) : [];
        });
        if ((raw.selections.stage || []).indexOf('mine') >= 0) {
            raw.mine = true; raw.selections.stage = raw.selections.stage.filter(function (value) { return value !== 'mine'; });
        }
        if ((raw.selections.stage || []).indexOf('all') >= 0) raw.selections.stage = [];
        var migrationNotices = [];
        if (mode === 'ppt') {
            raw.selections.scope = (raw.selections.scope || []).filter(function (value) {
                if (value !== 'all' && value !== 'active') return true;
                if (!migrationNotices.length) migrationNotices.push('Legacy PPT scope filters were retired; use Stage and Portfolio Status to narrow the saved-assessment portfolio.');
                return false;
            });
            var attention = params.get('attention');
            if (attention && ['0', '1', 'false', 'true'].indexOf(attention) < 0) {
                raw.attention = true;
                migrationNotices.push('Legacy attention reason filters now use Needs attention; all attention reasons are included.');
            }
        }
        var retired = false;
        if (!params.has('schedule_performance') && params.has('health')) {
            raw.selections.schedule_performance = params.getAll('health').filter(function (value) {
                if (value === 'on_track' || value === 'at_risk') return true;
                retired = true; return false;
            });
        }
        DATE_KEYS.forEach(function (key) {
            var from = params.get(key + '_from') || '', to = params.get(key + '_to') || '';
            if (from || to) raw.dateRanges[key] = {from: from, to: to};
        });
        var result = normalizeFilters(raw, keys);
        result.notices = result.notices.concat(migrationNotices);
        if (retired) result.notices.push('Legacy Off Track or terminal Health filters were retired; select a recorded performance assessment.');
        return result;
    }
    function needsAttention(record) {
        return (record.attention || []).some(function (flag) { return flag.tone === 'danger' || flag.tone === 'warning'; }) ||
            record.status === 'submitted';
    }
    function isMine(record, viewer) {
        var viewerId = viewer && typeof viewer === 'object' ? viewer.pk : viewer;
        var assignedId = viewer && viewer.is_engineer ? record.sse_id : record.approver_id;
        return viewerId != null && String(viewerId) !== '' && assignedId != null &&
            String(assignedId) !== '' && String(assignedId) === String(viewerId);
    }
    function matchesRecord(record, filters, viewer) {
        var selections = filters.selections || {};
        if (!Object.keys(selections).every(function (key) {
            var chosen = selections[key];
            if (!chosen.length) return true;
            var value = record[key] == null ? '' : String(record[key]);
            if (!value && ID_KEYS.indexOf(key) >= 0) value = '__unassigned__';
            if (!value && ASSESSMENT_KEYS.indexOf(key) >= 0) value = '__not_assessed__';
            return chosen.indexOf(value) >= 0;
        })) return false;
        var search = String(record.search || record.search_text || '').toLowerCase();
        if (!(filters.search || '').toLowerCase().split(/\s+/).filter(Boolean).every(function (word) { return search.indexOf(word) >= 0; })) return false;
        if (filters.mine && !isMine(record, viewer)) return false;
        if (filters.attention && !needsAttention(record)) return false;
        if (filters.priceMin || filters.priceMax) {
            var price = String(record.total == null ? '' : record.total);
            if (!validPrice(price)) return false;
            if (filters.priceMin && Number(price) < Number(filters.priceMin)) return false;
            if (filters.priceMax && Number(price) > Number(filters.priceMax)) return false;
        }
        return Object.keys(filters.dateRanges || {}).every(function (key) {
            var range = filters.dateRanges[key];
            if (!range.from && !range.to) return true;
            var value = record[key];
            return validDate(value) && (!range.from || value >= range.from) && (!range.to || value <= range.to);
        });
    }
    root.SqrFilterCore = {normalizeFilters: normalizeFilters, fromQuery: fromQuery,
        matchesRecord: matchesRecord, filterRecords: function (records, filters, viewer) {
            return records.filter(function (record) { return matchesRecord(record, filters, viewer); });
        }, isMine: isMine, needsAttention: needsAttention, validDate: validDate, validPrice: validPrice, dateKeys: DATE_KEYS};
})(typeof window === 'undefined' ? globalThis : window);
