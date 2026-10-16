(() => {
    'use strict';
    const root = document.getElementById('form-editor');
    if (!root) return;
    const state = JSON.parse(document.getElementById('form-editor-state').textContent);
    let schema = structuredClone(state.schema), revision = state.revision, dirty = false, busy = false;
    const byId = id => document.getElementById(id);
    const fields = byId('editor-fields');
    const baseline = new Set(state.baseline_keys);
    const locked = state.protected_fields || {};
    let selectedKey = schema.fields[0]?.key;
    let advancedOpen = false;
    const isProtected = field => !!locked[field?.key] || /password/i.test(field?.key || '');
    const isCustom = field => !baseline.has(field.key);
    function node(tag, text, cls) {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = text;
        if (cls) element.className = cls;
        return element;
    }
    function status(message) {
        byId('editor-status').textContent = `${message || state.status} · Revision ${revision}${dirty ? ' · Unsaved changes' : ''}`;
        root.querySelector('[data-action="publish"]').disabled = busy || dirty;
    }
    function changed() { dirty = true; status(); }
    function error(message) {
        const box = byId('editor-error');
        box.textContent = message;
        box.hidden = false;
        box.focus();
    }
    function control(container, label, value, onChange, options = {}) {
        const wrap = node('div', undefined, options.wide ? 'wide' : '');
        const id = `control-${control.next++}`;
        const caption = node('label', label, 'form-label small');
        caption.htmlFor = id;
        let input;
        if (options.choices) {
            input = node('select', undefined, 'form-select');
            options.choices.forEach(([key, text]) => {
                const option = node('option', text); option.value = key; input.append(option);
            });
        } else {
            input = node(options.multiline ? 'textarea' : 'input', undefined, 'form-control');
            if (options.multiline) input.rows = 3;
            else input.type = options.checkbox ? 'checkbox' : 'text';
        }
        input.id = id;
        input.disabled = !!options.disabled;
        if (options.checkbox) {
            wrap.className = 'form-check';
            input.className = 'form-check-input'; input.checked = !!value;
            caption.className = 'form-check-label';
        } else input.value = value ?? '';
        input.addEventListener(options.choices || options.checkbox ? 'change' : 'input', () => {
            onChange(options.checkbox ? input.checked : input.value); changed();
        });
        if (options.checkbox) wrap.append(input, caption); else wrap.append(caption, input);
        container.append(wrap);
        return input;
    }
    control.next = 0;
    function button(label, callback, disabled) {
        const btn = node('button', label, 'btn btn-outline-secondary btn-sm');
        btn.type = 'button'; btn.disabled = !!disabled;
        btn.addEventListener('click', callback); return btn;
    }
    function focusInspector() {
        byId('selected-field-heading')?.focus();
    }
    function move(index, delta) {
        const next = index + delta;
        if (next < 0 || next >= schema.fields.length || isProtected(schema.fields[index]) || isProtected(schema.fields[next])) return;
        [schema.fields[index], schema.fields[next]] = [schema.fields[next], schema.fields[index]];
        changed(); renderFields();
        status('Field moved. Save the draft to keep this order');
        const target = byId('editor-inspector').querySelector(`.move-${delta > 0 ? 'down' : 'up'}`);
        if (target && !target.disabled) target.focus(); else focusInspector();
    }
    function renderNavigation() {
        const query = byId('field-search').value.trim().toLocaleLowerCase();
        fields.replaceChildren();
        let count = 0;
        schema.fields.forEach(field => {
            if (![field.label, field.key, field.section].some(value => String(value || '').toLocaleLowerCase().includes(query))) return;
            count++;
            const item = button('', () => {
                selectedKey = field.key; advancedOpen = false;
                renderFields(); focusInspector();
            }, busy);
            item.className = `editor-field-link${field.key === selectedKey ? ' is-selected' : ''}`;
            item.dataset.key = field.key;
            item.setAttribute('aria-controls', 'editor-inspector');
            item.setAttribute('aria-pressed', String(field.key === selectedKey));
            item.append(node('span', /password/i.test(field.key) ? 'Password field (excluded)' : field.label || field.key, 'fw-semibold'));
            const tags = [field.section, isProtected(field) ? 'Locked' : isCustom(field) ? 'Custom' : 'Core', field.hidden ? 'Archived' : field.required ? 'Required' : 'Optional'].filter(Boolean);
            item.append(node('span', tags.join(' · '), 'small text-muted'));
            fields.append(item);
        });
        byId('field-count').textContent = `${count} of ${schema.fields.length} fields`;
        if (!count) fields.append(node('p', 'No matching fields. Try another search.', 'small text-muted mb-0'));
    }
    const restrictedSelections = new Map();
    function renderPeople(container, field, metadata) {
        const property = metadata.property || 'person_ids';
        const choices = metadata.choices || [];
        const section = node('fieldset', undefined, 'editor-people mt-3');
        section.append(node('legend', 'People / allowed values', 'h6'));
        section.append(node('p', `Choose from ${metadata.label || 'the eligible options'} for this form. Accounts and approved member names are not edited here.`, 'small text-muted'));
        const allId = `people-all-${control.next++}`;
        const allWrap = node('div', undefined, 'form-check mb-3');
        const all = node('input', undefined, 'form-check-input');
        all.type = 'checkbox'; all.id = allId;
        all.checked = !Array.isArray(field[property]);
        const allLabel = node('label', 'Allow all eligible options', 'form-check-label');
        allLabel.htmlFor = allId; allWrap.append(all, allLabel); section.append(allWrap);
        const picker = node('div');
        const searchId = `people-search-${control.next++}`;
        const searchLabel = node('label', 'Search people / allowed values', 'form-label small');
        searchLabel.htmlFor = searchId;
        const search = node('input', undefined, 'form-control mb-2');
        search.type = 'search'; search.id = searchId; search.placeholder = 'Search available names';
        const count = node('p', undefined, 'small text-muted mb-2');
        count.setAttribute('role', 'status'); count.setAttribute('aria-live', 'polite');
        const list = node('div', undefined, 'editor-people-list');
        list.id = `people-list-${control.next++}`; search.setAttribute('aria-controls', list.id);
        picker.append(searchLabel, search, list); section.append(count, picker); container.append(section);
        function selection() { return Array.isArray(field[property]) ? field[property].map(String) : []; }
        function updateCount() {
            if (all.checked) { count.textContent = `All eligible options allowed (${choices.length} currently available)`; return; }
            const selected = selection();
            const eligible = new Set(choices.map(choice => String(choice.value)));
            const unavailable = selected.filter(value => !eligible.has(value)).length;
            count.textContent = `${selected.length} selected${unavailable ? ` · ${unavailable} no longer available` : ''}`;
        }
        function renderList() {
            picker.hidden = all.checked;
            updateCount();
            if (all.checked) return;
            const selected = selection();
            const available = new Set(choices.map(choice => String(choice.value)));
            const entries = [...choices.map(choice => ({...choice, value: String(choice.value)})), ...selected.filter(value => !available.has(value)).map(value => ({value, label: `Saved selection (${value})`, unavailable: true}))];
            const query = search.value.trim().toLocaleLowerCase();
            list.replaceChildren(); updateCount();
            let matches = 0;
            entries.forEach(choice => {
                if (!String(choice.label).toLocaleLowerCase().includes(query)) return;
                matches++;
                const wrap = node('div', undefined, 'form-check editor-person-option');
                const input = node('input', undefined, 'form-check-input');
                input.type = 'checkbox'; input.id = `person-option-${control.next++}`;
                input.checked = selected.includes(choice.value);
                const label = node('label', undefined, 'form-check-label'); label.htmlFor = input.id;
                label.append(node('span', choice.label));
                if (choice.unavailable) label.append(node('span', 'No longer eligible. Uncheck to remove; it will not be silently discarded.', 'd-block small text-muted'));
                input.addEventListener('change', () => {
                    const current = selection();
                    field[property] = input.checked ? [...new Set([...current, choice.value])] : current.filter(value => value !== choice.value);
                    restrictedSelections.set(field.key, [...field[property]]);
                    changed(); updateCount();
                });
                wrap.append(input, label); list.append(wrap);
            });
            if (!matches) list.append(node('p', 'No matching people / allowed values.', 'small text-muted mb-0'));
        }
        all.addEventListener('change', () => {
            if (all.checked) {
                restrictedSelections.set(field.key, selection());
                delete field[property];
            } else field[property] = [...(restrictedSelections.get(field.key) || choices.map(choice => String(choice.value)))];
            changed(); renderList();
        });
        search.addEventListener('input', renderList);
        renderList();
    }
    function renderChoices(container, field) {
        const section = node('fieldset', undefined, 'mt-3');
        section.append(node('legend', 'Answer options', 'h6'));
        section.append(node('p', 'Labels are shown to people filling in the form. Keep saved values stable to preserve the meaning of existing answers.', 'small text-muted'));
        const rows = node('div'); section.append(rows);
        function draw(focusIndex) {
            rows.replaceChildren();
            (field.choices || []).forEach((choice, index) => {
                const row = node('div', undefined, 'editor-option-row mb-2');
                control(row, `Option ${index + 1} label`, choice.label, value => { choice.label = value; });
                control(row, `Option ${index + 1} saved value`, choice.value, value => { choice.value = value; });
                row.append(button('Remove option', () => {
                    field.choices.splice(index, 1); changed(); draw(); add.focus();
                }));
                rows.append(row);
            });
            if (focusIndex !== undefined) rows.children[focusIndex]?.querySelector('input')?.focus();
        }
        const add = button('Add option', () => {
            field.choices ||= [];
            let number = 1;
            while (field.choices.some(choice => choice.value === `option_${number}`)) number++;
            field.choices.push({value: `option_${number}`, label: `Option ${number}`});
            changed(); draw(field.choices.length - 1);
        });
        section.append(add); container.append(section); draw();
    }
    function renderInspector() {
        const inspector = byId('editor-inspector'); inspector.replaceChildren(); control.next = 0;
        const index = schema.fields.findIndex(field => field.key === selectedKey);
        const field = schema.fields[index];
        if (!field) { inspector.append(node('p', 'Choose a field from the list to edit its settings.', 'text-muted')); return; }
        const protectedField = isProtected(field), custom = isCustom(field);
        const heading = node('h3', /password/i.test(field.key) ? 'Password field (excluded)' : field.label || field.key, 'h5');
        heading.id = 'selected-field-heading'; heading.tabIndex = -1; inspector.append(heading);
        const description = protectedField ? (/password/i.test(field.key) ? 'Locked: passwords cannot be edited, reordered or previewed.' : `Locked: ${locked[field.key]}`) : custom ? 'Custom field. Edit its label and answers below.' : 'Core field. Its type, required status and visibility are managed by the application.';
        inspector.append(node('p', description, 'editor-lock'));
        if (protectedField) return;
        const actions = node('div', undefined, 'd-flex flex-wrap gap-2 mb-3');
        const up = button('Move up', () => move(index, -1), index === 0 || isProtected(schema.fields[index - 1]));
        const down = button('Move down', () => move(index, 1), index === schema.fields.length - 1 || isProtected(schema.fields[index + 1]));
        up.classList.add('move-up'); down.classList.add('move-down'); actions.append(up, down);
        if (custom) actions.append(button(field.hidden ? 'Restore field' : 'Archive field', () => {
            field.hidden = !field.hidden; changed(); renderFields(); focusInspector();
            status(field.hidden ? 'Field archived. Saved answers are retained' : 'Field restored');
        }));
        inspector.append(actions);
        if (field.hidden) inspector.append(node('p', 'Archived: this field is not shown on the live form. Restore it to use it again.', 'alert alert-secondary small'));
        const basics = node('div', undefined, 'field-controls');
        control(basics, 'Field label', field.label, value => {
            field.label = value; heading.textContent = value || field.key; renderNavigation();
        }, {wide: true});
        control(basics, 'Help text', field.help_text, value => { field.help_text = value; }, {multiline: true, wide: true});
        if (custom) control(basics, 'Answer required', field.required, value => { field.required = value; renderNavigation(); }, {checkbox: true});
        inspector.append(basics);
        const metadata = state.person_fields?.[field.key];
        if (metadata) renderPeople(inspector, field, metadata);
        else if (custom && ['select', 'multiselect'].includes(field.type)) renderChoices(inspector, field);
        const advanced = node('details', undefined, 'editor-advanced mt-4'); advanced.open = advancedOpen;
        advanced.append(node('summary', 'Advanced settings'));
        advanced.addEventListener('toggle', () => { advancedOpen = advanced.open; });
        const grid = node('div', undefined, 'field-controls pt-3');
        grid.append(node('p', `Field key: ${field.key} · Type: ${field.type}. Keys stay fixed. To change a custom field type, archive it and add a new field.`, 'small text-muted wide mb-0'));
        control(grid, 'Placeholder', field.placeholder, value => { field.placeholder = value; });
        control(grid, 'Section heading', field.section, value => { field.section = value; renderNavigation(); });
        control(grid, 'Layout width', field.width || 'full', value => { field.width = value; }, {choices: [['full', 'Full width'], ['half', 'Half width']]});
        if (custom) {
            const targets = [['', 'Always visible'], ...schema.fields.filter(other => other.key !== field.key && !isProtected(other) && !other.hidden).map(other => [other.key, other.label || other.key])];
            if (field.condition && !targets.some(([key]) => key === field.condition.field)) targets.push([field.condition.field, `Unavailable field (${field.condition.field})`]);
            const condition = control(grid, 'Show only when', field.condition?.field, value => {
                if (value) field.condition = {field: value, operator: 'equals', value: ''};
                else delete field.condition;
                advancedOpen = true; renderInspector();
                byId(condition.id)?.focus();
            }, {choices: targets});
            if (field.condition) {
                control(grid, 'Condition', field.condition.operator, value => { field.condition.operator = value; }, {choices: [['equals', 'Equals'], ['not_equals', 'Does not equal'], ['empty', 'Is empty'], ['not_empty', 'Is not empty']]});
                control(grid, 'Match value (ignored for empty checks)', field.condition.value, value => { field.condition.value = value; });
            }
        }
        advanced.append(grid); inspector.append(advanced);
    }
    function renderFields() {
        if (!schema.fields.some(field => field.key === selectedKey)) selectedKey = schema.fields[0]?.key;
        renderNavigation(); renderInspector();
    }
    function renderHistory() {
        const list = byId('editor-history'); list.replaceChildren();
        if (!state.history.length) list.append(node('p', 'No published versions yet.', 'small text-muted'));
        state.history.forEach(version => {
            const item = node('div', undefined, 'editor-history-item');
            item.append(node('div', `Version ${version.number}${version.id === state.published_version ? ' · Active' : ''}`, 'small fw-semibold'), node('div', `${version.actor} · ${new Date(version.created_at).toLocaleString()}`, 'small text-muted'));
            item.append(button('Republish this version', () => act('rollback', {version_id: version.id}))); list.append(item);
        });
    }
    function apply(data, replace) {
        revision = data.revision; Object.assign(state, data);
        if (replace) { schema = structuredClone(data.schema); restrictedSelections.clear(); dirty = false; render(); }
        else { renderHistory(); status(); }
    }
    async function request(action, payload) {
        const response = await fetch(state.endpoints[action], {
            method: action === 'history' ? 'GET' : 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': root.querySelector('[name=csrfmiddlewaretoken]').value},
            body: action === 'history' ? undefined : JSON.stringify({...payload, expected_revision: revision})
        });
        if (!response.ok) {
            let message = `Request failed (${response.status}).`;
            try { message = (await response.json()).error || message; } catch (_) { /* A login or CSRF response is not JSON. */ }
            if (response.status === 409) message += ' Unsaved changes are preserved. Copy changes or reload the page to get the latest revision.';
            throw new Error(message);
        }
        return action === 'preview' ? response.text() : response.json();
    }
    async function act(action, payload = {}) {
        if (busy) return;
        if (action === 'publish' && dirty) { error('Save the draft before publishing.'); return; }
        if (['publish', 'rollback', 'reset'].includes(action) && !confirm(action === 'reset' ? 'Publish original defaults application-wide? Previous versions and answers are retained.' : action === 'rollback' ? 'Republish this version application-wide? Unsaved editor changes will be discarded.' : 'Publish this saved draft application-wide?')) return;
        busy = true; byId('editor-error').hidden = true;
        root.setAttribute('aria-busy', 'true');
        status('Working…');
        const controls = [...byId('editor-form').querySelectorAll('input, select, textarea')].map(input => [input, input.disabled]);
        controls.forEach(([input]) => { input.disabled = true; });
        root.querySelectorAll('button').forEach(btn => { btn.disabled = true; });
        try {
            const data = await request(action, action === 'draft' || action === 'preview' ? {schema} : payload);
            if (action === 'preview') {
                byId('editor-preview').innerHTML = data;
                byId('editor-preview-panel').hidden = false;
                byId('editor-preview-panel').scrollIntoView({block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
                document.dispatchEvent(new Event('form-editor-preview'));
            } else if (action === 'history' && data.revision !== revision && dirty) {
                state.history = data.history; renderHistory(); error('Another session changed this form. Unsaved changes are preserved; reload before saving.');
            } else apply(data, action !== 'history' || !dirty);
        } catch (failure) { error(failure.message); }
        finally {
            busy = false; root.removeAttribute('aria-busy');
            controls.forEach(([input, disabled]) => { input.disabled = disabled; });
            renderFields(); renderHistory();
            root.querySelectorAll('[data-action], #add-field').forEach(btn => { btn.disabled = false; });
            status();
        }
    }
    function render() {
        byId('editor-title').value = schema.title || '';
        byId('editor-description').value = schema.description || '';
        renderFields(); renderHistory(); status();
    }
    ['title', 'description'].forEach(property => byId(`editor-${property}`).addEventListener('input', event => { schema[property] = event.target.value; changed(); }));
    byId('editor-form').addEventListener('submit', event => event.preventDefault());
    byId('field-search').addEventListener('input', renderNavigation);
    byId('add-field').addEventListener('click', () => {
        let number = 1; while (schema.fields.some(f => f.key === `custom_field_${number}`)) number++;
        const type = byId('new-field-type').value;
        const field = {key: `custom_field_${number}`, label: 'New custom field', help_text: '', placeholder: '', required: false, hidden: false, section: '', width: 'full', type, choices: []};
        if (['select', 'multiselect'].includes(type)) field.choices = [{value: 'option_1', label: 'Option 1'}];
        schema.fields.push(field); selectedKey = field.key; advancedOpen = false;
        byId('field-search').value = '';
        changed(); renderFields();
        byId('editor-inspector').querySelector('input').focus();
        status('Custom field added. Give it a clear label');
    });
    root.querySelectorAll('[data-action]').forEach(btn => btn.addEventListener('click', () => act(btn.dataset.action)));
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    render();
})();
