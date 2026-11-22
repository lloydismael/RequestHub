(() => {
    const form = document.getElementById('accounts-edit-form');
    if (!form || !('onformdata' in form)) return;

    const searchForm = document.getElementById('account-search-form');
    const search = document.getElementById('account-search');
    const status = document.getElementById('account-search-status');
    const tbody = document.getElementById('accounts-tbody');
    const newRow = document.getElementById('account-new-row');
    const save = document.getElementById('accounts-save-btn');
    const discard = document.getElementById('accounts-discard');
    const draftStatus = document.getElementById('account-draft-status');
    const summary = document.getElementById('accounts-errors');
    const fieldLimit = form.dataset.fieldLimit ? Number(form.dataset.fieldLimit) : Infinity;
    const drafts = new Map();
    const baselines = new WeakMap();
    const undoButtons = new WeakMap();
    const rowSelector = '[data-account-prefix]';
    document.getElementById('accounts-native-limit')?.remove();
    save.disabled = false;
    let controller;
    let timer;
    let submitting = false;
    let pendingBatch = null;
    let revertsOffered = false;

    function controls(row) {
        const prefix = row.dataset.accountPrefix + '-';
        return Array.from(row.querySelectorAll('input[name], select[name], textarea[name]'))
            .filter(field => field.name.startsWith(prefix))
            .map(field => [field.name.slice(prefix.length), field]);
    }

    function readRow(row, successfulOnly = false) {
        return controls(row).flatMap(([key, field]) => {
            if (successfulOnly && field.disabled) return [];
            if (field.type === 'checkbox' || field.type === 'radio') return field.checked ? [[key, field.value]] : [];
            if (field.multiple) return Array.from(field.selectedOptions, option => [key, option.value]);
            return [[key, field.value]];
        });
    }

    function rowSignature(entries) {
        return JSON.stringify(entries.sort(([left], [right]) => left.localeCompare(right)));
    }

    function restoreRow(row, entries) {
        const values = new Map();
        for (const [key, value] of entries) {
            if (!values.has(key)) values.set(key, []);
            values.get(key).push(value);
        }
        for (const [key, field] of controls(row)) {
            const selected = values.get(key) || [];
            if (field.type === 'checkbox' || field.type === 'radio') field.checked = selected.includes(field.value);
            else if (field.multiple) {
                for (const option of field.options) option.selected = selected.includes(option.value);
            } else field.value = selected[0] ?? '';
        }
    }

    function resetRow(row) {
        restoreRow(row, baselines.get(row).entries);
        refreshConditions();
        captureRow(row);
        showDraftStatus();
        controls(row).find(([key]) => key === 'name')?.[1].focus();
    }

    function warnBeforeLeaving(event) {
        event.preventDefault();
        event.returnValue = '';
    }

    function showDraftStatus() {
        const count = drafts.size + Number(Boolean(newRow && newRow.dataset.changed));
        const dirty = Boolean(summary || count);
        if (dirty && !submitting) window.addEventListener('beforeunload', warnBeforeLeaving);
        else window.removeEventListener('beforeunload', warnBeforeLeaving);
        save.hidden = !dirty;
        discard.hidden = !dirty;
        draftStatus.textContent = count ? `${count} unsaved ${count === 1 ? 'change' : 'changes'}` : '';
    }

    function rememberRows(root) {
        root.querySelectorAll(rowSelector).forEach(row => {
            if (baselines.has(row)) return;
            const entries = readRow(row);
            baselines.set(row, {entries, signature: rowSignature(entries)});
        });
    }

    function captureRow(row) {
        const changed = rowSignature(readRow(row)) !== baselines.get(row).signature;
        if (row === newRow) {
            row.dataset.changed = changed ? 'true' : '';
        } else if (changed) {
            drafts.set(row.dataset.accountId, {row, submission: readRow(row, true)});
        } else {
            drafts.delete(row.dataset.accountId);
        }
        if (revertsOffered) updateRowRevert(row);
    }

    rememberRows(form);
    showDraftStatus();
    if (summary) summary.focus();

    function refreshConditions() {
        document.dispatchEvent(new CustomEvent('form-editor-refresh', {detail: form}));
    }

    function updateRowRevert(row) {
        const changed = row === newRow ? row.dataset.changed : drafts.has(row.dataset.accountId);
        let button = undoButtons.get(row);
        if (changed && !button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-secondary mt-2';
            button.textContent = 'Revert row edits';
            button.addEventListener('click', () => resetRow(row));
            (row === newRow ? row : row.lastElementChild).append(button);
            undoButtons.set(row, button);
        }
        if (button) button.hidden = !changed;
    }

    function recordEdit(event) {
        const row = event.target.closest?.(rowSelector);
        if (!summary && row) {
            captureRow(row);
            showDraftStatus();
        }
    }

    // The shared Form Editor refreshes conditions on the form before the event reaches this listener.
    document.addEventListener('input', recordEdit);
    document.addEventListener('change', recordEdit);

    if (summary) {
        summary.addEventListener('click', event => {
            const link = event.target.closest('a[href^="#"]');
            const field = link && document.getElementById(link.hash.slice(1));
            if (field) {
                event.preventDefault();
                field.focus();
            }
        });
    }
    discard.addEventListener('click', () => window.removeEventListener('beforeunload', warnBeforeLeaving));

    function cancelSearch() {
        clearTimeout(timer);
        if (controller) controller.abort();
        controller = null;
    }

    form.addEventListener('submit', event => {
        cancelSearch();
        refreshConditions();
        if (!summary) {
            form.querySelectorAll(rowSelector).forEach(captureRow);
            const entries = Array.from(drafts.values(), draft => draft.submission);
            const initial = entries.length;
            if (newRow?.dataset.changed) entries.push(readRow(newRow, true));
            pendingBatch = {initial, entries};
        }
        if (fieldLimit !== Infinity && Array.from(new FormData(form).keys()).length > fieldLimit) {
            event.preventDefault();
            pendingBatch = null;
            showDraftStatus();
            if (!summary) {
                revertsOffered = true;
                form.querySelectorAll(rowSelector).forEach(updateRowRevert);
            }
            draftStatus.textContent = 'Too many account fields to save at once. ' + (summary
                ? 'Reduce the number of selections, or discard and retry with fewer edits.'
                : 'Use Revert row edits to leave some accounts unchanged, or reduce the number of selections.');
            return;
        }
        submitting = true;
        window.removeEventListener('beforeunload', warnBeforeLeaving);
    });

    // Reindex only the submitted payload; live control IDs and labels stay stable.
    form.addEventListener('formdata', event => {
        if (!pendingBatch) return;
        const data = event.formData;
        for (const row of form.querySelectorAll(rowSelector)) {
            for (const [, field] of controls(row)) data.delete(field.name);
        }
        const {initial, entries} = pendingBatch;
        data.set('form-INITIAL_FORMS', initial);
        data.set('form-TOTAL_FORMS', entries.length);
        entries.forEach((fields, index) => fields.forEach(([key, value]) => data.append(`form-${index}-${key}`, value)));
        if (submitting) pendingBatch = null;
    });

    if (!searchForm) return;

    async function loadPage(url, moveFocus = false) {
        cancelSearch();
        const current = new AbortController();
        controller = current;
        try {
            const response = await fetch(url, {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                signal: current.signal,
            });
            if (!response.ok) throw new Error('Could not load accounts');
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            if (controller !== current) return;
            const results = page.getElementById('account-results');
            if (!results) throw new Error('Unexpected account results');

            const hadRowFocus = tbody.contains(document.activeElement);
            // Keep each draft's original controls, choices and signed schema token together.
            const rows = Array.from(results.querySelector('#accounts-tbody').children, row =>
                drafts.get(row.dataset.accountId)?.row ?? row);
            tbody.replaceChildren(...rows);
            const pager = results.querySelector('#account-pager');
            document.getElementById('account-pager').replaceWith(pager);
            document.getElementById('account-count-badge').textContent = results.querySelector('#account-count-badge').textContent;
            rememberRows(tbody);
            refreshConditions();
            const restoredUrl = results.dataset.listUrl;
            search.value = new URL(restoredUrl, window.location.href).searchParams.get('account_q') ?? '';
            form.action = restoredUrl;
            discard.href = restoredUrl;
            window.history.replaceState(null, '', restoredUrl);
            status.textContent = '';
            if (moveFocus || hadRowFocus) pager.querySelector('#account-limit-note').focus();
        } catch (error) {
            if (controller === current && !submitting) {
                status.textContent = 'Showing previous accounts. Retry search or select a page again.';
            }
        } finally {
            if (controller === current) controller = null;
        }
    }

    function searchUrl() {
        const url = new URL(searchForm.action);
        url.searchParams.set('tab', 'accounts');
        const query = search.value.trim();
        if (query) url.searchParams.set('account_q', query);
        return url;
    }

    search.addEventListener('input', () => {
        cancelSearch();
        timer = setTimeout(() => loadPage(searchUrl()), 250);
    });
    searchForm.addEventListener('submit', event => {
        event.preventDefault();
        loadPage(searchUrl());
    });
    form.addEventListener('click', event => {
        const link = event.target.closest('#account-pager a');
        if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        loadPage(link.href, true);
    });
})();