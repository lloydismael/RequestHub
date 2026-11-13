"""Execute the tracker status response handlers to catch JavaScript scope errors."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase


@skipUnless(shutil.which("node"), "Node.js is required for JavaScript regressions")
class SqrInlineStatusScriptTests(SimpleTestCase):
    def test_structured_display_renders_untrusted_values_as_text(self):
        template = (Path(settings.BASE_DIR) / "templates" / "hub" / "sqr.html").read_text(encoding="utf-8")
        self.assertIn("renderStructuredDisplay(displayEl, field, normalizedValue);", template)
        self.assertNotIn("displayEl.innerHTML = getDisplay", template)
        self.assertNotIn("pmDisp.innerHTML", template)
        labels = template.split("var LABELS = ", 1)[1].split(";\n    var CHOICES", 1)[0]
        styles = template.split("var BADGE_STYLES = ", 1)[1].split(";\n    var BADGE_ICONS", 1)[0]
        icons = template.split("var BADGE_ICONS = ", 1)[1].split(";\n\n    function renderStructuredDisplay", 1)[0]
        renderer = "function renderStructuredDisplay" + template.split(
            "function renderStructuredDisplay", 1
        )[1].split("\n    function getDisplay", 1)[0]
        source = (
            "var LABELS = " + labels + ";\n"
            "var BADGE_STYLES = " + styles + ";\n"
            "var BADGE_ICONS = " + icons + ";\n"
            + renderer
        )
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Element {
    constructor(name) { this.name = name; this.children = []; this.attributes = {}; this.style = {}; }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
    appendChild(child) { this.children.push(child); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
}
const document = {
    createElement: name => new Element(name),
    createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; }
};
const context = {document};
vm.runInNewContext(SOURCE, context);
const display = new Element('div');
context.renderStructuredDisplay(display, 'proposal_status', '<img src=x onerror=alert(1)>');
assert.equal(display.textContent, '–');
assert.equal(display.children[0].name, 'span');
context.renderStructuredDisplay(display, 'proposal_status', 'closed_won');
assert.equal(display.textContent, 'Closed-Won');
assert.equal(display.children[0].children[0].name, 'i');
context.renderStructuredDisplay(display, 'delivery_progress', '50<img src=x onerror=alert(1)>');
assert.equal(display.textContent, '50%');
assert.equal(display.children[0].children[0].children[0].style.width, '50%');
for (const [field, value, label, tone] of [
    ['schedule_performance', '', 'N/R', 'text-muted'],
    ['schedule_performance', 'delayed', 'Delayed', 'text-danger'],
    ['budget_effort_performance', 'at_risk', 'At Risk', 'text-warning'],
    ['quality_performance', 'met', 'Met', 'text-success']
]) {
    context.renderStructuredDisplay(display, field, value);
    assert.equal(display.textContent, label);
    assert.ok(display.children[0].className.includes(tone));
}
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_revision_email_only_saves_without_opening_teams(self):
        template = (Path(settings.BASE_DIR) / "templates" / "hub" / "sqr.html").read_text(encoding="utf-8")
        source = "function openRevisionModal" + template.split("function openRevisionModal", 1)[1].split(
            "    document.querySelectorAll('.sqr-inline-cell')", 1
        )[0]
        self.assertIn('id="sqr-revision-email-only"', template)
        self.assertIn("Submit Email Notification Only", template)
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const saves = [], chats = [];
const nodes = {};
const classes = () => ({add() {}, remove() {}});
for (const id of ['modal', 'notes', 'confirm', 'email-only', 'cancel', 'close', 'backdrop']) {
    nodes['sqr-revision-' + id] = {
        handlers: new Set(), classList: classes(), setAttribute() {}, focus() {},
        addEventListener(type, fn) { this.handlers.add(fn); },
        removeEventListener(type, fn) { this.handlers.delete(fn); },
        click() { for (const fn of [...this.handlers]) fn(); }
    };
}
const cell = {closest: () => ({dataset: {
    sqrRef: 'SQR-7', sqrCustomer: 'Customer', engineerEmail: 'engineer@example.com',
    pmEmail: 'pm@example.com', engineerName: 'Engineer'
}})};
const context = {
    document: {getElementById: id => nodes[id], body: {classList: classes()}},
    window: {open: (...args) => chats.push(args)}, setTimeout: fn => fn(),
    saveField: (...args) => saves.push(args)
};
vm.runInNewContext(SOURCE, context);
for (const action of ['email-only', 'confirm', 'cancel', 'email-only']) {
    context.openRevisionModal('7', 'for_revision', cell, cell, cell);
    assert.equal(nodes['sqr-revision-notes'].value, '');
    nodes['sqr-revision-notes'].value = '  Please revise scope.  ';
    nodes['sqr-revision-' + action].click();
    for (const id of ['confirm', 'email-only', 'cancel', 'close', 'backdrop']) {
        assert.equal(nodes['sqr-revision-' + id].handlers.size, 0);
    }
}
assert.equal(saves.length, 3);
for (const args of saves) {
    assert.equal(args[0], '7');
    assert.equal(args[1], 'status');
    assert.equal(args[2], 'for_revision');
    assert.equal(args[5], 'Please revise scope.');
}
assert.equal(chats.length, 1);
assert.ok(chats[0][0].startsWith('https://teams.microsoft.com/'));
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_automatic_clipboard_starts_before_fetch_and_launches_after_copy(self):
        source = (Path(settings.BASE_DIR) / "static" / "hub" / "sqr_approval_autocopy.js").read_text(encoding="utf-8")
        script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
(async () => {
    for (const withBody of [true, false]) for (const mode of ['success', 'denied', 'unsupported', 'fetch-failed', 'parse-failed', 'cancel']) {
        const events = [];
        const window = {location: {href: ''}};
        let notice = null;
        const clipboard = {async write(items) {
            events.push('write-start');
            if (mode === 'denied') throw new Error('Denied');
            const html = await items[0].data['text/html'];
            const text = await items[0].data['text/plain'];
            assert.equal(html.type, 'text/html');
            assert.equal(text.type, 'text/plain');
            assert.equal(await html.text(), '<img src="https://example.com/logo.png" alt="Phil-Data"><table><tbody><tr><td>Approved</td></tr></tbody></table>');
            assert.equal(await text.text(), 'Approved');
            events.push('copied');
        }};
        vm.runInNewContext(SOURCE, {
            window,
            navigator: mode === 'unsupported' ? {} : {clipboard},
            ClipboardItem: class {constructor(data) {this.data = data;}},
            Blob,
            document: {getElementById: () => null, createElement: () => {
                const created = {children: [], setAttribute() {}, appendChild(child) {this.children.push(child);}};
                notice = notice || created;
                return created;
            }, querySelector: () => ({prepend() {}})},
            DOMParser: class {parseFromString() {return {getElementById(id) {
                if (id === 'sqr-email-html') return {textContent: mode === 'parse-failed' ? '{' : JSON.stringify('<img src="https://example.com/logo.png" alt="Phil-Data"><table><tbody><tr><td>Approved</td></tr></tbody></table>')};
                if (id === 'sqr-email-text') return {textContent: JSON.stringify('Approved')};
                if (id === 'sqr-email-open') return {getAttribute: name => name === 'href'
                    ? 'mailto:reviewer@example.com?subject=Approved&body='
                    : (withBody ? 'mailto:reviewer@example.com?subject=Approved&body=Approved' : null)};
                return null;
            }}}},
            fetch: async () => {events.push('fetch'); return {ok: mode !== 'fetch-failed', text: async () => 'preview'};}
        });
        const transfer = window.startSqrApprovalDraft();
        if (mode !== 'unsupported') assert.equal(events[0], 'write-start');
        assert.equal(window.location.href, '');
        if (mode === 'cancel') {
            transfer.cancel();
            await new Promise(resolve => setImmediate(resolve));
            assert.equal(window.location.href, '');
            assert.equal(events.includes('fetch'), false);
        } else {
            await transfer.complete('/preview/');
            const copied = mode === 'success';
            const launch = 'mailto:reviewer@example.com?subject=Approved&body=';
            assert.equal(window.location.href, copied ? launch : '/preview/', `${mode}, withBody=${withBody}`);
            if (copied) {
                assert.match(notice.textContent, /formatted.*copied/i);
                assert.match(notice.textContent, /blank.*body/);
                assert.match(notice.textContent, /Ctrl[+]V/);
                assert.doesNotMatch(notice.textContent, /Ctrl[+]A|text body filled in/);
                assert.equal(notice.children[0].href, launch);
                assert.deepEqual(events, ['write-start', 'fetch', 'copied']);
            } else {
                assert.equal(notice, null);
            }
        }
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_approval_submits_microsoft_draft_directly_without_loading_preview(self):
        source = (Path(settings.BASE_DIR) / "static" / "hub" / "sqr_approval_autocopy.js").read_text(encoding="utf-8")
        script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
(async () => {
    for (const microsoftUrl of ['/sqr/7/approval-email/microsoft/', '//evil.example/steal/']) {
        const window = {location: {href: ''}};
        const events = [];
        let submitted = null;
        vm.runInNewContext(SOURCE, {
            window,
            navigator: {clipboard: {async write(items) {
                try { await items[0].data['text/html']; } catch (error) { events.push('clipboard-skipped'); }
            }}},
            ClipboardItem: class {constructor(data) {this.data = data;}},
            Blob,
            document: {
                getElementById: () => null,
                createElement: () => ({children: [], appendChild(child) { this.children.push(child); }, submit() { submitted = this; }}),
                body: {appendChild() {}},
                querySelector: () => ({prepend() {}})
            },
            DOMParser: class {parseFromString() {return {getElementById: () => ({
                textContent: JSON.stringify('x'), getAttribute: () => 'mailto:reviewer@example.com'
            })}}},
            fetch: async () => {events.push('fetch'); return {ok: true, text: async () => 'preview'};}
        });
        await window.startSqrApprovalDraft().complete('/preview/', {microsoftUrl, csrfToken: 'token-123'});
        await new Promise(resolve => setImmediate(resolve));
        if (microsoftUrl.startsWith('/sqr/')) {
            assert.deepEqual(events, ['clipboard-skipped']);
            assert.equal(window.location.href, '');
            assert.equal(submitted.method, 'post');
            assert.equal(submitted.action, microsoftUrl);
            assert.equal(submitted.children[0].name, 'csrfmiddlewaretoken');
            assert.equal(submitted.children[0].value, 'token-123');
        } else {
            // An off-site action is ignored and the normal preview flow runs.
            assert.equal(submitted, null);
            assert.ok(events.includes('fetch'));
        }
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_approval_submits_microsoft_draft_form_instead_of_mailto(self):
        source = (Path(settings.BASE_DIR) / "static" / "hub" / "sqr_approval_autocopy.js").read_text(encoding="utf-8")
        script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
(async () => {
    for (const action of ['/sqr/7/approval-email/microsoft/', '//evil.example/steal/']) {
        const window = {location: {href: ''}};
        const appended = [];
        let submitted = null;
        const created = () => ({
            children: [],
            appendChild(child) { this.children.push(child); },
            submit() { submitted = this; }
        });
        const microsoft = {
            getAttribute: name => name === 'action' ? action : '7',
            querySelector: () => ({value: 'token-123'})
        };
        vm.runInNewContext(SOURCE, {
            window,
            navigator: {},
            ClipboardItem: class {},
            Blob,
            document: {
                getElementById: () => null,
                createElement: created,
                body: {appendChild: node => appended.push(node)},
                querySelector: () => ({prepend() {}})
            },
            DOMParser: class {parseFromString() {return {getElementById(id) {
                if (id === 'sqr-email-html') return {textContent: JSON.stringify('<table>Approved</table>')};
                if (id === 'sqr-email-text') return {textContent: JSON.stringify('Approved')};
                if (id === 'sqr-email-microsoft') return microsoft;
                return {getAttribute: name => name === 'href' ? 'mailto:reviewer@example.com?subject=Approved&body=' : null};
            }}}},
            fetch: async () => ({ok: true, text: async () => 'preview'})
        });
        await window.startSqrApprovalDraft().complete('/preview/');
        if (action.startsWith('/sqr/')) {
            assert.equal(window.location.href, '');
            assert.ok(submitted);
            assert.equal(submitted.method, 'post');
            assert.equal(submitted.action, action);
            assert.equal(submitted.children[0].name, 'csrfmiddlewaretoken');
            assert.equal(submitted.children[0].value, 'token-123');
        } else {
            assert.equal(submitted, null);
            assert.equal(window.location.href, '/preview/');
        }
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preview_autostarts_microsoft_draft_once_after_sign_in(self):
        source = (Path(settings.BASE_DIR) / "static" / "hub" / "sqr_approval_preview.js").read_text(encoding="utf-8")
        script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const store = {};
let submits = 0;
function run(autostart) {
    const form = {
        getAttribute: name => name === 'data-autostart' ? (autostart ? 'true' : null) : '7',
        submit() { submits++; }
    };
    vm.runInNewContext(SOURCE, {
        window: {sessionStorage: {getItem: key => store[key] || null, setItem: (key, value) => { store[key] = value; }}},
        document: {getElementById: id => id === 'sqr-email-microsoft' ? form : null}
    });
}
run(false);
assert.equal(submits, 0);
run(true);
assert.equal(submits, 1);
run(true);
assert.equal(submits, 1);
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preview_clipboard_html_plain_and_manual_fallbacks(self):
        source = (Path(settings.BASE_DIR) / "static" / "hub" / "sqr_approval_preview.js").read_text(encoding="utf-8")
        script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
(async () => {
    for (const mode of ['html', 'plain', 'blocked', 'unsupported']) {
        let handler;
        let formats;
        let selected = false;
        const nodes = {
            'sqr-email-copy': {addEventListener(type, fn) { handler = fn; }},
            'sqr-email-feedback': {},
            'sqr-email-text': {textContent: JSON.stringify('Quotation body')},
            'sqr-email-html': {textContent: JSON.stringify('<table>Quotation body</table>')},
            'sqr-email-plain-fallback': {},
            'sqr-email-plain': {focus() {}, select() { selected = true; }}
        };
        const clipboard = {
            async write(items) {
                if (mode !== 'html') throw new Error('Denied');
                formats = Object.keys(items[0].data);
            },
            async writeText(text) {
                if (mode === 'blocked') throw new Error('Denied');
                assert.equal(text, 'Quotation body');
                formats = ['text/plain'];
            }
        };
        vm.runInNewContext(SOURCE, {
            document: {getElementById: id => nodes[id]},
            navigator: mode === 'unsupported' ? {} : {clipboard},
            ClipboardItem: class { constructor(data) { this.data = data; } },
            Blob
        });
        await handler();
        assert.equal(nodes['sqr-email-copy'].disabled, false);
        if (mode === 'html') assert.deepEqual(formats, ['text/html', 'text/plain']);
        if (mode === 'plain') assert.deepEqual(formats, ['text/plain']);
        if (mode === 'blocked' || mode === 'unsupported') {
            assert.equal(selected, true);
            assert.equal(nodes['sqr-email-plain-fallback'].open, true);
            assert.match(nodes['sqr-email-feedback'].textContent, /approval is still saved/);
        }
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_status_response_renders_badge_and_launches_selected_advisory(self):
        template = (Path(settings.BASE_DIR) / "templates" / "hub" / "sqr.html").read_text(encoding="utf-8")
        display_helpers = template.split("    function renderStatusBadge(", 1)[1].split(
            "    function buildWidget(", 1
        )[0]
        response_handler = template.split("    function applyInlineUpdateResponse(", 1)[1].split(
            "    var undoToastTimer", 1
        )[0]
        script = """
