(function (global) {
    'use strict';

    const MAX_MESSAGE_LENGTH = 4000;

    function initAssistant(root, config) {
        if (!root || root.dataset.assistantInitialized === 'true') return;
        root.dataset.assistantInitialized = 'true';

        const document = global.document;
        const windowElement = root.closest('#ai-chat-window');
        const widget = windowElement ? windowElement.parentElement : null;
        const messages = root.querySelector('[data-assistant-messages]');
        const status = root.querySelector('[data-assistant-status]');
        const error = root.querySelector('[data-assistant-error]');
        const errorMessage = root.querySelector('[data-assistant-error-message]');
        const input = root.querySelector('[data-assistant-input]');
        const form = root.querySelector('[data-assistant-form]');
        const sendButton = root.querySelector('[data-assistant-send]');
        const retryButton = root.querySelector('[data-assistant-retry]');
        const newChatButton = root.querySelector('[data-assistant-new-chat]');
        const expandButton = root.querySelector('[data-assistant-expand]');
        const directory = root.querySelector('[data-assistant-directory]');
        const directoryForm = root.querySelector('[data-assistant-directory-form]');
        const directorySearch = root.querySelector('[data-assistant-directory-search]');
        const directoryResults = root.querySelector('[data-assistant-directory-results]');
        const sourceViewer = root.querySelector('[data-assistant-source-viewer]');
        const sourceContent = root.querySelector('[data-assistant-source-content]');
        const starters = root.querySelector('[data-assistant-starters]');

        let conversationId = null;
        let serverGeneration = 0;
        let clientGeneration = 0;
        let exchanges = [];
        let stateLoaded = false;
        let pending = false;
        let retryRequestId = null;
        let retryDraft = null;
        let expanded = false;
        let savedInertStates = [];

        const setStatus = (text) => {
            if (status) status.textContent = text;
        };

        const showError = (text, canRetry) => {
            if (errorMessage) errorMessage.textContent = text;
            if (error) error.hidden = false;
            if (retryButton) retryButton.hidden = !canRetry;
        };

        const clearError = () => {
            if (errorMessage) errorMessage.textContent = '';
            if (error) error.hidden = true;
            if (retryButton) retryButton.hidden = true;
        };

        const makeElement = (tagName, className, text) => {
            const element = document.createElement(tagName);
            if (className) element.className = className;
            if (text !== undefined) element.textContent = String(text);
            return element;
        };

        const sameOriginPath = (value) => {
            if (typeof value !== 'string' || !value) return null;
            try {
                const url = new URL(value, global.location.origin);
                if (url.origin !== global.location.origin) return null;
                return `${url.pathname}${url.search}${url.hash}`;
            } catch (_) {
                return null;
            }
        };

        const appendDestination = (container, card) => {
            const path = sameOriginPath(card.url);
            if (!path) return;
            const link = makeElement('a', 'rh-assistant__destination');
            link.setAttribute('href', path);
            link.appendChild(makeElement('strong', '', card.title || 'Open page'));
            if (card.description) {
                link.appendChild(makeElement('span', '', card.description));
            }
            link.appendChild(makeElement('span', 'rh-assistant__destination-action', 'Open page'));
            container.appendChild(link);
        };

        const appendDepartment = (container, department) => {
            const card = makeElement('section', 'rh-assistant__department-card');
            card.appendChild(makeElement('strong', '', department.name || 'Department'));
            card.appendChild(
                makeElement('span', 'rh-assistant__member-count', `${department.member_count || 0} active members`),
            );
            if (department.summary) {
                card.appendChild(makeElement('p', '', department.summary));
            } else {
                card.appendChild(makeElement('p', 'rh-assistant__muted', 'No approved description is available.'));
            }
            container.appendChild(card);
        };

        const appendMembers = (container, memberPage, departmentKey, query = '') => {
            if (!memberPage || !Array.isArray(memberPage.items)) return;
            const list = makeElement('ul', 'rh-assistant__member-list');
            memberPage.items.forEach((member) => {
                const item = makeElement('li', '');
                item.appendChild(makeElement('strong', '', member.display_name || 'Unnamed member'));
                item.appendChild(
                    makeElement(
                        'span',
                        '',
                        [member.department, member.application_role].filter(Boolean).join(' · '),
                    ),
                );
                list.appendChild(item);
            });
            container.appendChild(list);
            const nextDepartmentKey = departmentKey || memberPage.department_key;
            if (nextDepartmentKey && memberPage.total > memberPage.page * memberPage.page_size) {
                const more = makeElement('button', 'rh-assistant__button rh-assistant__button--secondary', 'More members');
                more.type = 'button';
                more.dataset.department = nextDepartmentKey;
                more.dataset.page = String(memberPage.page + 1);
                more.dataset.query = memberPage.query || query;
                more.addEventListener('click', async () => {
                    more.disabled = true;
                    more.hidden = true;
                    const loaded = await loadMembers(
                        more.dataset.department,
                        Number(more.dataset.page),
                        more.dataset.query,
                        container,
                    );
                    if (loaded) {
                        more.remove();
                    } else {
                        more.disabled = false;
                        more.hidden = false;
                    }
                });
                container.appendChild(more);
            }
        };

        const openGuide = async (source) => {
            const prefix = 'guide:';
            if (!source || source.kind !== 'guide' || !source.key || !source.key.startsWith(prefix)) return;
            const guideKey = source.key.slice(prefix.length);
            if (!/^[a-z][a-z0-9_]*$/.test(guideKey)) return;
            const url = config.guideUrlTemplate.replace('__guide_key__', encodeURIComponent(guideKey));
            const path = sameOriginPath(url);
            if (!path) return;
            const requestGeneration = clientGeneration;
            clearError();
            setStatus('Loading the approved guide…');
            try {
                const response = await global.fetch(path, {credentials: 'same-origin'});
                const payload = await response.json();
                if (requestGeneration !== clientGeneration) return;
                if (!response.ok) throw new Error(payload.message || 'The guide could not be loaded.');
                if (sourceContent) {
                    sourceContent.replaceChildren();
                    sourceContent.appendChild(makeElement('h4', '', payload.title));
                    sourceContent.appendChild(makeElement('p', '', payload.purpose));
                    const steps = makeElement('ol', 'rh-assistant__guide-steps');
                    (payload.steps || []).forEach((step) => steps.appendChild(makeElement('li', '', step)));
                    sourceContent.appendChild(steps);
                }
                if (directory) directory.hidden = true;
                if (sourceViewer) sourceViewer.hidden = false;
                setStatus('Approved guide loaded.');
            } catch (loadError) {
                if (requestGeneration === clientGeneration) {
                    showError(loadError.message || 'The guide could not be loaded.', false);
                    setStatus('The guide is unavailable.');
                }
            }
        };

        const appendSources = (container, sources) => {
            if (!Array.isArray(sources) || !sources.length) return;
            const sourceList = makeElement('div', 'rh-assistant__sources');
            sourceList.appendChild(makeElement('span', 'rh-assistant__sources-label', 'Sources'));
            sources.forEach((source) => {
                if (source.kind === 'guide' && typeof source.key === 'string') {
                    const button = makeElement('button', 'rh-assistant__source-link', source.title || 'Approved guide');
                    button.type = 'button';
                    button.addEventListener('click', () => openGuide(source));
                    sourceList.appendChild(button);
                } else {
                    sourceList.appendChild(makeElement('span', 'rh-assistant__source-label', source.title || 'Verified source'));
                }
            });
            container.appendChild(sourceList);
        };

        const appendReply = (container, reply) => {
            container.appendChild(makeElement('p', 'rh-assistant__message-text', reply.text || ''));
            (reply.destinations || []).forEach((card) => appendDestination(container, card));
            (reply.departments || []).forEach((department) => appendDepartment(container, department));
            appendMembers(container, reply.members);
            appendSources(container, reply.sources);
        };

        const renderExchanges = () => {
            if (!messages) return;
            messages.replaceChildren();
            exchanges.forEach((exchange) => {
                const userMessage = makeElement('article', 'rh-assistant__message rh-assistant__message--user');
                userMessage.appendChild(makeElement('p', 'rh-assistant__message-text', exchange.user));
                messages.appendChild(userMessage);
                const assistantMessage = makeElement('article', 'rh-assistant__message rh-assistant__message--assistant');
                appendReply(assistantMessage, exchange.reply || {text: exchange.assistant});
                messages.appendChild(assistantMessage);
            });
            messages.scrollTop = messages.scrollHeight;
        };

        const csrfToken = () => {
            const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
            return match ? decodeURIComponent(match[1]) : '';
        };

        const newRequestId = () => {
            if (global.crypto && typeof global.crypto.randomUUID === 'function') {
                return global.crypto.randomUUID();
            }
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
                const value = Math.floor(Math.random() * 16);
                return (character === 'x' ? value : (value & 0x3) | 0x8).toString(16);
            });
        };

        const fetchJson = async (url, options) => {
            const response = await global.fetch(url, options);
            let payload;
            try {
                payload = await response.json();
            } catch (_) {
                throw new Error('The assistant returned an unreadable response.');
            }
            if (!response.ok || payload.status === 'error') {
                const error = new Error(payload.message || 'The request could not be completed.');
                error.code = payload.code || 'request_failed';
                throw error;
            }
            return payload;
        };

        const loadState = async () => {
            if (stateLoaded) return;
            const requestGeneration = clientGeneration;
            clearError();
            setStatus('Connecting to Request Hub…');
            try {
                const payload = await fetchJson(config.stateUrl, {credentials: 'same-origin'});
                if (requestGeneration !== clientGeneration) return;
                conversationId = payload.conversation_id;
                serverGeneration = payload.generation;
                exchanges = (payload.messages || []).map((exchange) => ({
                    user: exchange.user,
                    assistant: exchange.assistant,
                }));
                stateLoaded = true;
                renderExchanges();
                if (starters && Array.isArray(payload.starters)) {
                    starters.hidden = exchanges.length > 0;
                }
                if (payload.provider_status === 'configured') {
                    setStatus('Ready when you are.');
                } else {
                    setStatus('Guides and the member directory are available; the AI model is not configured.');
                }
            } catch (loadError) {
                if (requestGeneration === clientGeneration) {
                    showError(loadError.message, false);
                    setStatus('The assistant could not connect.');
                }
            }
        };

        const setPending = (value) => {
            pending = value;
            // Keep focused controls enabled: disabling them drops focus and the shell closes the popup.
            if (sendButton) {
                if (value) sendButton.setAttribute('aria-disabled', 'true');
                else sendButton.removeAttribute('aria-disabled');
            }
            if (input) input.readOnly = value;
            if (value) setStatus('Working…');
        };

        const sendMessage = async (event) => {
            if (event) event.preventDefault();
            if (pending || !input) return;
            const draft = input.value;
            const message = draft.trim();
            if (!message) return;
            if (message.length > MAX_MESSAGE_LENGTH) {
                showError('Messages must be 4,000 characters or fewer.', false);
                return;
            }
            if (!conversationId) await loadState();
            if (!conversationId) return;

            if (retryDraft !== draft) {
                retryRequestId = null;
                retryDraft = draft;
            }
            const requestId = retryRequestId || newRequestId();
            retryRequestId = requestId;
            const requestGeneration = clientGeneration;
            setPending(true);
            clearError();
            try {
                const payload = await fetchJson(config.messagesUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken(),
                    },
                    body: JSON.stringify({
                        message,
                        current_page_key: config.pageKey || null,
                        conversation_id: conversationId,
                        request_id: requestId,
                    }),
                });
                if (requestGeneration !== clientGeneration) return;
                const reply = payload.reply;
                exchanges.push({user: message, assistant: reply.text, reply});
                renderExchanges();
                input.value = '';
                retryRequestId = null;
                retryDraft = null;
                if (starters) starters.hidden = true;
                setStatus('Answer ready.');
            } catch (sendError) {
                if (requestGeneration === clientGeneration) {
                    showError(sendError.message || 'The request failed. Your draft is still here.', true);
                    setStatus('The assistant could not finish that request.');
                }
            } finally {
                if (requestGeneration === clientGeneration) setPending(false);
            }
        };

        const renderDepartmentList = (items) => {
            if (!directoryResults) return;
            directoryResults.replaceChildren();
            if (!items.length) {
                directoryResults.appendChild(makeElement('p', 'rh-assistant__muted', 'No departments matched your search.'));
                return;
            }
            items.forEach((item) => {
                const button = makeElement('button', 'rh-assistant__department-option');
                button.type = 'button';
                button.dataset.department = item.key;
                button.dataset.assistantAction = 'department';
                button.appendChild(makeElement('strong', '', item.name));
                button.appendChild(makeElement('span', '', `${item.member_count} active members`));
                button.addEventListener('click', () => loadMembers(item.key, 1));
                directoryResults.appendChild(button);
            });
        };

        const loadDepartments = async () => {
            const url = new URL(config.departmentsUrl, global.location.origin);
            if (directorySearch && directorySearch.value.trim()) {
                url.searchParams.set('q', directorySearch.value.trim());
            }
            try {
                const payload = await fetchJson(`${url.pathname}${url.search}`, {credentials: 'same-origin'});
                renderDepartmentList(payload.items || []);
            } catch (loadError) {
                showError(loadError.message, false);
            }
        };

        const loadMembers = async (
            departmentKey,
            page,
            query = '',
            resultsContainer = directoryResults,
        ) => {
            const url = new URL(config.membersUrl, global.location.origin);
            url.searchParams.set('department', departmentKey);
            if (query) url.searchParams.set('q', query);
            url.searchParams.set('page', String(page));
            try {
                const payload = await fetchJson(`${url.pathname}${url.search}`, {credentials: 'same-origin'});
                if (!resultsContainer) return false;
                if (page === 1) resultsContainer.replaceChildren();
                const heading = makeElement('h4', '', departmentKey);
                if (page === 1 && resultsContainer === directoryResults) {
                    resultsContainer.appendChild(heading);
                }
                appendMembers(resultsContainer, payload, departmentKey, query);
                return true;
            } catch (loadError) {
                showError(loadError.message, false);
                return false;
            }
        };

        const showDirectory = () => {
            if (directory) directory.hidden = false;
            if (starters) starters.hidden = true;
            if (directorySearch) directorySearch.focus();
            loadDepartments();
        };

        const restoreInert = () => {
            savedInertStates.forEach(({element, inert}) => {
                element.inert = inert;
            });
            savedInertStates = [];
        };

        const setExpanded = (value, restoreFocus) => {
            if (!windowElement || !widget || expanded === value) return;
            expanded = value;
            windowElement.classList.toggle('ai-chat-window--expanded', value);
            widget.classList.toggle('ai-chat-widget--expanded', value);
            if (value) {
                windowElement.setAttribute('aria-modal', 'true');
                savedInertStates = Array.from(document.body.children)
                    .filter((element) => element !== widget)
                    .map((element) => ({element, inert: Boolean(element.inert)}));
                savedInertStates.forEach(({element}) => {
                    element.inert = true;
                });
                if (expandButton) {
                    expandButton.setAttribute('aria-expanded', 'true');
                    expandButton.setAttribute('aria-label', 'Restore assistant size');
                    expandButton.title = 'Restore assistant size';
                }
                const focusable = windowElement.querySelectorAll(
                    'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
                );
                if (focusable.length) focusable[0].focus();
            } else {
                windowElement.removeAttribute('aria-modal');
                restoreInert();
                if (expandButton) {
                    expandButton.setAttribute('aria-expanded', 'false');
                    expandButton.setAttribute('aria-label', 'Expand assistant');
                    expandButton.title = 'Expand assistant';
                }
                if (restoreFocus && expandButton) expandButton.focus();
            }
        };

        if (form) form.addEventListener('submit', sendMessage);
        if (sendButton && !form) sendButton.addEventListener('click', sendMessage);
        if (retryButton) retryButton.addEventListener('click', sendMessage);
        if (newChatButton) {
            newChatButton.addEventListener('click', async () => {
                const resetGeneration = ++clientGeneration;
                stateLoaded = false;
                conversationId = null;
                serverGeneration = 0;
                exchanges = [];
                retryRequestId = null;
                retryDraft = null;
                setPending(false);
                clearError();
                renderExchanges();
                if (input) input.value = '';
                if (starters) starters.hidden = false;
                if (directory) directory.hidden = true;
                if (sourceViewer) sourceViewer.hidden = true;
                setStatus('Starting a new conversation…');
                try {
                    const payload = await fetchJson(config.resetUrl, {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: {'X-CSRFToken': csrfToken()},
                    });
                    if (resetGeneration !== clientGeneration) return;
                    conversationId = payload.conversation_id;
                    serverGeneration = payload.generation;
                    stateLoaded = true;
                    setStatus('New conversation ready.');
                } catch (resetError) {
                    if (resetGeneration === clientGeneration) {
                        showError(resetError.message, false);
                        setStatus('A new conversation could not be started.');
                    }
                }
            });
        }
        if (input) {
            input.addEventListener('input', () => {
                if (input.value !== retryDraft) retryRequestId = null;
            });
            input.addEventListener('keydown', (event) => {
                if (
                    event.key === 'Enter'
                    && !event.shiftKey
                    && !event.isComposing
                    && event.keyCode !== 229
                ) {
                    event.preventDefault();
                    sendMessage(event);
                }
            });
        }
        if (directoryForm) {
            directoryForm.addEventListener('submit', (event) => {
                event.preventDefault();
                loadDepartments();
            });
        }
        if (expandButton) {
            expandButton.addEventListener('click', () => setExpanded(!expanded, true));
        }
        if (root) {
            root.addEventListener('click', (event) => {
                const target = event.target.closest('[data-assistant-action]');
                if (!target) return;
                const action = target.dataset.assistantAction;
                if (action === 'directory') showDirectory();
                if (action === 'directory-close' && directory) directory.hidden = true;
                if (action === 'source-close' && sourceViewer) sourceViewer.hidden = true;
            });
            root.addEventListener('assistant:open', loadState);
            root.addEventListener('assistant:close', () => setExpanded(false, false));
        }
        if (starters) {
            starters.querySelectorAll('[data-assistant-starter]').forEach((button) => {
                button.addEventListener('click', () => {
                    if (input) {
                        input.value = button.dataset.assistantStarter || button.textContent.trim();
                        sendMessage();
                    }
                });
            });
        }
        if (windowElement) {
            windowElement.addEventListener('keydown', (event) => {
                if (!expanded || event.key !== 'Tab') return;
                const focusable = Array.from(windowElement.querySelectorAll(
                    'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
                ));
                if (!focusable.length) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    last.focus();
                    event.preventDefault();
                } else if (!event.shiftKey && document.activeElement === last) {
                    first.focus();
                    event.preventDefault();
                }
            });
        }
    }

    global.initAssistant = initAssistant;

    const configElement = global.document.getElementById('assistant-config');
    const assistantRoot = global.document.querySelector('[data-assistant-root]');
    if (configElement && assistantRoot) {
        try {
            initAssistant(assistantRoot, JSON.parse(configElement.textContent));
        } catch (error) {
            assistantRoot.dataset.assistantInitialized = 'true';
            const status = assistantRoot.querySelector('[data-assistant-status]');
            if (status) status.textContent = 'The assistant could not be initialized.';
        }
    }
}(window));
