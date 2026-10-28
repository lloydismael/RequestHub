/* SQR Tracker Prototype: pipeline list + record editor.
 * Reads /sqr/prototype-data/ and writes through the existing inline-update, undo and history endpoints,
 * so validation, permissions, locks and audit history stay authoritative on the server. */
(function () {
    'use strict';

    var root = document.getElementById('sqr-proto');
    if (!root || root.dataset.ready) return;
    root.dataset.ready = 'true';

    var ENDPOINT = root.dataset.endpoint;
    var CSRF = root.dataset.csrf;
    var STORE_KEY = 'requestHub.sqr.prototype';
    var VIEW_KEY = 'requestHub.sqr.tableView';
    var PAGE_SIZE = 40;
    var STALE_MS = 60000;

    var BADGES = {
        status: {submitted: 'warning', for_revision: 'danger', reviewed: 'success', on_hold: 'muted'},
        proposal_status: {submitted_pending: 'primary', negotiation_review: 'info', closed_won: 'success', closed_lost: 'danger', closed_canceled: 'muted'},
        delivery_health: {on_track: 'success', off_track: 'warning', at_risk: 'danger', completed: 'primary', cancelled: 'muted'},
        overall_status: {planning: 'info', in_progress: 'primary', on_hold: 'muted', completed: 'success', cancelled: 'danger'},
        revenue_status: {billed: 'success', not_yet_billed: 'warning'}
    };
    var STAGE_ICONS = {done: 'bi-check-circle-fill', current: 'bi-record-circle-fill', todo: 'bi-circle', closed: 'bi-slash-circle-fill'};
    var STAGE_NAMES = {opportunity: 'Opportunity', delivery: 'Delivery', billing: 'Billing', closed: 'Closed'};

    var state = {
        loaded: false, loadedAt: 0, viewer: null, schema: null, options: {}, records: [], byPk: {},
        specs: {}, sections: {}, labels: {}, selectedPk: null, tab: 'summary', shown: PAGE_SIZE,
        dirtyClassic: false, history: {},
        filters: {stage: 'all', search: ''}
    };
    var inFlight = null;
    var toastTimer = 0;
    var pendingUndo = null;

    function $(id) { return document.getElementById('sqr-proto-' + id); }
    var el = {
        pipeline: $('pipeline'), body: $('body'), items: $('items'), more: $('more'),
        editor: $('editor'), search: $('search'),
        toast: $('toast'), toastText: $('toast-text'), toastUndo: $('toast-undo'), toastClose: $('toast-close'),
        approve: $('approve'), revise: $('revise'), reviseNotes: $('revise-notes'), reviseError: $('revise-error')
    };

    /* ── helpers ─────────────────────────────────────────────────── */
    function h(tag, props, children) {
        var node = document.createElement(tag);
        Object.keys(props || {}).forEach(function (key) {
            var value = props[key];
            if (value === null || value === undefined || value === false) return;
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key === 'dataset') Object.keys(value).forEach(function (name) { if (value[name] !== null && value[name] !== undefined) node.dataset[name] = value[name]; });
            else if (key === 'on') Object.keys(value).forEach(function (name) { node.addEventListener(name, value[name]); });
            else if (key === 'value') node.value = value;
            else node.setAttribute(key, value === true ? '' : value);
        });
        [].concat(children || []).forEach(function (child) {
            if (child === null || child === undefined || child === false) return;
            node.append(child.nodeType ? child : document.createTextNode(String(child)));
        });
        return node;
    }
    function icon(name, extra) { return h('i', {class: 'bi ' + name + (extra ? ' ' + extra : ''), 'aria-hidden': 'true'}); }

    var dateFmt = new Intl.DateTimeFormat(undefined, {year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'});
    function fmtDate(iso) { return iso ? dateFmt.format(new Date(iso + 'T00:00:00Z')) : ''; }
    function peso(raw) {
        if (raw === '' || raw === null || raw === undefined) return '';
        var n = parseFloat(raw);
        return isNaN(n) ? String(raw) : '\u20b1' + n.toLocaleString('en-PH', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }
    function compactPeso(n) {
        if (!n) return '\u20b10';
        if (n >= 1e6) return '\u20b1' + (n / 1e6).toFixed(2) + 'M';
        if (n >= 1e3) return '\u20b1' + (n / 1e3).toFixed(1) + 'K';
        return '\u20b1' + n.toFixed(0);
    }
    function choiceLabel(key, value) {
        var map = state.labels[key];
        return map && map[value] !== undefined ? map[value] : value;
    }
    function badge(key, value, text) {
        if (!value) return null;
        return h('span', {class: 'sqr-proto__badge', dataset: {tone: (BADGES[key] || {})[value] || 'muted'}, text: text || choiceLabel(key, value)});
    }
    function api(url, options) {
        var init = Object.assign({credentials: 'same-origin', headers: {Accept: 'application/json'}}, options || {});
        return fetch(url, init).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                return {ok: response.ok && data.ok !== false, status: response.status, data: data};
            });
        });
    }
    function post(url, body) {
        return api(url, {
            method: 'POST', body: body === undefined ? undefined : JSON.stringify(body),
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': CSRF, Accept: 'application/json'}
        });
    }
    function record(pk) { return state.byPk[pk] || null; }
    function savePrefs() {
        try { sessionStorage.setItem(STORE_KEY, JSON.stringify(state.filters)); } catch (error) { /* storage unavailable */ }
    }
    function loadPrefs() {
        try {
            var saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null');
            if (saved) { Object.keys(state.filters).forEach(function (key) { if (key in saved) state.filters[key] = saved[key]; }); return true; }
        } catch (error) { /* ignore corrupt prefs */ }
        return false;
    }

    /* ── toast ───────────────────────────────────────────────────── */
    function hideToast() {
        el.toast.hidden = true;
        pendingUndo = null;
        clearTimeout(toastTimer);
    }
    function showToast(text, options) {
        options = options || {};
        el.toastText.textContent = text;
        el.toast.dataset.tone = options.tone || '';
        pendingUndo = options.undo || null;
        el.toastUndo.hidden = !pendingUndo;
        el.toastUndo.disabled = false;
        el.toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(hideToast, pendingUndo ? 10000 : 5000);
    }
    el.toastClose.addEventListener('click', hideToast);
    el.toastUndo.addEventListener('click', function () {
        if (!pendingUndo) return;
        var undo = pendingUndo;
        el.toastUndo.disabled = true;
        post(undo.url).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Unable to undo this change.');
            markClassicStale();
            delete state.history[undo.pk];
            return refreshRecord(undo.pk, res.data).then(function () { showToast('Change undone.'); });
        }).catch(function (error) { showToast(error.message, {tone: 'error'}); });
    });

    /* ── filtering / sorting ─────────────────────────────────────── */
    function needsAction(rec) {
        if (rec.status === 'on_hold') return false;
        return rec.attention.some(function (flag) { return flag.tone === 'danger'; }) ||
            (state.viewer.is_pm && rec.status === 'submitted');
    }
    function baseMatches(rec) {
        var f = state.filters;
        if (f.search) {
            var terms = f.search.toLowerCase().split(/\s+/).filter(Boolean);
            for (var i = 0; i < terms.length; i += 1) if (rec.search.indexOf(terms[i]) === -1) return false;
        }
        return true;
    }
    function isMine(rec) { return !!state.viewer && rec.approver_id === state.viewer.pk; }
    function stageMatches(rec) {
        var stage = state.filters.stage;
        if (stage === 'all') return true;
        if (stage === 'mine') return state.viewer.is_pm ? isMine(rec) : true;
        if (stage === 'attention') return needsAction(rec);
        if (stage === 'on_hold') return rec.status === 'on_hold';
        return rec.stage === stage;
    }
    function attentionScore(rec) {
        return rec.attention.reduce(function (sum, flag) { return sum + (flag.tone === 'danger' ? 3 : flag.tone === 'warning' ? 2 : 0); }, 0);
    }
    function newestFirst(a, b) { return a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : b.pk - a.pk; }
    function needsAttentionFirst(a, b) { return attentionScore(b) - attentionScore(a) || newestFirst(a, b); }
    function visibleRecords() {
        return state.records.filter(function (rec) { return baseMatches(rec) && stageMatches(rec); }).sort(needsAttentionFirst);
    }

    /* ── pipeline strip ──────────────────────────────────────────── */
    var STAGE_TONES = {all: 'secondary', mine: 'primary', attention: 'danger', opportunity: 'primary', delivery: 'success', billing: 'warning', closed: 'secondary'};
    function renderPipeline() {
        var pool = state.records.filter(baseMatches);
        var defs = [{key: 'all', label: 'All SQRs', test: function () { return true; }}];
        if (state.viewer.is_pm) defs.push({key: 'mine', label: 'My Assigned SQR', icon: 'bi-person-check-fill', test: isMine});
        defs.push({key: 'attention', label: 'Needs action', test: needsAction});
        defs.push({key: 'on_hold', label: 'On Hold', test: function (rec) { return rec.status === 'on_hold'; }});
        state.schema.stages.forEach(function (stage) {
            defs.push({key: stage[0], label: stage[1], test: function (rec) { return rec.stage === stage[0]; }});
        });
        el.pipeline.replaceChildren.apply(el.pipeline, defs.map(function (def) {
            var matching = pool.filter(def.test);
            var total = matching.reduce(function (sum, rec) { return sum + (parseFloat(rec.total) || 0); }, 0);
            var tone = STAGE_TONES[def.key] || 'secondary';
            return h('button', {
                type: 'button', class: 'badge bg-' + tone + '-subtle text-' + tone + ' border border-' + tone + '-subtle sqr-badge-filter',
                dataset: {stage: def.key}, title: compactPeso(total),
                'aria-pressed': state.filters.stage === def.key ? 'true' : 'false',
                on: {click: function () {
                    state.filters.stage = state.filters.stage === def.key ? 'all' : def.key;
                    state.shown = PAGE_SIZE; savePrefs(); renderAll();
                }}
            }, [def.icon ? icon(def.icon, 'me-1') : null, def.label + ': ' + matching.length]);
        }));
    }

    /* ── list ────────────────────────────────────────────────────── */
    function cardFor(rec) {
        var flags = rec.attention.filter(function (flag) { return flag.key !== 'revision' && flag.key !== 'awaiting'; });
        var meta = [badge('status', rec.status), badge('proposal_status', rec.proposal_status)];
        if (flags[0]) meta.push(h('span', {class: 'sqr-proto__badge', dataset: {tone: flags[0].tone}, text: flags[0].label}));
        var showBar = rec.stage === 'delivery' && rec.progress !== null && rec.progress !== undefined;
        return h('button', {
            type: 'button', class: 'sqr-proto__card',
            dataset: {pk: rec.pk, stage: rec.stage, dim: rec.stage === 'closed' ? 'true' : null},
            'aria-current': state.selectedPk === rec.pk ? 'true' : 'false',
            on: {click: function () { select(rec.pk); }}
        }, [
            h('span', {class: 'sqr-proto__card-top'}, [
                h('span', {class: 'sqr-proto__card-ref', text: rec.ref + ' \u00b7 ' + STAGE_NAMES[rec.stage]}),
                h('span', {class: 'sqr-proto__card-value', text: rec.total ? peso(rec.total) : '\u2013'})
            ]),
            h('span', {class: 'sqr-proto__card-title', text: rec.account}),
            h('span', {class: 'sqr-proto__card-sub', text: rec.service}),
            h('span', {class: 'sqr-proto__card-meta'}, meta),
            showBar ? h('span', {class: 'sqr-proto__bar', role: 'progressbar', 'aria-label': 'Delivery progress', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': rec.progress},
                h('span', {style: 'width:' + Math.max(0, Math.min(100, rec.progress)) + '%'})) : null
        ]);
    }
    function renderList() {
        var activePk = el.items.contains(document.activeElement) && document.activeElement.dataset ? document.activeElement.dataset.pk : null;
        var rows = visibleRecords();
        var slice = rows.slice(0, state.shown);
        var children = slice.map(function (rec) { return h('li', null, cardFor(rec)); });
        if (!rows.length) children.push(h('li', {class: 'sqr-proto__count', text: state.records.length ? 'No matches.' : 'No SQRs yet.'}));
        el.items.replaceChildren.apply(el.items, children);
        el.more.hidden = rows.length <= slice.length;
        el.more.textContent = 'Show ' + Math.min(PAGE_SIZE, rows.length - slice.length) + ' more';
        if (activePk) {
            var again = el.items.querySelector('.sqr-proto__card[data-pk="' + activePk + '"]');
            if (again) again.focus({preventScroll: true});
        }
    }
    function renderAll() {
        if (!state.loaded) return;
        renderPipeline();
        renderList();
    }

    /* ── selection ───────────────────────────────────────────────── */
    function select(pk) {
        var rec = record(pk);
        if (!rec) return;
        var changed = state.selectedPk !== pk;
        state.selectedPk = pk;
        if (changed) state.tab = 'summary';
        el.body.dataset.mode = 'detail';
        renderList();
        renderEditor();
        // On narrow screens the list is hidden, so move the reader to the record.
        if (changed && window.matchMedia('(max-width: 1199.98px)').matches) {
            var heading = el.editor.querySelector('.sqr-proto__ed-title');
            if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus(); }
            el.editor.scrollIntoView({block: 'start'});
        }
    }
    function backToList() {
        el.body.dataset.mode = 'list';
        var card = el.items.querySelector('[aria-current="true"]');
        if (card) card.focus();
    }

    /* ── editor: chrome (header, stepper, next step) ─────────────── */
    function stepStates(rec) {
        if (rec.stage === 'opportunity') return ['current', 'todo', 'todo'];
        if (rec.stage === 'delivery') return ['done', 'current', 'todo'];
        if (rec.stage === 'billing') return ['done', 'done', 'current'];
        if (rec.closed_reason === 'billed') return ['done', 'done', 'done'];
        return rec.proposal_status === 'closed_won' ? ['done', 'closed', 'todo'] : ['closed', 'todo', 'todo'];
    }
    function renderChrome(rec) {
        var canWorkflow = rec.editable.indexOf('status') !== -1;
        var names = ['opportunity', 'delivery', 'billing'];
        var states = stepStates(rec);
        var actions = [];
        if (rec.perms.edit_form) actions.push(h('a', {class: 'btn btn-outline-primary', href: rec.urls.edit}, [icon('bi-pencil-square', 'me-2'), 'Edit submission']));
        var newTab = function () { return h('span', {class: 'visually-hidden', text: ' (opens in a new tab)'}); };
        if (rec.values.sqr_folder_link) actions.push(h('a', {class: 'btn btn-outline-secondary', href: rec.values.sqr_folder_link, target: '_blank', rel: 'noopener'}, [icon('bi-folder2-open', 'me-2'), 'SQR documents', icon('bi-box-arrow-up-right', 'ms-2 sqr-proto__ext'), newTab()]));
        if (rec.linked_request) actions.push(h('a', {class: 'btn btn-outline-secondary', href: rec.linked_request.url, target: '_blank', rel: 'noopener'}, [icon('bi-link-45deg', 'me-2'), rec.linked_request.code, icon('bi-box-arrow-up-right', 'ms-2 sqr-proto__ext'), newTab()]));
        if (rec.perms.delete) actions.push(h('a', {class: 'btn btn-outline-danger', href: rec.urls.delete}, [icon('bi-trash', 'me-2'), 'Delete']));

        var next = rec.next_step;
        var nextActions = [];
        if (next.action === 'review' && canWorkflow) {
            if (rec.status !== 'reviewed') nextActions.push(h('button', {type: 'button', class: 'btn btn-primary', on: {click: function () { openApprove(rec.pk); }}}, [icon('bi-patch-check', 'me-2'), 'Approve']));
            if (rec.status !== 'for_revision') nextActions.push(h('button', {type: 'button', class: 'btn btn-outline-danger', on: {click: function () { openRevise(rec.pk); }}}, [icon('bi-arrow-repeat', 'me-2'), 'Request revision']));
        }
        if (next.action === 'edit' && rec.perms.edit_form) nextActions.push(h('a', {class: 'btn btn-primary', href: rec.urls.edit}, [icon('bi-pencil-square', 'me-2'), 'Edit submission']));
        if (next.action === 'status' && canWorkflow) nextActions.push(h('button', {type: 'button', class: 'btn btn-outline-primary', on: {click: function () { goToField('opportunity', 'status'); }}}, 'Change SQR status'));

        var flags = rec.attention.filter(function (flag) { return flag.key !== 'revision' && flag.key !== 'awaiting'; });
        return [
            h('div', {class: 'sqr-proto__ed-head'}, [
                h('div', {style: 'min-width:0'}, [
                    h('h3', {class: 'sqr-proto__ed-title', text: rec.account}),
                    h('p', {class: 'sqr-proto__ed-sub', text: rec.service})
                ]),
                h('div', {class: 'sqr-proto__ed-actions'}, actions)
            ]),
            h('ol', {class: 'sqr-proto__stepper', 'aria-label': 'Progress through the pipeline'}, names.map(function (name, index) {
                var step = states[index];
                return h('li', {class: 'sqr-proto__step', dataset: {state: step, stage: name}, 'aria-current': step === 'current' ? 'step' : null}, [
                    icon(STAGE_ICONS[step]), STAGE_NAMES[name],
                    step === 'closed' ? h('span', {class: 'visually-hidden', text: ' (closed)'}) : null
                ]);
            })),
            h('div', {class: 'sqr-proto__next', dataset: {tone: next.tone}}, [
                h('div', null, [
                    h('p', {class: 'sqr-proto__next-eyebrow', text: 'Next step'}),
                    h('p', {class: 'sqr-proto__next-title', text: next.title}),
                    next.hint ? h('p', {class: 'sqr-proto__next-hint', text: next.hint}) : null
                ]),
                nextActions.length ? h('div', {class: 'sqr-proto__next-actions'}, nextActions) : null
            ]),
            rec.review_notes ? h('div', {class: 'sqr-proto__notes'}, [h('strong', {text: 'Revision notes'}), rec.review_notes]) : null,
            flags.length ? h('div', {class: 'sqr-proto__flags'}, flags.map(function (flag) {
                return h('span', {class: 'sqr-proto__badge', dataset: {tone: flag.tone}, text: flag.label});
            })) : null
        ];
    }

    /* ── editor: fields ──────────────────────────────────────────── */
    var EDIT_TYPES = {text: 1, textarea: 1, number: 1, date: 1, select: 1, user: 1, request: 1};
    function isInput(spec) { return !!EDIT_TYPES[spec.type]; }
    function lockReason(rec, spec) {
        if (!rec.editable.length) return 'Read-only for your role';
        if (rec.locked && spec.tab !== 'opportunity') return 'Locked: proposal is closed';
        return 'Admin only';
    }
    function optionsFor(rec, spec) {
        var current = rec.values[spec.key] || '';
        var list;
        if (spec.type === 'select') list = spec.choices.map(function (c) { return {value: c[0], label: c[1]}; });
        else if (spec.type === 'user') list = (state.options[spec.options] || []).slice();
        else list = (state.options.rq_options || []).slice();
        var blank = spec.key !== 'status';
        var known = list.some(function (o) { return o.value === current; });
        if (current && !known) {
            var label = spec.key === 'assigned_pm' ? rec.assigned_pm_name : spec.key === 'assigned_sse' ? rec.assigned_sse_name :
                spec.key === 'linked_request' && rec.linked_request ? rec.linked_request.code : current;
            list.unshift({value: current, label: label || current});
        }
        if (blank) list.unshift({value: '', label: '\u2014 Not set \u2014'});
        return list;
    }
    function displayText(rec, spec) {
        var value = rec.values[spec.key];
        if (value === '' || value === null || value === undefined) return '';
        if (spec.type === 'select') return choiceLabel(spec.key, value);
        if (spec.type === 'user') return spec.key === 'assigned_pm' ? rec.assigned_pm_name : rec.assigned_sse_name;
        if (spec.type === 'date') return fmtDate(value);
        if (spec.type === 'computed' && spec.format === 'money') return peso(value);
        if (spec.type === 'number') {
            if (spec.unit === '\u20b1') return peso(value);
            return value + (spec.unit ? ' ' + spec.unit : '');
        }
        return String(value);
    }
    function fillReadonly(node, rec, spec) {
        var value = rec.values[spec.key];
        node.replaceChildren();
        if (spec.type === 'request' && rec.linked_request) {
            node.append(h('a', {href: rec.linked_request.url, target: '_blank', rel: 'noopener', text: rec.linked_request.code}));
            node.dataset.empty = 'false';
            return;
        }
        if (spec.type === 'link' && value) {
            node.append(h('a', {href: value, target: '_blank', rel: 'noopener', text: 'Open link'}));
            node.dataset.empty = 'false';
            return;
        }
        var text = displayText(rec, spec);
        node.dataset.empty = text ? 'false' : 'true';
        if (spec.multiline) { node.style.whiteSpace = 'pre-wrap'; node.style.overflowWrap = 'anywhere'; }
        node.textContent = text || '\u2014';
    }
    function setControl(control, rec, spec) {
        var value = rec.values[spec.key] || '';
        if (control.tagName === 'SELECT' && !Array.prototype.some.call(control.options, function (o) { return o.value === value; })) {
            control.append(h('option', {value: value, text: displayText(rec, spec) || value}));
        }
        control.value = value;
    }
    function setFieldState(wrap, stateName, message) {
        wrap.dataset.state = stateName;
        var node = wrap.querySelector('.sqr-proto__state');
        if (!node) return;
        node.replaceChildren();
        if (stateName === 'saving') node.append(h('span', {class: 'sqr-proto__spinner', 'aria-hidden': 'true'}), 'Saving\u2026');
        else if (stateName === 'saved') node.append(icon('bi-check2'), 'Saved');
        else if (stateName === 'error') node.append(icon('bi-exclamation-circle'), message || 'Could not save');
        if (stateName === 'saved') setTimeout(function () { if (wrap.dataset.state === 'saved') setFieldState(wrap, 'idle'); }, 2200);
    }

    function buildControl(rec, spec, id) {
        var control;
        if (spec.type === 'textarea') control = h('textarea', {id: id, rows: 3});
        else if (spec.type === 'select' || spec.type === 'user' || spec.type === 'request') {
            control = h('select', {id: id}, optionsFor(rec, spec).map(function (o) { return h('option', {value: o.value, text: o.label}); }));
        } else if (spec.type === 'number') {
            control = h('input', {id: id, type: 'number', inputmode: 'decimal', step: spec.key === 'delivery_progress' || spec.key === 'discount_rate' ? '1' : 'any', min: spec.min, max: spec.max});
        } else if (spec.type === 'date') control = h('input', {id: id, type: 'date'});
        else control = h('input', {id: id, type: 'text', autocomplete: 'off', style: spec.uppercase ? 'text-transform:uppercase' : null});
        setControl(control, rec, spec);
        return control;
    }

    function renderField(rec, spec) {
        var editable = rec.editable.indexOf(spec.key) !== -1;
        var id = 'sqr-proto-f-' + rec.pk + '-' + spec.key;
        var wide = spec.type === 'textarea' || spec.multiline;
        var wrap = h('div', {class: 'sqr-proto__field' + (wide ? ' sqr-proto__field--wide' : ''), dataset: {fieldKey: spec.key, state: 'idle', emphasis: spec.emphasis ? 'true' : null}});
        if (spec.workflow) { renderStatusBlock(wrap, rec, spec); return wrap; }
        var lockedInput = !editable && isInput(spec);
        var label = h(editable ? 'label' : 'span', {class: 'sqr-proto__label', id: editable ? null : id + '-label', for: editable ? id : null}, [
            spec.label,
            lockedInput ? h('span', {class: 'sqr-proto__lock', title: lockReason(rec, spec)}, [icon('bi-lock-fill'), h('span', {class: 'visually-hidden', text: lockReason(rec, spec)})]) : null
        ]);
        wrap.append(label);
        if (editable) {
            var control = buildControl(rec, spec, id);
            control.dataset.key = spec.key;
            control.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') { setControl(control, record(rec.pk), spec); control.blur(); }
                else if (event.key === 'Enter' && spec.type !== 'textarea') { event.preventDefault(); control.blur(); }
                else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); control.blur(); }
            });
            var eventName = control.tagName === 'SELECT' || spec.type === 'date' ? 'change' : 'blur';
            control.addEventListener(eventName, function () { commit(rec.pk, spec, control, wrap); });
            wrap.append(h('div', {class: 'sqr-proto__control'}, [control, spec.unit && spec.unit !== '\u20b1' ? h('span', {class: 'sqr-proto__unit', text: spec.unit}) : null,
                spec.unit === '\u20b1' ? h('span', {class: 'sqr-proto__unit', text: 'PHP'}) : null]));
        } else {
            var box = h('div', {class: 'sqr-proto__readonly', role: 'group', 'aria-labelledby': id + '-label'});
            fillReadonly(box, rec, spec);
            wrap.append(box);
        }
        if (spec.hint) wrap.append(h('p', {class: 'sqr-proto__meta', text: spec.hint}));
        wrap.append(h('div', {class: 'sqr-proto__state', 'aria-live': 'polite'}));
        return wrap;
    }

    function renderStatusBlock(wrap, rec, spec) {
        var canWorkflow = rec.editable.indexOf('status') !== -1;
        var id = 'sqr-proto-status-' + rec.pk;
        wrap.replaceChildren(
            h(canWorkflow ? 'label' : 'span', {class: 'sqr-proto__label', for: canWorkflow ? id : null}, spec.label),
            h('div', {class: 'sqr-proto__control'}, badge('status', rec.status))
        );
        if (canWorkflow) {
            var select = buildControl(rec, spec, id);
            var reasonId = id + '-hold-reason';
            var hintId = reasonId + '-hint';
            var errorId = reasonId + '-error';
            var reason = h('textarea', {id: reasonId, rows: 3, required: true, 'aria-describedby': hintId + ' ' + errorId, value: rec.values.hold_reason || ''});
            var error = h('p', {id: errorId, class: 'sqr-proto__meta', role: 'alert', hidden: true});
            var confirm = h('button', {type: 'button', class: 'btn btn-sm btn-outline-secondary', text: 'Place On Hold'});
            var hold = h('div', {hidden: true}, [
                h('label', {class: 'sqr-proto__label', for: reasonId, text: 'Hold reason (required)'}),
                h('div', {class: 'sqr-proto__control'}, reason),
                h('p', {id: hintId, class: 'sqr-proto__meta', text: 'Explain the pause. This does not change Remarks, opportunity or delivery status.'}),
                error, confirm
            ]);
            select.addEventListener('change', function () {
                var value = select.value;
                hold.hidden = value !== 'on_hold';
                error.hidden = true;
                reason.removeAttribute('aria-invalid');
                if (value === 'on_hold') { reason.focus(); return; }
                select.value = record(rec.pk).status;
                if (value === rec.status) return;
                if (value === 'reviewed') openApprove(rec.pk);
                else if (value === 'for_revision') openRevise(rec.pk);
                else if (value === 'submitted') saveStatus(rec.pk, value, wrap);
            });
            confirm.addEventListener('click', function () {
                var value = reason.value.trim();
                if (!value) {
                    error.textContent = 'Enter a nonblank hold reason.';
                    error.hidden = false;
                    reason.setAttribute('aria-invalid', 'true');
                    reason.focus();
                    return;
                }
                confirm.disabled = true;
                select.disabled = true;
                save(rec.pk, 'status', 'on_hold', wrap, {holdReason: value}).catch(function (failure) {
                    confirm.disabled = false;
                    select.disabled = false;
                    error.textContent = failure.message;
                    error.hidden = false;
                    reason.setAttribute('aria-invalid', 'true');
                    reason.focus();
                });
            });
            wrap.append(h('div', {class: 'sqr-proto__control'}, select), hold,
                h('p', {class: 'sqr-proto__meta', text: 'On Hold pauses this SQR. Resume manually by selecting processing, revision or approval; existing approval and revision dialogs still apply.'}));
        }
        wrap.append(h('div', {class: 'sqr-proto__state', 'aria-live': 'polite'}));
    }

    function sectionsForTab(tab) {
        var order = [];
        var groups = {};
        state.schema.fields.forEach(function (spec) {
            if (spec.tab !== tab) return;
            if (!groups[spec.section]) { groups[spec.section] = []; order.push(spec.section); }
            groups[spec.section].push(spec);
        });
        return order.map(function (name) { return {name: name, fields: groups[name]}; });
    }
    function renderFormTab(rec, tab) {
        var nodes = [];
        if (rec.locked && tab !== 'opportunity') {
            nodes.push(h('div', {class: 'sqr-proto__locknote'}, [icon('bi-lock-fill'),
                'Locked because the proposal is Closed-Lost or Closed-Canceled.']));
        } else if (!rec.editable.length) {
            nodes.push(h('div', {class: 'sqr-proto__locknote'}, [icon('bi-eye'),
                rec.perms.edit_form ? 'View only. Use Edit submission to change the request.' : 'View only.']));
        }
        sectionsForTab(tab).forEach(function (section) {
            nodes.push(h('section', {class: 'sqr-proto__section', 'aria-label': section.name}, [
                h('h4', {text: section.name}),
                h('div', {class: 'sqr-proto__fields'}, section.fields.map(function (spec) { return renderField(rec, spec); }))
            ]));
        });
        return nodes;
    }

    /* ── editor: summary + history ───────────────────────────────── */
    // Opens the tab holding a field, then scrolls to and focuses it.
    function goToField(tab, key) {
        setTab(tab);
        var wrap = el.editor.querySelector('[data-field-key="' + key + '"]');
        if (!wrap) return;
        wrap.scrollIntoView({block: 'center', behavior: 'smooth'});
        var control = wrap.querySelector('input, select, textarea, button');
        if (control) control.focus({preventScroll: true});
        wrap.dataset.spotlight = 'true';
        window.setTimeout(function () { delete wrap.dataset.spotlight; }, 2200);
    }
    function fieldTab(key) { return (state.specs[key] || {}).tab || 'opportunity'; }
    function canEditField(rec, key) { return rec.editable.indexOf(key) !== -1; }
    // Don't invite edits to delivery/billing fields before the proposal is won.
    function canSetNow(rec, key) {
        if (!canEditField(rec, key) || rec.status === 'on_hold') return false;
        var tab = fieldTab(key);
        if (tab === 'delivery') return rec.stage !== 'opportunity';
        if (tab === 'billing') return rec.stage !== 'opportunity';
        return true;
    }

    // Missing or overdue items for the current stage. Only approved SQRs get this list;
    // earlier states are already covered by the "Next step" banner.
    function todoItems(rec) {
        var items = [];
        if (rec.status !== 'reviewed' || rec.stage === 'closed') return items;
        var v = rec.values;
        function add(key, title, hint, tone) {
            items.push({key: key, tab: fieldTab(key), title: title, hint: hint, tone: tone || 'warning', canEdit: canEditField(rec, key)});
        }
        function flag(name) { return rec.attention.filter(function (item) { return item.key === name; })[0]; }
        if (rec.stage === 'opportunity') {
            var validity = flag('validity');
            if (validity) add('proposal_status', validity.label, 'Confirm with the customer, then update the Proposal Status.', validity.tone);
            if (!v.proposal_status) add('proposal_status', 'Set the Proposal Status', 'Record where the customer stands: pending, negotiation, won, lost or canceled.');
            if (!rec.total) add('sse_manhrs', 'Add pricing', 'There is no total price yet. Enter the man-hours or maintenance amount.');
        } else if (rec.stage === 'delivery') {
            if (flag('overdue')) add('delivery_target_finish_date', 'Delivery is overdue', 'Update the target finish date or the progress.', 'danger');
            if (!v.assigned_sse) add('assigned_sse', 'Assign an SSE', 'Choose who will deliver the work.');
            if (!v.delivery_start_date) add('delivery_start_date', 'Set the delivery start date', 'Needed to plan the delivery.');
            if (!v.delivery_target_finish_date) add('delivery_target_finish_date', 'Set the target finish date', 'Needed to track overdue work.');
            if (!v.overall_status) add('overall_status', 'Set the overall status', 'Planning, in progress, on hold, completed or cancelled.');
            if (!v.delivery_health) add('delivery_health', 'Set the health status', 'On track, off track or at risk.');
            if (!v.delivery_progress) add('delivery_progress', 'Update the progress', 'Enter the % complete.', 'info');
        } else if (rec.stage === 'billing') {
            if (!v.delivery_completion_signed_date) add('delivery_completion_signed_date', 'Record the completion sign-off date', 'Add it once the customer signs off the delivery.');
            if (!v.po_pnl_date) add('po_pnl_date', 'Add the PO / PNL date', 'Required before billing.');
            if (!v.revenue_reference_no) add('revenue_reference_no', 'Add the billing reference', 'Invoice or internal billing reference number.');
            add('revenue_status', 'Mark as Billed', 'Do this once billing is done.', 'info');
        }
        return items;
    }
    function todoCounts(rec) {
        var counts = {};
        todoItems(rec).forEach(function (item) { counts[item.tab] = (counts[item.tab] || 0) + 1; });
        return counts;
    }
    // Flags the fields behind the open action items so the tab shows where to act.
    function applyTodoMarks(rec) {
        var byKey = {};
        todoItems(rec).forEach(function (item) {
            var entry = byKey[item.key];
            if (!entry) byKey[item.key] = {tone: item.tone, hints: [item.title + ': ' + item.hint]};
            else {
                entry.hints.push(item.title + ': ' + item.hint);
                if (item.tone === 'danger') entry.tone = 'danger';
            }
        });
        el.editor.querySelectorAll('[data-field-key]').forEach(function (wrap) {
            var item = byKey[wrap.dataset.fieldKey];
            var label = wrap.querySelector('.sqr-proto__label');
            var old = wrap.querySelector('.sqr-proto__todo-flag');
            if (old) old.remove();
            if (!item || !label) { delete wrap.dataset.todo; delete wrap.dataset.todoTone; return; }
            wrap.dataset.todo = 'true';
            wrap.dataset.todoTone = item.tone;
            var text = item.hints.length > 1 ? item.hints.length + ' action items' : 'Action needed';
            label.append(h('span', {class: 'sqr-proto__todo-flag', title: item.hints.join('\n')}, [icon('bi-exclamation-circle-fill'), text]));
        });
    }
    function refreshTabPills(rec) {
        var counts = todoCounts(rec);
        el.editor.querySelectorAll('.sqr-proto__tab').forEach(function (button) {
            var old = button.querySelector('.sqr-proto__pill');
            if (old) old.remove();
            var pending = counts[button.dataset.tab];
            if (pending) button.append(h('span', {class: 'sqr-proto__pill', title: pending + ' to do'}, [pending, h('span', {class: 'visually-hidden', text: ' to do'})]));
        });
    }
    function renderTodo(rec) {
        var items = todoItems(rec);
        if (rec.stage === 'closed' || rec.status !== 'reviewed') return null;
        var heading = h('h4', null, ['Action items', items.length ? h('span', {class: 'sqr-proto__pill', text: items.length}) : null]);
        if (!items.length) {
            return h('section', {class: 'sqr-proto__section sqr-proto__todo', 'aria-label': 'Action items', dataset: {empty: 'true'}}, [
                heading, h('p', {class: 'sqr-proto__todo-clear'}, [icon('bi-check-circle-fill'), 'Nothing is missing for this stage.'])
            ]);
        }
        return h('section', {class: 'sqr-proto__section sqr-proto__todo', 'aria-label': 'Action items'}, [
            heading,
            h('ul', {class: 'sqr-proto__todo-list'}, items.map(function (item) {
                var row = h('li', {class: 'sqr-proto__todo-item', dataset: {tone: item.tone}}, [
                    icon(item.tone === 'danger' ? 'bi-exclamation-octagon-fill' : item.tone === 'info' ? 'bi-info-circle-fill' : 'bi-exclamation-triangle-fill'),
                    h('div', {class: 'sqr-proto__todo-text'}, [h('p', {class: 'sqr-proto__todo-title', text: item.title}), h('p', {class: 'sqr-proto__todo-hint', text: item.hint})])
                ]);
                row.append(item.canEdit
                    ? h('button', {type: 'button', class: 'btn btn-sm btn-outline-primary', on: {click: function () { goToField(item.tab, item.key); }}}, [(state.specs[item.key] || {}).label ? 'Go to ' + state.specs[item.key].label : 'Open ' + (STAGE_NAMES[item.tab] || '') + ' tab', icon('bi-arrow-right', 'ms-2')])
                    : h('span', {class: 'sqr-proto__todo-lock'}, [icon('bi-lock-fill'), 'Not editable by you']));
                return row;
            }))
        ]);
    }

    // `key` makes an empty value actionable: it becomes a link to the field when the viewer can edit it.
    function fact(label, value, rec, key) {
        var empty = value === '' || value === null || value === undefined;
        var content = empty ? '\u2014' : value;
        if (empty && rec && key && canSetNow(rec, key)) {
            content = h('button', {type: 'button', class: 'sqr-proto__set', on: {click: function () { goToField(fieldTab(key), key); }}}, [icon('bi-plus-circle', 'me-1'), 'Set ', h('span', {class: 'visually-hidden', text: label})]);
        }
        return h('div', {class: 'sqr-proto__fact', dataset: {empty: empty ? 'true' : null}}, [h('dt', {text: label}), h('dd', null, content)]);
    }
    function renderSummary(rec) {
        var v = rec.values;
        var timeline = [
            ['SQR submitted', fmtDate(rec.created_at)], ['Approved', v.reviewed_at], ['Validity due', v.validity_due_date],
            ['Delivery start', fmtDate(v.delivery_start_date), 'delivery_start_date'], ['Target finish', fmtDate(v.delivery_target_finish_date), 'delivery_target_finish_date'],
            ['Actual finish', fmtDate(v.delivery_actual_finish_date), 'delivery_actual_finish_date'], ['Completion signed', fmtDate(v.delivery_completion_signed_date), 'delivery_completion_signed_date'],
            ['PO / PNL', fmtDate(v.po_pnl_date), 'po_pnl_date'], ['Billed', fmtDate(v.revenue_date), 'revenue_date']
        ];
        return [
            renderTodo(rec),
            h('section', {class: 'sqr-proto__section', 'aria-label': 'Key facts'}, [
                h('h4', {text: 'At a glance'}),
                h('dl', {class: 'sqr-proto__facts'}, [
                    fact('Total price', rec.total ? peso(rec.total) : ''),
                    fact('SQR status', badge('status', rec.status)),
                    fact('Proposal', badge('proposal_status', rec.proposal_status), rec, 'proposal_status'),
                    fact('Delivery', badge('overall_status', rec.overall_status), rec, 'overall_status'),
                    fact('Health', badge('delivery_health', rec.delivery_health), rec, 'delivery_health'),
                    fact('Progress', rec.progress === null || rec.progress === undefined ? '' : rec.progress + '%', rec, 'delivery_progress'),
                    fact('Billing', rec.stage === 'opportunity' || (rec.stage === 'closed' && rec.closed_reason !== 'billed') ? '' : badge('revenue_status', rec.revenue_status)),
                    fact('Requester', rec.requester), fact('Approver', rec.approver),
                    fact('Assigned PM', rec.assigned_pm_name, rec, 'assigned_pm'), fact('Assigned SSE', rec.assigned_sse_name, rec, 'assigned_sse'),
                    fact('Scope', rec.scope), fact('Group', rec.group),
                    fact('Remarks (SSE filing identifier)', h('span', {style: 'white-space:pre-wrap;overflow-wrap:anywhere', text: v.remarks || '\u2014'})),
                    fact('Hold reason (retained)', h('span', {style: 'white-space:pre-wrap;overflow-wrap:anywhere', text: v.hold_reason || '\u2014'}))
                ])
            ]),
            h('section', {class: 'sqr-proto__section', 'aria-label': 'Key dates'}, [
                h('h4', {text: 'Key dates'}),
                h('ul', {class: 'sqr-proto__timeline'}, timeline.map(function (row) {
                    var done = !!row[1];
                    var tail = h('span', {text: row[1] || 'Not set'});
                    if (!done && row[2] && canSetNow(rec, row[2])) {
                        tail = h('button', {type: 'button', class: 'sqr-proto__set', on: {click: function () { goToField(fieldTab(row[2]), row[2]); }}}, [icon('bi-calendar-plus', 'me-1'), 'Set date', h('span', {class: 'visually-hidden', text: ' for ' + row[0]})]);
                    }
                    return h('li', {dataset: {done: done ? 'true' : 'false'}}, [icon(done ? 'bi-check-circle-fill' : 'bi-circle'), row[0], tail]);
                }))
            ])
        ];
    }

    function renderHistory(rec, container) {
        var slot = h('div', {class: 'sqr-proto__history-wrap'});
        container.append(slot);
        var cached = state.history[rec.pk];
        if (cached) { fillHistory(slot, rec, cached); return; }
        slot.append(h('p', {class: 'sqr-proto__message', text: 'Loading history\u2026'}));
        api(rec.urls.history).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Unable to load history.');
            state.history[rec.pk] = res.data.entries;
            if (state.selectedPk === rec.pk && state.tab === 'history') fillHistory(slot, rec, res.data.entries);
        }).catch(function (error) {
            slot.replaceChildren(h('p', {class: 'sqr-proto__message', dataset: {state: 'error'}, text: error.message}));
        });
    }
    function fillHistory(slot, rec, entries) {
        if (!entries.length) { slot.replaceChildren(h('p', {class: 'sqr-proto__message', text: 'No changes have been recorded yet.'})); return; }
        slot.replaceChildren(h('ul', {class: 'sqr-proto__history'}, entries.map(function (entry) {
            return h('li', null, [
                h('div', {class: 'sqr-proto__history-head'}, [
                    h('span', null, [h('strong', {text: entry.action_label}), ' \u00b7 ' + entry.actor]),
                    h('span', {text: entry.created_at})
                ]),
                entry.summary ? h('p', {class: 'sqr-proto__change', text: entry.summary}) : null
            ].concat(entry.changes.map(function (change) {
                return h('p', {class: 'sqr-proto__change'}, [change.field_label + ': ', h('del', {text: change.old || 'empty'}), ' \u2192 ', h('ins', {text: change.new || 'empty'})]);
            })).concat(entry.restorable ? [h('div', null, h('button', {type: 'button', class: 'btn btn-sm btn-outline-secondary', on: {click: function (event) { restore(rec, entry, event.currentTarget); }}}, [icon('bi-arrow-counterclockwise', 'me-1'), 'Restore this change']))] : [])
              .concat(entry.is_restored ? [h('p', {class: 'sqr-proto__meta', text: 'Restored by ' + entry.restored_by + ' on ' + entry.restored_at})] : []));
        })));
    }
    function restore(rec, entry, button) {
        button.disabled = true;
        post(entry.restore_url).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Unable to restore this change.');
            markClassicStale();
            delete state.history[rec.pk];
            return refreshRecord(rec.pk).then(function () { showToast('History entry restored.'); renderPanel(); });
        }).catch(function (error) { button.disabled = false; showToast(error.message, {tone: 'error'}); });
    }

    /* ── editor: tabs and panel ──────────────────────────────────── */
    function tabList(rec) {
        var tabs = [['summary', 'Summary']].concat(state.schema.tabs);
        if (rec.perms.history) tabs.push(['history', 'History']);
        return tabs;
    }
    function renderEditor() {
        var rec = record(state.selectedPk);
        if (!rec) return;
        var tabs = tabList(rec);
        var counts = todoCounts(rec);
        if (!tabs.some(function (t) { return t[0] === state.tab; })) state.tab = 'summary';
        el.editor.replaceChildren(
            h('button', {type: 'button', class: 'btn btn-outline-secondary sqr-proto__back', on: {click: backToList}}, [icon('bi-arrow-left', 'me-2'), 'All SQRs']),
            h('div', {class: 'sqr-proto__chrome'}, renderChrome(rec)),
            h('div', {class: 'sqr-proto__tabs', role: 'tablist', 'aria-label': 'Record sections'}, tabs.map(function (tab) {
                var selected = tab[0] === state.tab;
                var pending = counts[tab[0]];
                return h('button', {
                    type: 'button', role: 'tab', class: 'sqr-proto__tab', id: 'sqr-proto-tab-' + tab[0],
                    'aria-selected': selected ? 'true' : 'false', 'aria-controls': 'sqr-proto-panel', tabindex: selected ? '0' : '-1',
                    dataset: {tab: tab[0]},
                    on: {click: function () { setTab(tab[0]); }, keydown: tabKeys}
                }, [tab[1], pending ? h('span', {class: 'sqr-proto__pill', title: pending + ' to do'}, [pending, h('span', {class: 'visually-hidden', text: ' to do'})]) : null]);
            })),
            h('div', {class: 'sqr-proto__panel', id: 'sqr-proto-panel', role: 'tabpanel', tabindex: '-1'})
        );
        renderPanel();
    }
    function renderPanel() {
        var rec = record(state.selectedPk);
        var panel = document.getElementById('sqr-proto-panel');
        if (!rec || !panel) return;
        panel.setAttribute('aria-labelledby', 'sqr-proto-tab-' + state.tab);
        panel.replaceChildren();
        if (state.tab === 'summary') panel.append.apply(panel, renderSummary(rec).filter(Boolean));
        else if (state.tab === 'history') renderHistory(rec, panel);
        else { panel.append.apply(panel, renderFormTab(rec, state.tab)); applyTodoMarks(rec); }
    }
    function setTab(tab, focus) {
        state.tab = tab;
        el.editor.querySelectorAll('.sqr-proto__tab').forEach(function (button) {
            var selected = button.dataset.tab === tab;
            button.setAttribute('aria-selected', selected ? 'true' : 'false');
            button.tabIndex = selected ? 0 : -1;
            if (selected && focus) button.focus();
        });
        renderPanel();
    }
    function tabKeys(event) {
        var tabs = Array.prototype.slice.call(el.editor.querySelectorAll('.sqr-proto__tab'));
        var index = tabs.indexOf(event.currentTarget);
        var target = null;
        if (event.key === 'ArrowRight') target = tabs[(index + 1) % tabs.length];
        else if (event.key === 'ArrowLeft') target = tabs[(index - 1 + tabs.length) % tabs.length];
        else if (event.key === 'Home') target = tabs[0];
        else if (event.key === 'End') target = tabs[tabs.length - 1];
        if (target) { event.preventDefault(); setTab(target.dataset.tab, true); }
    }

    /* ── editing ─────────────────────────────────────────────────── */
    function markClassicStale() {
        state.dirtyClassic = true;
        document.dispatchEvent(new CustomEvent('sqr:performance-invalidated'));
    }
    function normalize(spec, raw) {
        var value = String(raw === null || raw === undefined ? '' : raw).trim();
        if (spec.uppercase) value = value.toUpperCase();
        return value;
    }
    function validate(spec, value) {
        if (spec.type !== 'number' || value === '') return '';
        var n = parseFloat(value);
        if (isNaN(n)) return 'Enter a number';
        if (spec.min !== undefined && n < spec.min) return 'Must be at least ' + spec.min;
        if (spec.max !== undefined && n > spec.max) return 'Must be at most ' + spec.max;
        return '';
    }
    function commit(pk, spec, control, wrap) {
        var rec = record(pk);
        if (!rec) return;
        var value = normalize(spec, control.value);
        var original = rec.values[spec.key] || '';
        if (spec.type === 'number' && value !== '' && original !== '' && parseFloat(value) === parseFloat(original)) return;
        if (value === original) { control.value = value; return; }
        var problem = validate(spec, value);
        if (problem) { setFieldState(wrap, 'error', problem); control.value = original; return; }
        save(pk, spec.key, value, wrap, {control: control, spec: spec}).catch(function () {});
    }
    function save(pk, key, value, wrap, extra) {
        extra = extra || {};
        var rec = record(pk);
        var label = (state.specs[key] || {}).label || key;
        if (wrap) setFieldState(wrap, 'saving');
        var payload = {field: key, value: value};
        if (extra.reviewNotes !== undefined) payload.review_notes = extra.reviewNotes;
        if (extra.holdReason !== undefined) payload.hold_reason = extra.holdReason;
        return post(rec.urls.inline, payload).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Could not save this change.');
            markClassicStale();
            delete state.history[pk];
            var message = 'Saved ' + label + '.';
            if (res.data.workflow_side_effect_warning && res.data.workflow_side_effect_message) message += ' ' + res.data.workflow_side_effect_message;
            return refreshRecord(pk, key === 'status' ? res.data : null).then(function () {
                var fresh = state.selectedPk === pk ? el.editor.querySelector('[data-field-key="' + key + '"]') : null;
                if (fresh) setFieldState(fresh, 'saved');
                showToast(message, res.data.undo_url ? {undo: {url: res.data.undo_url, pk: pk}} : null);
                return res.data;
            });
        }).catch(function (error) {
            var current = state.selectedPk === pk ? el.editor.querySelector('[data-field-key="' + key + '"]') : wrap;
            if (current) setFieldState(current, 'error', error.message);
            if (extra.control && extra.spec) setControl(extra.control, record(pk), extra.spec);
            throw error;
        });
    }
    function saveStatus(pk, value, wrap) {
        save(pk, 'status', value, wrap).catch(function () {});
    }

    /* ── workflow dialogs ────────────────────────────────────────── */
    var dialogPk = null;
    function openApprove(pk) { dialogPk = pk; el.approve.showModal(); }
    function openRevise(pk) {
        dialogPk = pk;
        el.reviseNotes.value = '';
        el.reviseError.hidden = true;
        el.revise.showModal();
        el.reviseNotes.focus();
    }
    el.approve.addEventListener('click', function (event) {
        var button = event.target.closest('[data-approve]');
        if (!button) return;
        var choice = button.dataset.approve;
        var pk = dialogPk;
        var transfer = choice === 'draft' && typeof window.startSqrApprovalDraft === 'function' ? window.startSqrApprovalDraft() : null;
        el.approve.close();
        save(pk, 'status', 'reviewed', el.editor.querySelector('[data-field-key="status"]')).then(function (data) {
            var url = choice === 'eml' ? data.approval_email_eml_url : data.approval_email_draft_url;
            if (!url) { if (transfer) transfer.cancel(); return; }
            if (transfer) transfer.complete(url);
            else window.location.href = url;
        }).catch(function () { if (transfer) transfer.cancel(); });
    });
    el.revise.addEventListener('click', function (event) {
        if (!event.target.closest('[data-revise="confirm"]')) return;
        var notes = el.reviseNotes.value.trim();
        if (!notes) { el.reviseError.hidden = false; el.reviseNotes.focus(); return; }
        var rec = record(dialogPk);
        var pk = dialogPk;
        el.revise.close();
        openTeamsChat(rec, notes);
        save(pk, 'status', 'for_revision', el.editor.querySelector('[data-field-key="status"]'), {reviewNotes: notes}).catch(function () {});
    });
    function openTeamsChat(rec, notes) {
        if (!rec || (!rec.engineer_email && !rec.pm_email)) return;
        var people = [rec.engineer_email, rec.pm_email].filter(Boolean).join(',');
        var message = 'Hi ' + (rec.engineer_name ? '@' + rec.engineer_name : 'Team') + ',\n\nSubmitted SQR is for revision, please refer to the ff. comments below.\n\nComments:\n' + notes + '\n\nThanks';
        var url = 'https://teams.microsoft.com/l/chat/0/0?users=' + encodeURIComponent(people) +
            '&topicName=' + encodeURIComponent(rec.ref + ' ' + rec.account) + '&message=' + encodeURIComponent(message);
        window.open(url, '_blank', 'noopener');
    }

    /* ── data ────────────────────────────────────────────────────── */
    function index(records) {
        state.records = records;
        state.byPk = {};
        records.forEach(function (rec) { state.byPk[rec.pk] = rec; });
    }
    function syncEditor(rec, previous) {
        var sameAccess = JSON.stringify(previous.editable) === JSON.stringify(rec.editable) && previous.locked === rec.locked;
        var chrome = el.editor.querySelector('.sqr-proto__chrome');
        if (chrome) chrome.replaceChildren.apply(chrome, renderChrome(rec).filter(Boolean));
        refreshTabPills(rec);
        if (!sameAccess || state.tab === 'summary') { renderPanel(); return; }
        applyTodoMarks(rec);
        el.editor.querySelectorAll('[data-field-key]').forEach(function (wrap) {
            var spec = state.specs[wrap.dataset.fieldKey];
            if (!spec) return;
            if (spec.workflow) { renderStatusBlock(wrap, rec, spec); return; }
            var control = wrap.querySelector('[data-key]');
            if (control) { if (document.activeElement !== control) setControl(control, rec, spec); return; }
            var box = wrap.querySelector('.sqr-proto__readonly');
            if (box) fillReadonly(box, rec, spec);
        });
    }
    function refreshRecord(pk, workflowResponse) {
        return api(ENDPOINT + '?pk=' + encodeURIComponent(pk), {cache: 'no-store'}).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Unable to refresh this SQR.');
            var previous = record(pk);
            var fresh = res.data.record;
            if (workflowResponse && Object.prototype.hasOwnProperty.call(workflowResponse, 'hold_reason')) {
                fresh.values.hold_reason = workflowResponse.hold_reason;
            }
            state.records = state.records.map(function (rec) { return rec.pk === pk ? fresh : rec; });
            state.byPk[pk] = fresh;
            renderAll();
            if (state.selectedPk === pk && previous) syncEditor(fresh, previous);
        });
    }
    function apply(data) {
        state.viewer = data.viewer;
        state.schema = data.schema;
        state.options = data.options;
        state.specs = {};
        state.labels = {};
        data.schema.fields.forEach(function (spec) {
            state.specs[spec.key] = spec;
            if (spec.choices) {
                state.labels[spec.key] = {};
                spec.choices.forEach(function (choice) { state.labels[spec.key][choice[0]] = choice[1]; });
            }
        });
        index(data.records);
        state.history = {};
        if (!state.loaded) loadPrefs();
        state.loaded = true;
        state.loadedAt = Date.now();
        el.search.value = state.filters.search;
        renderAll();
        if (state.selectedPk && record(state.selectedPk)) {
            var focused = el.editor.contains(document.activeElement);
            if (!focused) renderEditor();
        } else if (state.selectedPk) {
            state.selectedPk = null;
            el.body.dataset.mode = 'list';
        }
    }
    function load(background) {
        if (inFlight) return inFlight;
        if (!background && !state.loaded) el.items.replaceChildren(h('li', {class: 'sqr-proto__count', text: 'Loading\u2026'}));
        inFlight = api(ENDPOINT, {cache: 'no-store'}).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Unable to load SQR data.');
            apply(res.data);
        }).catch(function (error) {
            if (!state.loaded) {
                el.items.replaceChildren(h('li', {class: 'sqr-proto__count', text: error.message}), h('li', null, h('button', {type: 'button', class: 'btn btn-outline-primary', on: {click: function () { load(); }}}, 'Try again')));
            } else showToast(error.message, {tone: 'error'});
        }).then(function () { inFlight = null; });
        return inFlight;
    }

    /* ── wiring ──────────────────────────────────────────────────── */
    var searchTimer = 0;
    el.search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () { state.filters.search = el.search.value.trim(); state.shown = PAGE_SIZE; savePrefs(); renderAll(); }, 150);
    });
    el.more.addEventListener('click', function () { state.shown += PAGE_SIZE; renderList(); });
    el.items.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        var cards = Array.prototype.slice.call(el.items.querySelectorAll('.sqr-proto__card'));
        var index = cards.indexOf(document.activeElement);
        if (index === -1) return;
        event.preventDefault();
        var next = cards[index + (event.key === 'ArrowDown' ? 1 : -1)];
        if (next) next.focus();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== '/' || root.hidden || event.ctrlKey || event.metaKey || event.altKey) return;
        var tag = (event.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select' || event.target.isContentEditable) return;
        event.preventDefault();
        el.search.focus();
    });

    document.addEventListener('sqr:performance-view', function (event) {
        var view = event.detail && event.detail.view;
        if (view === 'prototype') {
            if (!state.loaded) load();
            else if (Date.now() - state.loadedAt > STALE_MS) load(true);
            return;
        }
        if (state.dirtyClassic) {
            // The classic tables are server-rendered; reload so they never show pre-edit values.
            try { localStorage.setItem(VIEW_KEY, view || 'tracker'); } catch (error) { /* ignore */ }
            window.location.reload();
        }
    });
    if (!root.hidden) load();
})();
