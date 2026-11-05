import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User


class AssistantWidgetTemplateTests(TestCase):
    def create_user(self, username, *, show_chatbot=True):
        return User.objects.create_user(
            username=username,
            password="assistant-widget-test-password",
            role=User.Roles.REQUESTOR,
            profile_completed=True,
            show_chatbot=show_chatbot,
        )

    def dashboard(self, user):
        client = Client()
        client.force_login(user)
        return client.get(reverse("hub:dashboard"))

    def test_anonymous_and_opted_out_users_do_not_receive_the_widget(self):
        anonymous = self.client.get(reverse("landing"))
        opted_out = self.dashboard(self.create_user("widget-opted-out", show_chatbot=False))

        self.assertNotContains(anonymous, 'id="ai-chat-widget-container"')
        self.assertNotContains(opted_out, 'id="ai-chat-widget-container"')

    @override_settings(AI_ASSISTANT_NATIVE_ENABLED=False)
    def test_disabled_native_rollout_keeps_the_legacy_iframe(self):
        response = self.dashboard(self.create_user("widget-legacy"))

        self.assertContains(response, 'id="ai-chat-widget-container"')
        self.assertContains(response, 'id="ai-chat-iframe"')
        self.assertNotContains(response, 'class="rh-assistant"')

    @override_settings(AI_ASSISTANT_NATIVE_ENABLED=True)
    def test_native_rollout_mounts_same_origin_configuration_only(self):
        with (
            patch("hub.assistant_directory.list_departments") as list_departments,
            patch("hub.assistant_provider.FoundryProvider") as provider_factory,
        ):
            response = self.dashboard(self.create_user("widget-native"))

        html = response.content.decode("utf-8")
        match = re.search(
            r'<script id="assistant-config" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        config = json.loads(match.group(1))
        self.assertContains(response, 'class="rh-assistant"')
        self.assertNotContains(response, 'id="ai-chat-iframe"')
        self.assertEqual(config["stateUrl"], reverse("hub:assistant-state"))
        self.assertEqual(config["messagesUrl"], reverse("hub:assistant-messages"))
        self.assertEqual(config["resetUrl"], reverse("hub:assistant-reset"))
        self.assertEqual(config["pageKey"], "dashboard")
        self.assertNotIn("AZURE_AI_BASE_URL", html)
        self.assertNotIn("AZURE_AI_API_KEY", html)
        self.assertIn("notification_toast_initial_cursor", response.context)
        list_departments.assert_not_called()
        provider_factory.assert_not_called()


@skipUnless(shutil.which("node"), "Node.js is required for JavaScript regressions")
class AssistantWidgetScriptTests(SimpleTestCase):
    def run_script(self, body):
        source = (
            Path(settings.BASE_DIR) / "static" / "js" / "assistant.js"
        ).read_text(encoding="utf-8")
        script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
let navigationCount = 0;
let modelProducedScriptNodes = 0;
let observerCount = 0;
class Element {
    constructor(name) {
        this.name = name;
        this.tagName = name.toUpperCase();
        this.children = [];
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.value = '';
        this.hidden = false;
        this.disabled = false;
        this.classList = {
            values: new Set(),
            add: value => this.classList.values.add(value),
            remove: value => this.classList.values.delete(value),
            contains: value => this.classList.values.has(value),
            toggle: value => {
                if (this.classList.values.has(value)) this.classList.values.delete(value);
                else this.classList.values.add(value);
            }
        };
        if (name === 'script') modelProducedScriptNodes += 1;
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return (this.text || '') + this.children.map(node => node.textContent).join(''); }
    appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
    replaceChildren(...nodes) { this.children = []; for (const node of nodes) this.appendChild(node); }
    remove() {
        if (this.parentNode) {
            this.parentNode.children = this.parentNode.children.filter(child => child !== this);
            this.parentNode = null;
        }
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] || null; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
    async dispatch(type, extra = {}) {
        const event = {target: this, preventDefault() { this.defaultPrevented = true; }, ...extra};
        for (const handler of this.listeners[type] || []) await handler(event);
        return event;
    }
    async click() {
        if (this.tagName === 'A') navigationCount += 1;
        return this.dispatch('click');
    }
    focus() { document.activeElement = this; }
    closest(selector) {
        if (selector === '#ai-chat-window' && this.windowElement) return this.windowElement;
        return selector === '[data-assistant-action]' && this.dataset.assistantAction ? this : null;
    }
    querySelector(selector) { return (this.nodes || {})[selector] || null; }
    querySelectorAll() { return []; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
}
const document = {
    cookie: 'csrftoken=test-token',
    activeElement: null,
    createElement: name => new Element(name),
    createTextNode: value => { const node = new Element('#text'); node.textContent = value; return node; },
    getElementById: () => null,
    querySelector: () => null,
    body: {children: []}
};
const window = {
    document,
    location: {origin: 'https://request-hub.test'},
    crypto: {randomUUID: () => '11111111-1111-4111-8111-111111111111'},
    fetch: async () => { throw new Error('Unexpected fetch'); }
};
window.window = window;
window.CustomEvent = class CustomEvent {
    constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
};
class MutationObserver {
    constructor() { observerCount += 1; }
    observe() {}
    disconnect() {}
}
const context = {window, document, URL, MutationObserver};
vm.runInNewContext(SOURCE, context);
function makeRoot() {
    const root = new Element('section');
    root.nodes = {};
    for (const selector of [
        '[data-assistant-messages]', '[data-assistant-status]', '[data-assistant-error]',
        '[data-assistant-error-message]', '[data-assistant-form]', '[data-assistant-input]',
        '[data-assistant-send]', '[data-assistant-new-chat]',
        '[data-assistant-retry]', '[data-assistant-directory]',
        '[data-assistant-directory-form]',
        '[data-assistant-directory-search]', '[data-assistant-directory-results]',
        '[data-assistant-source-viewer]', '[data-assistant-source-content]',
        '[data-assistant-expand]', '[data-assistant-starters]'
    ]) root.nodes[selector] = new Element('div');
    root.nodes['[data-assistant-error]'].appendChild(root.nodes['[data-assistant-error-message]']);
    root.nodes['[data-assistant-error]'].appendChild(root.nodes['[data-assistant-retry]']);
    root.nodes['[data-assistant-input]'].value = '';
    const widget = new Element('div');
    const windowElement = new Element('div');
    windowElement.parentElement = widget;
    root.windowElement = windowElement;
    root.closest = selector => selector === '#ai-chat-window' ? windowElement : null;
    document.body.children = [new Element('main'), widget];
    return root;
}
function response(payload, ok = true, status = 200) {
    return {ok, status, async json() { return payload; }};
}
const config = {
    stateUrl: '/assistant/state/',
    messagesUrl: '/assistant/messages/',
    resetUrl: '/assistant/conversation/reset/',
    guideUrlTemplate: '/assistant/guides/__guide_key__/',
    departmentsUrl: '/assistant/departments/',
    membersUrl: '/assistant/members/',
    pageKey: 'dashboard',
    pageLabel: 'Dashboard'
};
""" + body
        result = subprocess.run(
            [shutil.which("node"), "-"],
            input=script.replace("SOURCE", json.dumps(source)),
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initialization_and_text_safe_answer_rendering_never_navigate_early(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    const calls = [];
    window.fetch = async (url, options = {}) => {
        calls.push({url, options});
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            return response({status: 'ok', conversation_id: 'conversation-1', reply: {
                text: '<script>run()</script>',
                destinations: [{key: 'sqr', title: 'Service Quotations', description: 'Open the workspace.', url: '/sqr/'}],
                departments: [], members: null, sources: []
            }});
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    assert.equal(calls.filter(call => call.url === config.stateUrl).length, 1);
    const input = root.querySelector('[data-assistant-input]');
    input.value = 'Show me SQR';
    await root.querySelector('[data-assistant-form]').dispatch('submit');
    assert.equal(modelProducedScriptNodes, 0);
    assert.match(root.querySelector('[data-assistant-messages]').textContent, /<script>run\\(\\)<\\/script>/);
    assert.equal(navigationCount, 0);
    const links = root.querySelector('[data-assistant-messages]').children
        .flatMap(node => node.children).filter(node => node.tagName === 'A');
    assert.equal(links.length, 1);
    assert.equal(links[0].getAttribute('href'), '/sqr/');
    assert.equal(input.value, '');
    assert.equal(observerCount, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_request_errors_keep_the_draft_and_retry_the_same_request_id(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    const messageRequests = [];
    window.fetch = async (url, options = {}) => {
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            messageRequests.push(JSON.parse(options.body));
            if (messageRequests.length === 1) {
                return response({status: 'error', code: 'timeout', message: 'Try again.'}, false, 504);
            }
            return response({status: 'ok', conversation_id: 'conversation-1', reply: {
                text: 'Recovered.', destinations: [], departments: [], members: null, sources: []
            }});
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    const input = root.querySelector('[data-assistant-input]');
    input.value = 'Draft to keep';
    await root.querySelector('[data-assistant-form]').dispatch('submit');
    assert.equal(input.value, 'Draft to keep');
    assert.equal(root.querySelector('[data-assistant-error]').textContent, 'Try again.');
    await root.querySelector('[data-assistant-retry]').click();
    assert.equal(messageRequests.length, 2);
    assert.equal(messageRequests[0].request_id, messageRequests[1].request_id);
    assert.equal(input.value, '');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_pending_send_keeps_focused_controls_enabled_so_the_popup_stays_open(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    let release;
    let duringRequest = null;
    const input = root.querySelector('[data-assistant-input]');
    const sendButton = root.querySelector('[data-assistant-send]');
    window.fetch = async (url) => {
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            duringRequest = {
                inputDisabled: input.disabled,
                inputReadOnly: input.readOnly,
                sendDisabled: sendButton.disabled,
                sendAriaDisabled: sendButton.getAttribute('aria-disabled'),
            };
            await new Promise(resolve => { release = resolve; });
            return response({status: 'ok', conversation_id: 'conversation-1', reply: {
                text: 'Done.', destinations: [], departments: [], members: null, sources: []
            }});
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    input.value = 'Hello';
    const submitted = root.querySelector('[data-assistant-form]').dispatch('submit');
    while (!release) await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(duringRequest, {
        inputDisabled: false, inputReadOnly: true, sendDisabled: false, sendAriaDisabled: 'true',
    });
    release();
    await submitted;
    assert.equal(input.readOnly, false);
    assert.equal(sendButton.getAttribute('aria-disabled'), null);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_new_chat_ignores_an_in_flight_response_from_the_old_generation(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    let finishMessage;
    window.fetch = async (url, options = {}) => {
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            return new Promise(resolve => {
                finishMessage = () => resolve(response({status: 'ok', conversation_id: 'conversation-1', reply: {
                    text: 'Obsolete answer.', destinations: [], departments: [], members: null, sources: []
                }}));
            });
        }
        if (url === config.resetUrl) {
            return response({conversation_id: 'conversation-2', generation: 2, messages: []});
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    root.querySelector('[data-assistant-input]').value = 'Old question';
    const oldRequest = root.querySelector('[data-assistant-form]').dispatch('submit');
    await Promise.resolve();
    await root.querySelector('[data-assistant-new-chat]').click();
    finishMessage();
    await oldRequest;
    assert.doesNotMatch(root.querySelector('[data-assistant-messages]').textContent, /Obsolete answer/);
    assert.equal(root.querySelector('[data-assistant-input]').value, '');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_expanded_assistant_is_modal_and_restores_background_state_on_close(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    const outside = document.body.children[0];
    const windowElement = root.windowElement;
    window.initAssistant(root, config);
    await root.querySelector('[data-assistant-expand]').click();
    assert.equal(windowElement.classList.contains('ai-chat-window--expanded'), true);
    assert.equal(windowElement.getAttribute('aria-modal'), 'true');
    assert.equal(outside.inert, true);
    await root.dispatch('assistant:close');
    assert.equal(windowElement.classList.contains('ai-chat-window--expanded'), false);
    assert.equal(windowElement.getAttribute('aria-modal'), null);
    assert.equal(outside.inert, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_enter_submits_but_shift_enter_and_ime_enter_do_not(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    let messageRequests = 0;
    window.fetch = async (url) => {
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            messageRequests += 1;
            return response({status: 'ok', conversation_id: 'conversation-1', reply: {
                text: 'Submitted.', destinations: [], departments: [], members: null, sources: []
            }});
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    const input = root.querySelector('[data-assistant-input]');
    input.value = 'Question';
    const ime = await input.dispatch('keydown', {key: 'Enter', keyCode: 229, isComposing: true});
    const shift = await input.dispatch('keydown', {key: 'Enter', shiftKey: true, isComposing: false});
    assert.equal(ime.defaultPrevented, undefined);
    assert.equal(shift.defaultPrevented, undefined);
    assert.equal(messageRequests, 0);
    const enter = await input.dispatch('keydown', {key: 'Enter', shiftKey: false, isComposing: false});
    assert.equal(enter.defaultPrevented, true);
    assert.equal(messageRequests, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_directory_browser_uses_static_endpoints_and_text_safe_member_cards(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    const calls = [];
    window.fetch = async (url) => {
        calls.push(url);
        if (url === '/assistant/departments/') {
            return response({items: [{key: 'engineering', name: 'Engineering', member_count: 21}]});
        }
        if (url === '/assistant/members/?department=engineering&page=1') {
            return response({
                items: [{display_name: '<img src=x>', department: 'Engineering', application_role: 'Engineer'}],
                page: 1, page_size: 1, total: 2
            });
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    const trigger = new Element('button');
    trigger.dataset.assistantAction = 'directory';
    await root.dispatch('click', {target: trigger});
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(root.querySelector('[data-assistant-directory]').hidden, false);
    const departmentButton = root.querySelector('[data-assistant-directory-results]').children[0];
    await departmentButton.click();
    assert.match(root.querySelector('[data-assistant-directory-results]').textContent, /<img src=x>/);
    assert.match(root.querySelector('[data-assistant-directory-results]').textContent, /More members/);
    assert.equal(modelProducedScriptNodes, 0);
    assert.deepEqual(calls, [
        '/assistant/departments/',
        '/assistant/members/?department=engineering&page=1'
    ]);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_assistant_member_reply_can_continue_a_paginated_directory_result(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    const calls = [];
    window.fetch = async (url) => {
        calls.push(url);
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            return response({status: 'ok', conversation_id: 'conversation-1', reply: {
                text: 'Here are the first results.',
                destinations: [], departments: [],
                members: {
                    items: [{display_name: 'First Member', department: 'Engineering', application_role: 'Engineer'}],
                    page: 1, page_size: 1, total: 2, department_key: 'engineering', query: 'First'
                },
                sources: []
            }});
        }
        if (url === '/assistant/members/?department=engineering&q=First&page=2') {
            return response({
                items: [{display_name: 'Second Member', department: 'Engineering', application_role: 'Engineer'}],
                page: 2, page_size: 1, total: 2
            });
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    root.querySelector('[data-assistant-input]').value = 'Find members named First';
    await root.querySelector('[data-assistant-form]').dispatch('submit');
    function findNode(node, predicate) {
        if (predicate(node)) return node;
        for (const child of node.children) {
            const found = findNode(child, predicate);
            if (found) return found;
        }
        return null;
    }
    const more = findNode(
        root.querySelector('[data-assistant-messages]'),
        node => node.tagName === 'BUTTON' && node.textContent === 'More members'
    );
    assert.ok(more);
    assert.equal(more.dataset.department, 'engineering');
    assert.equal(more.dataset.page, '2');
    await more.click();
    const messages = root.querySelector('[data-assistant-messages]');
    assert.match(messages.textContent, /Second Member/);
    assert.doesNotMatch(messages.textContent, /More members/);
    assert.deepEqual(calls, [
        config.stateUrl,
        config.messagesUrl,
        '/assistant/members/?department=engineering&q=First&page=2'
    ]);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )

    def test_guide_source_opens_the_approved_in_popup_viewer(self):
        self.run_script(
            """
(async () => {
    const root = makeRoot();
    const calls = [];
    window.fetch = async (url) => {
        calls.push(url);
        if (url === config.stateUrl) {
            return response({conversation_id: 'conversation-1', generation: 1, messages: [], provider_status: 'configured'});
        }
        if (url === config.messagesUrl) {
            return response({status: 'ok', reply: {
                text: 'Use the Dashboard.',
                destinations: [], departments: [], members: null,
                sources: [{key: 'guide:dashboard', title: 'Dashboard', kind: 'guide'}]
            }});
        }
        if (url === '/assistant/guides/dashboard/') {
            return response({title: 'Dashboard guide', purpose: 'Review available work.', steps: ['Open Dashboard.']});
        }
        throw new Error('Unexpected URL ' + url);
    };
    window.initAssistant(root, config);
    await root.dispatch('assistant:open');
    root.querySelector('[data-assistant-input]').value = 'Help with the dashboard';
    await root.querySelector('[data-assistant-form]').dispatch('submit');
    function findNode(node, predicate) {
        if (predicate(node)) return node;
        for (const child of node.children) {
            const found = findNode(child, predicate);
            if (found) return found;
        }
        return null;
    }
    const source = findNode(
        root.querySelector('[data-assistant-messages]'),
        node => node.tagName === 'BUTTON' && node.textContent === 'Dashboard'
    );
    assert.ok(source);
    await source.click();
    assert.equal(root.querySelector('[data-assistant-source-viewer]').hidden, false);
    assert.deepEqual(calls, [
        config.stateUrl,
        config.messagesUrl,
        '/assistant/guides/dashboard/'
    ]);
    assert.equal(root.querySelector('[data-assistant-source-content]').children.length, 3);
    assert.match(root.querySelector('[data-assistant-source-content]').textContent, /Open Dashboard/);
    assert.equal(navigationCount, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        )