const assert = require('node:assert/strict');
let invalidations = 0;
let choices = 0;
let filterApplications = 0;
let trackerRows = [];
const holdFilterBadge = {textContent: ''};
const createNode = tagName => ({
    tagName: tagName.toUpperCase(), className: '', style: {}, children: [], _text: '',
    set textContent(value) { this._text = String(value); this.children = []; },
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); },
    appendChild(child) { this.children.push(child); },
    setAttribute() {}
});
function containsElement(node, tagName) {
    return node.tagName === tagName || node.children.some(child => containsElement(child, tagName));
}
const LABELS = {status: {
    submitted: 'For Processing', for_revision: 'For Revision',
    reviewed: 'Approved', on_hold: 'On Hold'
}, proposal_status: {
    submitted_pending: 'Submitted', negotiation_review: 'On Review',
    closed_won: 'Closed-Won', closed_lost: 'Closed-Lost', closed_canceled: 'Closed-Canceled'
}, overall_status: {planning: 'Planning'}, delivery_health: {on_track: 'On Track'},
revenue_declaration: {declared: 'Declared'}, revenue_status: {billed: 'Billed'}};
const document = {
    createElement: createNode,
    createTextNode: textContent => Object.assign(createNode('#text'), {textContent}),
    dispatchEvent: () => { invalidations++; },
    querySelector(selector) {
        assert.equal(selector, '[data-sqr-filter="status:on_hold"]');
        return holdFilterBadge;
    },
    querySelectorAll(selector) {
        assert.equal(selector, '.sqr-tracker tbody tr[data-sqr-status="on_hold"]');
        return trackerRows.filter(row => row.dataset.sqrStatus === 'on_hold');
    }
};
const window = {
    location: {href: ''},
    applySqrTableFilter() { filterApplications++; }
};
const CustomEvent = function(type) { this.type = type; };
const setTimeout = () => {};
function openApprovalChoiceModal() { choices++; }
""" + "function renderStatusBadge(" + display_helpers + "function applyInlineUpdateResponse(" + response_handler + """
for (const [status, action, undone, label, url, reason = '', hasControls = true] of [
    ['reviewed', 'draft', false, 'Approved', 'mailto:reviewer@example.com'],
    ['reviewed', 'eml', false, 'Approved', '/sqr/225/approval-email/eml/'],
    ['reviewed', '', false, 'Approved', ''],
    ['submitted', 'draft', true, 'For Processing', ''],
    ['for_revision', '', false, 'For Revision', ''],
    ['on_hold', 'draft', false, 'On Hold', '', 'Customer <scope> & budget pending'],
    ['submitted', 'draft', true, 'For Processing', '', 'Retained previous reason'],
    ['on_hold', 'draft', true, 'On Hold', '', 'Restored hold reason'],
    ['on_hold', '', false, 'On Hold', '', 'Hold without edit controls', false]
]) {
    window.location.href = '';
    const toggles = {};
    const row = {
        dataset: {sqrStatus: 'submitted'},
        classList: {toggle(name, active) { toggles[name] = active; }},
        querySelector: () => null
    };
    trackerRows = [row, {dataset: {sqrStatus: 'on_hold'}}];
    const holdText = {textContent: ''};
    const holdLabel = {textContent: ''};
    const holdDetails = {
        hidden: true,
        querySelector(selector) {
            if (selector === '[data-sqr-hold-text]') return holdText;
            assert.equal(selector, '[data-sqr-hold-label]');
            return holdLabel;
        }
    };
    const editHoldButton = {hidden: true};
    const attributes = {};
    const cell = {
        dataset: {}, classList: {add() {}, remove() {}}, closest: () => row,
        setAttribute(name, value) { attributes[name] = value; },
        querySelector(selector) {
            if (!hasControls) return null;
            if (selector === '[data-sqr-hold-details]') return holdDetails;
            assert.equal(selector, '[data-sqr-edit-hold]');
            return editHoldButton;
        }
    };
    const display = {textContent: '', appendChild(badge) { this.badge = badge; }};
    applyInlineUpdateResponse('225', 'status', 'old', cell, display, {
        field_value: status,
        hold_reason: reason,
        undone,
        approval_email_draft_url: 'mailto:reviewer@example.com',
        approval_email_eml_url: '/sqr/225/approval-email/eml/'
    }, {approvalEmailAction: action});
    assert.equal(cell.dataset.sqrValue, status);
    assert.equal(cell.dataset.sqrHoldReason, reason);
    assert.equal(row.dataset.sqrStatus, status);
    assert.equal(toggles['sqr-row--revision'], status === 'for_revision');
    assert.equal(display.badge.textContent, label);
    assert.equal(attributes['aria-label'], 'SQR Status: ' + label + '. Press Enter to edit.');
    assert.equal(window.location.href, url);
    assert.equal(holdFilterBadge.textContent, 'On Hold: ' + (status === 'on_hold' ? 2 : 1));
    if (hasControls) {
        assert.equal(holdDetails.hidden, !reason);
        assert.equal(holdText.textContent, reason);
        assert.equal(holdLabel.textContent, status === 'on_hold' ? 'Hold Reason:' : 'Previous Hold Reason:');
        assert.equal(editHoldButton.hidden, status !== 'on_hold');
    }
}
assert.equal(choices, 1);
assert.equal(invalidations, 9);
assert.equal(filterApplications, 9);
for (const [field, value, label] of [
    ['proposal_status', 'submitted_pending', 'Submitted'],
    ['proposal_status', 'negotiation_review', 'On Review'],
    ['proposal_status', 'closed_won', 'Closed-Won'],
    ['proposal_status', 'closed_lost', 'Closed-Lost'],
    ['proposal_status', 'closed_canceled', 'Closed-Canceled'],
    ['proposal_status', '', '\\u2013'],
    ['overall_status', 'planning', 'Planning'],
    ['delivery_health', 'on_track', 'On Track'],
    ['revenue_declaration', 'declared', 'Declared'],
    ['revenue_status', 'billed', 'Billed'],
    ['delivery_progress', '60', '60%'],
    ['delivery_progress', '<img src=x onerror=alert(1)>', '\\u2013'],
    ['revenue_remarks', '<img>\\n& note', '<img>\\n& note'],
    ['revenue_overview', '<img>\\n& note', '<img>\\n& note'],
    ['key_updates_risks', '<img>\\n& note', '<img>\\n& note']
]) {
    for (const undone of [false, true]) {
        const row = {dataset: {}, classList: {toggle() {}}, querySelector: () => null};
        const cell = {dataset: {}, classList: {add() {}, remove() {}}, closest: () => row};
        const display = createNode('div');
        applyInlineUpdateResponse('225', field, 'old', cell, display, {field_value: value, undone});
        assert.equal(cell.dataset.sqrValue, value);
        if (['revenue_remarks', 'revenue_overview', 'key_updates_risks'].includes(field)) {
            const renderedText = display.children.map(node => node.tagName === 'BR' ? '\\n' : node.textContent).join('');
            assert.equal(renderedText, label);
        } else {
            assert.ok(display.textContent.includes(label), display.textContent);
        }
        assert.equal(containsElement(display, 'IMG'), false);
        if (field === 'proposal_status') assert.equal(row.dataset.sqrProposalStatus, value);
    }
}
for (const field of ['customer_name', 'project_title', 'revenue_reference_no']) {
    const value = '<img src=x onerror=alert(1)>';
    const cell = {dataset: {}, classList: {add() {}, remove() {}}, closest: () => null};
    const display = {
        textContent: '',
        set innerHTML(value) { assert.fail('Plain text must not be rendered as HTML'); }
    };
    applyInlineUpdateResponse('225', field, value, cell, display, {});
    assert.equal(display.textContent, value);
}
"""
        result = subprocess.run(
            [shutil.which("node"), "-"], input=script, text=True,
            capture_output=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manual_date_response_preserves_editor_wrappers_and_applicability(self):
        template = (Path(settings.BASE_DIR) / "templates" / "hub" / "sqr.html").read_text(encoding="utf-8")
        source = "function applyInlineUpdateResponse(" + template.split(
            "    function applyInlineUpdateResponse(", 1
        )[1].split("    var undoToastTimer", 1)[0]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const classes = () => ({add() {}, remove() {}, toggle() {}});
function dateCell(value) {
    const display = {textContent: value, set innerHTML(value) { throw Error('Date display must be text'); }};
    return {dataset: {}, classList: classes(), display, textContent: value,
        set innerHTML(value) { throw Error('Do not replace the date editor wrapper'); },
        querySelector: () => display, setAttribute() {}};
}
const warranty = dateCell('old warranty'), start = dateCell('old start'), end = dateCell('old end');
warranty.querySelector = () => null;
start.dataset.sqrField = 'managed_support_start_date';
end.dataset.sqrField = 'managed_support_end_date';
const row = {dataset: {}, querySelector(selector) {
    if (selector.includes('post_svc_warranty_end_date')) return warranty;
    if (selector.includes('support_start_date')) return start;
    if (selector.includes('managed_support_end_date')) return end;
    return null;
}};
const cell = {dataset: {}, classList: classes(), closest: () => row};
const context = {window: {}, document: {dispatchEvent() {}}, CustomEvent: function () {},
    setTimeout() {}, getDisplay: (field, value) => String(value)};
vm.runInNewContext(SOURCE, context);
context.applyInlineUpdateResponse('1', 'delivery_actual_finish_date', '2026-11-13', cell, {textContent: ''}, {
    field_value: '2026-11-13', post_svc_warranty_end_date: 'Dec 13, 2026', support_start_date: '',
    managed_support_end_date: 'Feb 09, 2027', warranty_eligible: true, maintenance_eligible: true
});
assert.equal(warranty.textContent, 'Dec 13, 2026');
assert.equal(start.display.textContent, 'N/R');
assert.equal(end.display.textContent, 'Feb 09, 2027');
assert.equal(cell.dataset.sqrValue, '2026-11-13');
context.applyInlineUpdateResponse('1', 'managed_support_amount', '', cell, {textContent: ''}, {
    field_value: '', support_start_date: '', managed_support_end_date: '', maintenance_eligible: false
});
assert.equal(start.display.textContent, 'N/A');
assert.equal(end.display.textContent, 'N/A');
assert.equal(end.dataset.sqrApplicable, 'false');
""".replace("SOURCE", json.dumps(source))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_assessment_invalidates_loaded_ppt_snapshot(self):
        template = (Path(settings.BASE_DIR) / "templates" / "hub" / "sqr.html").read_text(encoding="utf-8")
        handler = "function applyInlineUpdateResponse(" + template.split(
            "    function applyInlineUpdateResponse(", 1
        )[1].split("    var undoToastTimer", 1)[0]
        controller = (Path(settings.BASE_DIR) / "static" / "js" / "sqr-performance.js").read_text(encoding="utf-8")
        script = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
