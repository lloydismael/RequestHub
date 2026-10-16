(() => {
    'use strict';

    const formNames = {request: 'New Request', sqr_submission: 'New SQR Submission'};
    let serial = 0;

    function nextId(prefix) {
        let id;
        do {
            serial += 1;
            id = `${prefix}-${serial}`;
        } while (document.getElementById(id));
        return id;
    }

    function node(tag, text, className) {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = text;
        if (className) element.className = className;
        return element;
    }

    function labelled(parent, caption, tag = 'input') {
        const wrap = node('div', undefined, 'people-control');
        const input = node(tag, undefined, tag === 'select' ? 'form-select' : 'form-control');
        input.id = nextId('people-input');
        const label = node('label', caption, 'form-label small mb-1');
        label.htmlFor = input.id;
        wrap.append(label, input);
        parent.append(wrap);
        return input;
    }

    function option(select, value, label) {
        const item = node('option', label);
        item.value = value;
        select.append(item);
    }

    function actionButton({text, className, icon, label, focusKey}) {
        const element = node('button', undefined, `btn ${className}`);
        element.type = 'button';
        if (icon) {
            const glyph = node('i', undefined, `bi ${icon} me-1`);
            glyph.setAttribute('aria-hidden', 'true');
            element.append(glyph);
        }
        element.append(text);
        if (label) element.setAttribute('aria-label', label);
        if (focusKey) element.dataset.focusKey = focusKey;
        return element;
    }

    // Enter inside a field would otherwise submit the whole form and save.
    function enterKey(input, action) {
        input.addEventListener('keydown', event => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            action();
        });
    }

    function cardShell(title, help) {
        const card = node('section', undefined, 'people-card');
        const heading = node('h3', title, 'people-card__title h6');
        heading.id = nextId('people-title');
        card.setAttribute('aria-labelledby', heading.id);
        const head = node('div', undefined, 'people-card__head');
        head.append(heading, node('p', help, 'people-card__help small text-muted mb-0'));
        card.append(head);
        return card;
    }

    function note(text) {
        const box = node('div', undefined, 'people-note');
        const icon = node('i', undefined, 'bi bi-info-circle-fill');
        icon.setAttribute('aria-hidden', 'true');
        box.append(icon, node('span', text));
        return box;
    }

    function modeOption(groupName, checked, title, hint, focusKey, onSelect) {
        const label = node('label', undefined, 'people-option');
        const input = node('input', undefined, 'form-check-input');
        input.type = 'radio';
        input.name = groupName;
        input.checked = checked;
        input.dataset.focusKey = focusKey;
        input.addEventListener('change', () => {
            if (input.checked) onSelect();
        });
        const text = node('span', undefined, 'people-option__text');
        text.append(node('span', title, 'fw-semibold'), node('span', hint, 'people-option__hint'));
        label.append(input, text);
        return label;
    }

    function supportedEntries(response) {
        return Object.entries(response.fields).filter(([, metadata]) => ['person_ids', 'member_options'].includes(metadata.property));
    }

    function summarize(list) {
        const parts = list.map(([, metadata]) => {
            if (metadata.property === 'person_ids') {
                return metadata.values === null ? `${metadata.label}: all eligible` : `${metadata.label}: ${metadata.values.length} shown`;
            }
            const count = metadata.values.length;
            return `${count} Account Manager${count === 1 ? '' : 's'}`;
        });
        return parts.join(' · ') || 'Nothing to configure';
    }

    function initPeople(root) {
        const find = selector => root.querySelector(selector);
        const tabs = Array.from(root.querySelectorAll('[data-people-key]'));
        const panel = find('[role="tabpanel"]');
        const form = find('[data-people-form]');
        const controls = find('[data-people-controls]');
        const host = find('[data-people-fields]');
        const message = find('[data-people-message]');
        const stateText = find('[data-people-state]');
        const statusText = find('[data-people-status]');
        const discard = find('[data-people-discard]');
        const save = find('[data-people-save]');
        const reload = find('[data-people-reload]');
        const endpoints = {request: root.dataset.requestUrl, sqr_submission: root.dataset.sqrUrl};
        const summaries = {};
        const drafts = {};
        let key = 'request';
        let entries = [];
        let fields = {};
        let baseline = '';
        let ready = false;
        let busy = false;
        let saving = false;
        let conflict = false;
        let hasDraft = false;
        let revision = null;
        let statusLabel = '';
        let editing = null;

        panel.id = nextId('people-panel');
        tabs.forEach(tab => {
            tab.id = nextId('people-tab');
            tab.setAttribute('aria-controls', panel.id);
        });

        const dirty = () => ready && JSON.stringify(fields) !== baseline;
        const activeTab = () => tabs.find(tab => tab.dataset.peopleKey === key);

        function notify(text, error = false, moveFocus = error) {
            message.textContent = text;
            message.hidden = !text;
            message.className = `people-message alert mt-3 ${error ? 'alert-danger' : 'alert-success'}`;
            message.setAttribute('role', error ? 'alert' : 'status');
            if (text && moveFocus) message.focus();
        }

        async function request(url, options = {}) {
            const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options});
            let body = null;
            try { body = await response.json(); } catch (_) { body = null; }
            if (response.ok) return body;
            if (response.status === 409) {
                conflict = true;
                throw new Error('These settings changed since you loaded them. Your edits are kept. Reload to review the latest version before saving again.');
            }
            const detail = body && (body.error || body.message || body.errors);
            const action = options.method === 'POST' ? 'save' : 'load';
            const reason = detail ? (typeof detail === 'string' ? detail : JSON.stringify(detail)) : 'Try again. If your session expired, sign in again.';
            throw new Error(`Unable to ${action} settings (${response.status}). ${reason}`);
        }

        function duplicate(name, label, group, exclude) {
            const wanted = label.toLocaleLowerCase();
            return fields[name].some(member => member !== exclude && member.group === group && member.label.trim().toLocaleLowerCase() === wanted);
        }

        function payload() {
            const body = {};
            entries.forEach(([name, metadata]) => {
                if (metadata.property === 'person_ids') {
                    body[name] = fields[name] === null ? null : fields[name].slice();
                } else {
                    body[name] = fields[name].map(member => ({value: member.value, label: member.label, group: member.group}));
                }
            });
            return body;
        }

        function renderPeopleCard(name, metadata) {
            const card = cardShell(metadata.label, 'Choose who can be selected in this dropdown.');
            const choices = (metadata.choices || []).map(choice => ({value: String(choice.value), label: choice.label}));
            const byValue = new Map(choices.map(choice => [choice.value, choice]));
            const everyone = fields[name] === null;
            const modeName = nextId('people-mode');
            const modes = node('fieldset', undefined, 'people-modes');
            modes.append(node('legend', 'Who appears in this dropdown?', 'people-legend'));
            modes.append(
                modeOption(modeName, everyone, 'All eligible accounts', 'Includes everyone who is eligible now, plus accounts added later.', `mode:${name}:all`, () => {
                    fields[name] = null;
                    renderAll(`mode:${name}:all`);
                }),
                modeOption(modeName, !everyone, 'Only the people I choose', 'Starts with everyone eligible. Remove anyone you do not want to show.', `mode:${name}:pick`, () => {
                    fields[name] = choices.map(choice => choice.value);
                    renderAll(`add:${name}`);
                })
            );
            card.append(modes);

            if (everyone) {
                card.append(node('p', `${choices.length} eligible ${choices.length === 1 ? 'account is' : 'accounts are'} shown on the form.`, 'people-hint'));
                return card;
            }

            const selected = fields[name];
            const chosen = new Set(selected);
            const available = choices.filter(choice => !chosen.has(choice.value));
            const count = node('p', `Shown in this dropdown (${selected.length})`, 'people-count');
            const list = node('ul', undefined, 'people-list');
            list.setAttribute('aria-label', `${metadata.label} shown in this dropdown`);
            if (!selected.length) list.append(node('li', 'No one is shown yet. Add a person below.', 'people-empty'));
            selected.forEach((value, index) => {
                const choice = byValue.get(value);
                const label = choice ? choice.label : `Unavailable account (ID ${value})`;
                const row = node('li', undefined, 'people-row');
                const text = node('span', label, 'people-row__name');
                if (!choice) text.append(node('span', 'No longer eligible. Remove it to stop showing it.', 'd-block small text-muted'));
                const remove = actionButton({text: 'Remove', className: 'btn-outline-danger btn-sm', icon: 'bi-x-lg', label: `Remove ${label}`, focusKey: `remove:${name}:${index}`});
                remove.addEventListener('click', () => {
                    fields[name] = fields[name].filter((_, position) => position !== index);
                    renderAll(index < fields[name].length ? `remove:${name}:${index}` : `add:${name}`);
                });
                const actions = node('div', undefined, 'people-row__actions');
                actions.append(remove);
                row.append(text, actions);
                list.append(row);
            });

            const addRow = node('div', undefined, 'people-add');
            const search = choices.length > 10 ? labelled(addRow, 'Find a person') : null;
            if (search) {
                search.type = 'search';
                search.placeholder = 'Type part of a name';
                enterKey(search, () => {});
            }
            const picker = labelled(addRow, 'Person to add', 'select');
            picker.dataset.focusKey = `add:${name}`;
            const addButton = actionButton({text: 'Add person', className: 'btn-primary', icon: 'bi-plus-lg'});
            addRow.append(addButton);
            const hint = node('p', undefined, 'people-hint');
            hint.setAttribute('aria-live', 'polite');

            function fillPicker() {
                const query = search ? search.value.trim().toLocaleLowerCase() : '';
                const matches = available.filter(choice => choice.label.toLocaleLowerCase().includes(query));
                picker.replaceChildren();
                option(picker, '', matches.length ? 'Choose a person…' : 'No one to add');
                matches.forEach(choice => option(picker, choice.value, choice.label));
                picker.disabled = !matches.length;
                addButton.disabled = !matches.length;
                if (!available.length) hint.textContent = 'Everyone eligible is already shown.';
                else if (!matches.length) hint.textContent = 'No one matches that search.';
                else hint.textContent = `${matches.length} ${matches.length === 1 ? 'person' : 'people'} available to add.`;
            }
            if (search) search.addEventListener('input', fillPicker);
            addButton.addEventListener('click', () => {
                if (!picker.value) {
                    hint.textContent = 'Choose a person to add first.';
                    picker.focus();
                    return;
                }
                fields[name] = [...selected, picker.value];
                renderAll(`add:${name}`);
            });
            fillPicker();
            card.append(count, list, addRow, hint);
            return card;
        }

        function editRow(name, member, groups, index) {
            const row = node('li', undefined, 'people-row people-row--editing');
            const editor = node('div', undefined, 'people-edit');
            const nameInput = labelled(editor, 'Name');
            nameInput.type = 'text';
            nameInput.maxLength = 255;
            nameInput.value = member.label;
            nameInput.dataset.focusKey = `edit-name:${name}`;
            const departmentInput = labelled(editor, 'Department', 'select');
            groups.forEach(group => option(departmentInput, group.value, group.label));
            if (!groups.some(group => group.value === member.group)) option(departmentInput, member.group, `Unavailable department (${member.group})`);
            departmentInput.value = member.group;
            const saveEdit = actionButton({text: 'Save', className: 'btn-primary btn-sm', icon: 'bi-check-lg'});
            const cancelEdit = actionButton({text: 'Cancel', className: 'btn-outline-secondary btn-sm'});
            const editActions = node('div', undefined, 'people-edit__actions');
            editActions.append(saveEdit, cancelEdit);
            editor.append(editActions);
            const problem = node('p', undefined, 'people-error');
            problem.setAttribute('aria-live', 'polite');

            function commitEdit() {
                const label = nameInput.value.trim();
                const group = departmentInput.value;
                let issue = '';
                if (!label) issue = 'Enter a name.';
                else if (duplicate(name, label, group, member)) issue = `${label} is already listed under this department.`;
                problem.textContent = issue;
                if (issue) {
                    nameInput.focus();
                    return;
                }
                member.label = label;
                member.value = label;
                member.group = group;
                editing = null;
                renderAll(`edit:${name}:${index}`);
            }
            function cancelAndRestore() {
                editing = null;
                renderAll(`edit:${name}:${index}`);
            }
            saveEdit.addEventListener('click', commitEdit);
            cancelEdit.addEventListener('click', cancelAndRestore);
            enterKey(nameInput, commitEdit);
            nameInput.addEventListener('keydown', event => {
                if (event.key === 'Escape') cancelAndRestore();
            });
            row.append(editor, problem);
            return row;
        }

        function memberRow(name, member, groups) {
            const index = fields[name].indexOf(member);
            if (editing === member) return editRow(name, member, groups, index);
            const row = node('li', undefined, 'people-row');
            const text = node('span', member.label, 'people-row__name');
            const edit = actionButton({text: 'Edit', className: 'btn-outline-secondary btn-sm', icon: 'bi-pencil', label: `Edit ${member.label}`, focusKey: `edit:${name}:${index}`});
            const remove = actionButton({text: 'Remove', className: 'btn-outline-danger btn-sm', icon: 'bi-x-lg', label: `Remove ${member.label}`, focusKey: `remove:${name}:${index}`});
            edit.addEventListener('click', () => {
                editing = member;
                renderAll(`edit-name:${name}`);
            });
            remove.addEventListener('click', () => {
                fields[name] = fields[name].filter(item => item !== member);
                editing = null;
                renderAll(`add-name:${name}`);
            });
            const actions = node('div', undefined, 'people-row__actions');
            actions.append(edit, remove);
            row.append(text, actions);
            return row;
        }

        function renderNamesCard(name, metadata) {
            const groups = (metadata.groups || []).map(group => ({value: String(group.value), label: group.label}));
            const members = fields[name];
            const card = cardShell(name === 'customer_contact' ? 'Account Managers' : metadata.label, 'Names shown in this dropdown, grouped by department. They are form options only and do not create login accounts.');

            const addTitle = node('p', 'Add an Account Manager', 'people-subhead');
            const addRow = node('div', undefined, 'people-add people-add--names');
            const nameInput = labelled(addRow, 'Name');
            nameInput.type = 'text';
            nameInput.maxLength = 255;
            nameInput.autocomplete = 'off';
            nameInput.placeholder = 'For example, Jane Doe';
            nameInput.dataset.focusKey = `add-name:${name}`;
            const departmentInput = labelled(addRow, 'Department', 'select');
            option(departmentInput, '', 'Choose a department');
            groups.forEach(group => option(departmentInput, group.value, group.label));
            departmentInput.value = drafts[name] || '';
            departmentInput.dataset.focusKey = `add-department:${name}`;
            const addButton = actionButton({text: 'Add', className: 'btn-primary', icon: 'bi-plus-lg'});
            addRow.append(addButton);
            const addError = node('p', undefined, 'people-error');
            addError.setAttribute('aria-live', 'polite');

            function submitAdd() {
                const label = nameInput.value.trim();
                const group = groups.find(item => item.value === departmentInput.value);
                drafts[name] = departmentInput.value;
                let problem = '';
                if (!label) problem = 'Enter a name.';
                else if (!group) problem = 'Choose a department.';
                else if (duplicate(name, label, group.value)) problem = `${label} is already listed under ${group.label}.`;
                addError.textContent = problem;
                if (problem) {
                    (label && !group ? departmentInput : nameInput).focus();
                    return;
                }
                fields[name] = [...members, {value: label, label, group: group.value}];
                renderAll(`add-name:${name}`);
            }
            addButton.addEventListener('click', submitAdd);
            enterKey(nameInput, submitAdd);
            card.append(addTitle, addRow, addError);

            const sections = groups.slice();
            members.forEach(member => {
                if (!sections.some(section => section.value === member.group)) {
                    sections.push({value: member.group, label: `Unavailable department (${member.group})`});
                }
            });
            const wrap = node('div', undefined, 'people-groups');
            let shown = 0;
            sections.forEach(section => {
                const rows = members.filter(member => member.group === section.value).sort((a, b) => a.label.localeCompare(b.label));
                if (!rows.length) return;
                shown += rows.length;
                const group = node('div', undefined, 'people-group');
                const title = node('h4', undefined, 'people-group__title');
                title.append(node('span', section.label), node('span', String(rows.length), 'people-group__count'));
                const list = node('ul', undefined, 'people-list');
                list.setAttribute('aria-label', `${section.label} Account Managers`);
                rows.forEach(member => list.append(memberRow(name, member, groups)));
                group.append(title, list);
                wrap.append(group);
            });
            if (!shown) wrap.append(node('p', 'No Account Managers yet. Add a name above to show it in this dropdown.', 'people-empty'));
            card.append(wrap);
            return card;
        }

        function renderAll(focusKey) {
            host.replaceChildren();
            if (key === 'request') {
                host.append(note('Account Manager is filled in automatically with the person who submits the request, so only the engineer list is managed here.'));
            }
            if (!entries.length) host.append(node('p', 'No people fields are configured for this form.', 'text-muted'));
            entries.forEach(([name, metadata]) => {
                host.append(metadata.property === 'person_ids' ? renderPeopleCard(name, metadata) : renderNamesCard(name, metadata));
            });
            updateChrome();
            if (!focusKey) return;
            const target = host.querySelector(`[data-focus-key="${focusKey}"]`);
            if (target && !target.disabled) target.focus();
        }

        function updateChrome() {
            const changed = dirty();
            controls.disabled = busy || !ready || hasDraft;
            reload.disabled = busy;
            discard.disabled = busy || !changed;
            save.disabled = busy || !ready || hasDraft || conflict || !changed;
            save.textContent = saving ? 'Saving…' : 'Save and apply';
            if (hasDraft) statusText.textContent = 'Publish or reset the advanced draft before changing people here.';
            else if (conflict) statusText.textContent = 'These settings changed elsewhere. Reload to review them before saving.';
            else if (changed) statusText.textContent = 'You have unsaved changes.';
            else statusText.textContent = ready ? 'All changes are saved.' : '';
            stateText.textContent = ready ? `Version ${revision} · ${statusLabel}` : '';
        }

        function renderTabs() {
            tabs.forEach(tab => {
                const name = tab.dataset.peopleKey;
                const selected = name === key;
                tab.setAttribute('aria-selected', String(selected));
                tab.tabIndex = selected ? 0 : -1;
                tab.querySelector('[data-tab-summary]').textContent = summaries[name] || 'Loading…';
            });
            panel.setAttribute('aria-labelledby', activeTab().id);
        }

        function apply(response) {
            if (!response || !response.fields || response.revision === undefined) {
                throw new Error('The server returned unexpected settings. Reload and try again.');
            }
            const list = supportedEntries(response);
            const next = {};
            list.forEach(([name, metadata]) => {
                if (metadata.property === 'person_ids') {
                    if (metadata.values !== null && !Array.isArray(metadata.values)) throw new Error('The server returned invalid account selections.');
                    next[name] = metadata.values === null ? null : metadata.values.map(String);
                    return;
                }
                if (!Array.isArray(metadata.values)) throw new Error('The server returned invalid Account Manager selections.');
                next[name] = metadata.values.map(item => ({value: String(item.value), label: String(item.label), group: String(item.group)}));
            });
            entries = list;
            fields = next;
            baseline = JSON.stringify(fields);
            revision = response.revision;
            statusLabel = response.status || 'Unknown status';
            hasDraft = Boolean(response.has_draft);
            ready = true;
            conflict = false;
            editing = null;
            summaries[key] = summarize(list);
            renderAll();
            renderTabs();
            if (hasDraft) notify('An advanced-editor draft exists. Publish or reset it before changing people here.', true);
        }

        async function load() {
            busy = true;
            ready = false;
            conflict = false;
            editing = null;
            notify('');
            host.replaceChildren(node('p', 'Loading…', 'text-muted small'));
            panel.setAttribute('aria-busy', 'true');
            updateChrome();
            try {
                apply(await request(endpoints[key]));
            } catch (error) {
                host.replaceChildren(node('p', 'These settings could not be loaded. Use Reload to try again.', 'text-muted'));
                summaries[key] = 'Unavailable';
                notify(error.message, true);
            } finally {
                busy = false;
                panel.setAttribute('aria-busy', 'false');
                renderTabs();
                updateChrome();
            }
        }

        async function loadSummary(name) {
            try {
                summaries[name] = summarize(supportedEntries(await request(endpoints[name])));
            } catch (_) {
                summaries[name] = 'Unavailable';
            }
            renderTabs();
        }

        function confirmDiscard() {
            return !dirty() || window.confirm('Discard unsaved changes to these people settings?');
        }

        function switchTab(name) {
            if (busy || name === key || !confirmDiscard()) return false;
            key = name;
            editing = null;
            renderTabs();
            load();
            return true;
        }

        tabs.forEach((tab, index) => {
            tab.addEventListener('click', () => switchTab(tab.dataset.peopleKey));
            tab.addEventListener('keydown', event => {
                const steps = {ArrowRight: 1, ArrowLeft: -1};
                let target = null;
                if (event.key === 'Home') target = tabs[0];
                else if (event.key === 'End') target = tabs[tabs.length - 1];
                else if (event.key in steps) target = tabs[(index + steps[event.key] + tabs.length) % tabs.length];
                if (!target) return;
                event.preventDefault();
                if (switchTab(target.dataset.peopleKey) || target.dataset.peopleKey === key) target.focus();
            });
        });

        reload.addEventListener('click', () => {
            if (busy || !confirmDiscard()) return;
            load();
        });

        discard.addEventListener('click', () => {
            if (busy || !dirty()) return;
            fields = JSON.parse(baseline);
            editing = null;
            renderAll();
            notify('Unsaved changes discarded.', false, true);
        });

        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (busy || !ready || hasDraft || conflict || !dirty()) return;
            if (editing) {
                notify('Save or cancel the name you are editing before saving.', true);
                return;
            }
            busy = true;
            saving = true;
            notify('');
            panel.setAttribute('aria-busy', 'true');
            updateChrome();
            try {
                const token = find('[name="csrfmiddlewaretoken"]').value;
                const response = await request(endpoints[key], {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                    body: JSON.stringify({expected_revision: revision, fields: payload()}),
                });
                apply(response);
                if (!hasDraft) notify(`${formNames[key]} people saved and applied.`, false, true);
            } catch (error) {
                notify(error.message, true);
            } finally {
                busy = false;
                saving = false;
                panel.setAttribute('aria-busy', 'false');
                updateChrome();
            }
        });

        window.addEventListener('beforeunload', event => {
            if (!root.isConnected || !dirty()) return;
            event.preventDefault();
            event.returnValue = '';
        });

        renderTabs();
        load();
        Object.keys(endpoints).filter(name => name !== key).forEach(loadSummary);
    }

    document.querySelectorAll('[data-form-people]').forEach(root => {
        if (root.dataset.peopleReady) return;
        root.dataset.peopleReady = 'true';
        initPeople(root);
    });
})();
