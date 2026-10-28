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
    for (const mode of ['success', 'denied', 'unsupported', 'fetch-failed', 'cancel']) {
        const events = [];
        const window = {location: {href: ''}};
        const notice = {setAttribute() {}, appendChild() {}};
        const clipboard = {async write(items) {
            events.push('write-start');
            if (mode === 'denied') throw new Error('Denied');
            const html = await items[0].data['text/html'];
            const text = await items[0].data['text/plain'];
            assert.equal(await html.text(), '<table>Approved</table>');
            assert.equal(await text.text(), 'Approved');
            events.push('copied');
        }};
        vm.runInNewContext(SOURCE, {
            window,
            navigator: mode === 'unsupported' ? {} : {clipboard},
            ClipboardItem: class {constructor(data) {this.data = data;}},
            Blob,
            document: {getElementById: () => null, createElement: () => notice, querySelector: () => ({prepend() {}})},
            DOMParser: class {parseFromString() {return {getElementById(id) {
                if (id === 'sqr-email-html') return {textContent: JSON.stringify('<table>Approved</table>')};
                if (id === 'sqr-email-text') return {textContent: JSON.stringify('Approved')};
                return {getAttribute: () => 'mailto:reviewer@example.com?subject=Approved'};
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
            assert.equal(window.location.href, mode === 'success' ? 'mailto:reviewer@example.com?subject=Approved' : '/preview/');
            if (mode === 'success') assert.deepEqual(events, ['write-start', 'fetch', 'copied']);
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
                return {getAttribute: () => 'mailto:reviewer@example.com?subject=Approved'};
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
const LABELS = {status: {
    submitted: 'For Processing', for_revision: 'For Revision',
    reviewed: 'Approved', on_hold: 'On Hold'
}};
const document = {
    createElement: () => ({className: '', textContent: ''}),
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
    ['revenue_remarks', '<img>\\n& note', '&lt;img&gt;<br>&amp; note'],
    ['revenue_overview', '<img>\\n& note', '&lt;img&gt;<br>&amp; note'],
    ['key_updates_risks', '<img>\\n& note', '&lt;img&gt;<br>&amp; note']
]) {
    for (const undone of [false, true]) {
        const row = {dataset: {}, classList: {toggle() {}}, querySelector: () => null};
        const cell = {dataset: {}, classList: {add() {}, remove() {}}, closest: () => row};
        const display = {
            innerHTML: '',
            set textContent(value) { assert.fail('Formatted display was assigned as literal text'); }
        };
        applyInlineUpdateResponse('225', field, 'old', cell, display, {field_value: value, undone});
        assert.equal(cell.dataset.sqrValue, value);
        assert.ok(display.innerHTML.includes(label), display.innerHTML);
        assert.ok(!display.innerHTML.includes('<img'), display.innerHTML);
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