class Element {
    constructor(tag='div') { this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.style={}; this.value=''; this.checked=false; this.hidden=false; this.listeners={}; this.classList={add(){},remove(){},toggle(){}}; }
    set textContent(value) { this.text=String(value); this.children=[]; }
    get textContent() { return (this.text||'')+this.children.map(n=>n.textContent).join(''); }
    append(...items) { for(const n of items) { if(n.tagName==='FRAGMENT') this.children.push(...n.children); else this.children.push(typeof n==='string'?Object.assign(new Element(),{textContent:n}):n); } }
    appendChild(n) { this.append(n); }
    replaceChildren(...items) { this.text=''; this.children=[]; this.append(...items); }
    add(n) { this.append(n); }
    setAttribute() {} removeAttribute() {} querySelector() { return null; }
    querySelectorAll() { return []; } closest() { return null; }
    addEventListener(type,fn) { (this.listeners[type] ||= []).push(fn); }
}
const nodes=new Map(); const root=new Element(); root.dataset={endpoint:'/snapshot',currentUserId:'2',defaultMine:'0'};
function lookup(id) { if(id==='sqr-performance-dashboard') return root; if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id); }
lookup('sqr-pm-scope').value='all';
const listeners={}; const document={hidden:false,getElementById:lookup,createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment'),
    addEventListener(type,fn){(listeners[type] ||= []).push(fn);},dispatchEvent(event){for(const fn of listeners[event.type]||[]) fn(event);}};
const frames=[]; let quality='';
function project() { return {pk:1,reference_code:'SQR-1',project_name:'Fictional project',customer_name:'Fictional account',pm_id:'2',pm_name:'PM',pm_fallback:false,approver_id:'2',sse_name:'SSE',
    status_key:'in_progress',status_label:'In Progress',health_key:'not_set',health_label:'Not set',progress:50,start_date:'2026-11-01',actual_finish_date:null,completion_signed_date:null,target_date:null,
    updates_risks:'',active_delivery:true,delivery_candidate:true,needs_attention:quality!=='met',overdue:false,due_soon:false,days_to_target:null,missing_data:quality?[]:['Quality Performance'],
    inconsistencies:[],reasons:[],priority:5,portfolio_status:'ongoing',overall_rating:quality==='met'?'good':quality?'warning':'unknown',missing_assessment:!quality,search_text:'fictional project',
    indicators:{schedule:{value:'on_track',label:'On Track',state:'good'},budget_effort:{value:'within_budget',label:'Within Budget',state:'good'},quality:{value:quality,label:quality==='met'?'Met':quality?'At Risk':'N/R',state:quality==='met'?'good':quality?'warning':'unknown'}}}; }
const context={document,window:{},Intl,Map,Option:function(label,value){const n=new Element('option');n.textContent=label;n.value=value;return n;},AbortController,
    CustomEvent:function(type,init){this.type=type;this.detail=init?.detail;},setTimeout(){return 1;},clearTimeout(){},requestAnimationFrame(fn){frames.push(fn);return frames.length;},cancelAnimationFrame(){},
    fetch:async()=>({ok:true,redirected:false,json:async()=>({ok:true,as_of:'2026-11-13',fetched_at:'2026-11-13T00:00:00Z',projects:[project()]})}),
    renderStructuredDisplay:(node,field,value)=>{node.textContent=value;},getDisplay:(field,value)=>value};
vm.createContext(context);
vm.runInContext(CONTROLLER,context); vm.runInContext(HANDLER,context);
async function settle(){await new Promise(resolve=>setImmediate(resolve));while(frames.length) frames.shift()();await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
    await settle(); assert.ok(lookup('sqr-pm-indicator-rows').textContent.includes('N/R'));
    quality='met'; context.applyInlineUpdateResponse('1','quality_performance','met',new Element(),new Element(),{field_value:'met'});
    await settle(); assert.ok(lookup('sqr-pm-indicator-rows').textContent.includes('Met'));
    root.hidden=true; quality='at_risk'; context.applyInlineUpdateResponse('1','quality_performance','at_risk',new Element(),new Element(),{field_value:'at_risk'});
    await settle(); assert.ok(lookup('sqr-pm-indicator-rows').textContent.includes('Met'));
    root.hidden=false; document.dispatchEvent(new context.CustomEvent('sqr:performance-view',{detail:{view:'performance'}}));
    await settle(); assert.ok(lookup('sqr-pm-indicator-rows').textContent.includes('At Risk'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("CONTROLLER", json.dumps(controller)).replace("HANDLER", json.dumps(handler))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _run_shared_filter_script(self, assertions):
        source = (Path(settings.BASE_DIR) / "static" / "js" / "sqr-filters.js").read_text(encoding="utf-8")
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
class Element {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.nodeType=1;this.children=[];this.dataset={};this.attributes={};this.listeners={};this.value='';this.checked=false;this.open=false;this.classList={add(){},remove(){},toggle(){}};}
    set textContent(value){this.text=String(value);this.children=[];}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
    append(...items){for(const n of items)this.children.push(typeof n==='string'?Object.assign(new Element(),{textContent:n}):n);}appendChild(n){this.append(n);}
    replaceChildren(...items){this.text='';this.children=[];this.append(...items);}setAttribute(k,v){this.attributes[k]=String(v);if(k==='id')this.id=String(v);if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v);}
    getAttribute(k){return this.attributes[k]??null;}removeAttribute(k){delete this.attributes[k];}addEventListener(k,f){(this.listeners[k]||=[]).push(f);}removeEventListener(k,f){this.listeners[k]=(this.listeners[k]||[]).filter(n=>n!==f);}
    emit(k,event={}){for(const f of this.listeners[k]||[])f({target:this,preventDefault(){},...event});}focus(){document.activeElement=this;}
    walk(){return[this,...this.children.flatMap(n=>n.walk())];}
    querySelectorAll(selector){return this.walk().slice(1).filter(n=>n.tagName==='DETAILS'&&(selector==='details'||n.getAttribute('data-sqr-filter-key')!==null));}
    querySelector(selector){const match=selector.match(/^\[id="([^"]+)"\]$/);return match?this.walk().find(n=>n.id===match[1])||null:null;}
    contains(n){return this.walk().includes(n);}
}
const document={createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),activeElement:null,addEventListener(){},removeEventListener(){}};
const context={document,window:{},URLSearchParams,CustomEvent:function(type,init){this.type=type;this.detail=init?.detail;}};
vm.runInNewContext(SOURCE,context);const filters=context.window.RequestHubSqrFilters;
const find=(root,predicate)=>root.walk().find(predicate);
""".replace("SOURCE", json.dumps(source)) + assertions
        result = subprocess.run([shutil.which("node"), "-"], input=harness, text=True,
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_multi_filter_url_round_trip_and_clear_all(self):
        self._run_shared_filter_script(r"""
const state=filters.read(new URLSearchParams('overall_status=in_progress&overall_status=on_hold&pm=1&pm=2&assigned=all&q=hello&linked=1&amount_min=10&layout=table&focus=7'));
assert.deepEqual(Array.from(state.selected.overall_status),['in_progress','on_hold']);
assert.deepEqual(filters.toQuery(state).getAll('pm'),['1','2']);
assert.equal(filters.read(new URLSearchParams(),{defaultAssigned:'mine'}).assigned,'mine');
assert.equal(filters.toQuery(state).get('assigned'),'all');
assert.equal(filters.toQuery(state).get('layout'),null);assert.equal(filters.toQuery(state).get('focus'),null);
const root=new Element();root.id='filters-test';const mounted=filters.mount(root,{state,options:{overall_status:[{value:'in_progress',label:'In Progress'},{value:'on_hold',label:'On Hold'}],pm:[{value:'1',label:'PM One'},{value:'2',label:'PM Two'}]},onChange(){}});
find(root,n=>n.tagName==='BUTTON'&&n.textContent==='Clear all').emit('click');
const cleared=mounted.getState();assert.equal(cleared.assigned,'all');assert.equal(cleared.q,'');
assert.equal(filters.toQuery(cleared).get('linked'),null);assert.equal(filters.toQuery(cleared).get('amount_min'),null);
assert.deepEqual(filters.toQuery(cleared).getAll('overall_status'),[]);mounted.destroy();
""")

    def test_filter_controls_expose_keyboard_selection_and_result_count(self):
        self._run_shared_filter_script(r"""
const root=new Element();root.id='keyboard-filters';let changed;
const mounted=filters.mount(root,{state:filters.read(new URLSearchParams('assigned=all')),options:{overall_status:[{value:'in_progress',label:'In Progress'},{value:'on_hold',label:'On Hold'}]},onChange(state){changed=state;}});
let check=find(root,n=>n.tagName==='INPUT'&&n.value==='in_progress');assert.equal(check.getAttribute('type'),'checkbox');check.checked=true;check.emit('change');
assert.deepEqual(Array.from(changed.selected.overall_status),['in_progress']);
const details=find(root,n=>n.tagName==='DETAILS');const summary=find(details,n=>n.tagName==='SUMMARY');details.open=true;details.emit('toggle');
assert.equal(summary.getAttribute('aria-expanded'),'true');details.emit('keydown',{key:'Escape'});assert.equal(details.open,false);assert.equal(document.activeElement,summary);
const live=find(root,n=>n.getAttribute('data-sqr-filter-count')!==null);assert.equal(live.getAttribute('aria-live'),'polite');
mounted.setCount(55);assert.ok(live.textContent.includes('55'));
const remove=find(root,n=>n.tagName==='BUTTON'&&(n.getAttribute('aria-label')||'').startsWith('Remove Delivery Status:'));
remove.emit('click');assert.deepEqual(Array.from(mounted.getState().selected.overall_status),[]);
""")

    def test_filter_dropdowns_close_other_category_panels(self):
        self._run_shared_filter_script(r"""
const root=new Element();root.id='overlapping-filters';
root.querySelectorAll=()=>root.walk().filter(n=>n.tagName==='DETAILS'&&n.getAttribute('data-sqr-filter-key')!==null);
filters.mount(root,{state:filters.read(new URLSearchParams('assigned=all')),options:{overall_status:[{value:'in_progress',label:'In Progress'}],pm:[{value:'1',label:'PM One'}]},onChange(){}});
const first=find(root,n=>n.getAttribute('data-sqr-filter-key')==='overall_status');
const second=find(root,n=>n.getAttribute('data-sqr-filter-key')==='pm');
first.open=true;first.emit('toggle');second.open=true;second.emit('toggle');
assert.equal(first.open,false,'Opening another category must close the older panel rather than cover chips after Escape');
second.emit('keydown',{key:'Escape'});assert.equal(second.open,false);
""")

    def test_detached_filter_toggle_cannot_close_replacement_panel(self):
        self._run_shared_filter_script(r"""
const root=new Element();root.id='queued-toggle';
const mounted=filters.mount(root,{state:filters.read(new URLSearchParams('assigned=all')),options:{quality_performance:[{value:'met',label:'Met'}]},onChange(){}});
const old=find(root,n=>n.getAttribute('data-sqr-filter-key')==='quality_performance');old.open=true;old.emit('toggle');
mounted.setState(mounted.getState());
const current=find(root,n=>n.getAttribute('data-sqr-filter-key')==='quality_performance');
old.emit('toggle');
assert.equal(current.open,true,'A queued toggle from detached markup must not close the current checkbox panel');
""")

    def test_filter_disclosure_focus_survives_latest_option_refresh(self):
        self._run_shared_filter_script(r"""
const root=new Element();root.id='refresh-focus';const options={quality_performance:[{value:'met',label:'Met'}]};
const mounted=filters.mount(root,{state:filters.read(new URLSearchParams('assigned=all')),options,onChange(){}});
const details=find(root,n=>n.getAttribute('data-sqr-filter-key')==='quality_performance');
details.open=true;details.emit('toggle');details.emit('keydown',{key:'Escape'});
mounted.setOptions(options);
assert.ok(root.contains(document.activeElement),'A late response must not remove the keyboard focus returned by Escape');
assert.equal(document.activeElement.getAttribute('aria-expanded'),'false');
""")

    def test_more_filter_selection_preserves_open_panel_and_keyboard_focus(self):
        self._run_shared_filter_script(r"""
const root=new Element();root.id='more-focus';
const mounted=filters.mount(root,{state:filters.read(new URLSearchParams('assigned=all')),options:{},onChange(){}});
let more=find(root,n=>n.tagName==='DETAILS'&&n.getAttribute('class')==='sqr-filter-more');more.open=true;
const linked=find(more,n=>n.tagName==='INPUT'&&n.value==='linked');linked.focus();linked.checked=true;linked.emit('change');
more=find(root,n=>n.tagName==='DETAILS'&&n.getAttribute('class')==='sqr-filter-more');
assert.equal(more.open,true,'Editing an extra predicate must not close its panel');
assert.ok(root.contains(document.activeElement),'Checkbox focus must survive applying its predicate');
assert.equal(mounted.getState().flags.linked,true);
more.emit('keydown',{key:'Escape'});assert.equal(more.open,false);
assert.equal(document.activeElement.tagName,'SUMMARY');
""")

    def test_classic_saved_filters_are_canonicalized_before_server_reload(self):
        controller = (Path(settings.BASE_DIR) / "static" / "js" / "sqr-classic-filters.js").read_text(encoding="utf-8")
        self._run_shared_filter_script(r"""
const location=new URL('https://fixture.invalid/sqr/?view=tracker');
location.assign=value=>{location.href=new URL(value,location).href;};
context.window.location=location;context.window.history={replaceState(_a,_b,value){location.href=new URL(value,location).href;}};
const saved={selected:{overall_status:['in_progress','on_hold']},q:'Multi-filter',assigned:'all',flags:{},ranges:{}};
context.window.sessionStorage={getItem(key){return key==='requestHub.sqr.filters.v1'?JSON.stringify(saved):null;},setItem(){}};
const root=new Element();root.id='sqr-classic-filters';root.dataset={defaultAssigned:'all',serverQuery:'view=tracker',initialCount:'56',optionsId:'filter-options'};
root.closest=()=>({hidden:false,querySelector(){return null;}});
const options=new Element();options.textContent='{}';
document.getElementById=id=>id===root.id?root:id==='filter-options'?options:null;
document.querySelectorAll=()=>[];const listeners={};
document.addEventListener=(name,fn)=>{(listeners[name]||=[]).push(fn);};
document.dispatchEvent=event=>{for(const fn of listeners[event.type]||[])fn(event);};
context.fetch=()=>Promise.resolve({ok:true,json:async()=>({ok:true,pipeline:{all:{count:55}}})});
vm.runInNewContext(CLASSIC,context);
assert.equal(location.searchParams.get('q'),'Multi-filter','Saved predicates must enter the URL before reloading server-rendered rows');
assert.deepEqual(location.searchParams.getAll('overall_status'),['in_progress','on_hold']);
""".replace("CLASSIC", json.dumps(controller)))

    def test_retired_filter_preference_is_not_silently_applied(self):
        self._run_shared_filter_script(r"""
const state=filters.read(new URLSearchParams('assigned=all&attention=overdue&overall_status=invalid'));
assert.deepEqual(Array.from(state.selected.overall_status),['invalid']);
assert.equal(filters.toQuery(state).get('attention'),'overdue');
const root=new Element();root.id='invalid-filters';filters.mount(root,{state,options:{overall_status:[{value:'in_progress',label:'In Progress'}]},onChange(){}});
assert.ok(/retired|unavailable/i.test(root.textContent));assert.ok(root.textContent.includes('Clear all'));
""")

    def test_stale_response_cannot_replace_newer_filter_results(self):
        source = (Path(settings.BASE_DIR) / "static" / "js" / "sqr-performance.js").read_text(encoding="utf-8")
        refresh = "async function refresh(" + source.split("    async function refresh(", 1)[1]
        refresh = refresh.split("    function invalidateSnapshot", 1)[0].split("    Object.keys(controls)", 1)[0]
        if "    function invalidateSnapshot(" in source:
            invalidator = "function invalidateSnapshot(" + source.split("    function invalidateSnapshot(", 1)[1].split("    Object.keys(controls)", 1)[0]
        else:
            invalidator = "function invalidateSnapshot() {" + source.split("document.addEventListener('sqr:performance-invalidated', function () {", 1)[1].split("    });", 1)[0] + "}"
        script = r"""
const assert=require('node:assert/strict'), vm=require('node:vm');
const requests=[];
const context={AbortController,setTimeout(){return 1;},clearTimeout(){},fetch:()=>new Promise(resolve=>requests.push(resolve))};
vm.createContext(context);
vm.runInContext(`var root={dataset:{endpoint:'/snapshot'},setAttribute(){}};
var snapshot=null,revision=0,inFlight=null,activeController=null,sharedFilters=null,filterState=null,dirty=true;
function visible(){return true;}function message(){}function updateManagers(){}function scheduleRender(){}
function el(){return {replaceChildren(){},append(){},dataset:{}};}`,context);
vm.runInContext(REFRESH+INVALIDATOR,context);
function response(name){return{ok:true,redirected:false,json:async()=>({ok:true,as_of:'2026-11-13',fetched_at:'2026-11-13T00:00:00Z',projects:[{name}]})};}
(async()=>{
    context.refresh();context.invalidateSnapshot();
    assert.equal(requests.length,2,'A new filter selection must start its own request, not wait for the superseded one');
    requests[1](response('latest selection'));await new Promise(resolve=>setImmediate(resolve));
    requests[0](response('superseded selection'));await new Promise(resolve=>setImmediate(resolve));
    assert.equal(vm.runInContext('snapshot.projects[0].name',context),'latest selection');
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("REFRESH", json.dumps(refresh)).replace("INVALIDATOR", json.dumps(invalidator))
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
