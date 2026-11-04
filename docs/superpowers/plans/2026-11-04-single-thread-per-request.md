# Single Email Thread per Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Draft for review. No application behavior has been changed. Mailbox, sender, and recipient decisions below require approval before implementation.

**Goal:** Keep assignment, acknowledgement, reassignment, and closing emails for a request in one traceable conversation.

**Architecture:** Establish one original assignment message per request in an approved Microsoft 365 team mailbox. Persist its identity and create subsequent messages as genuine replies using Microsoft Graph, instead of searching each engineer's mailbox by subject. Preserve automatic assignment notices and human-reviewed acknowledgement/closing drafts.

**Tech Stack:** Existing Django/PostgreSQL application, MSAL, Microsoft Graph, and Outlook. No new framework or package is proposed.

**Spec:** The user requirement is "from the email assignment to the closing must be replying on the same thread." The proposed requirements and decisions in this document are the draft specification, not approved requirements.

## 1. Intended experience

Example subject: `[Request Hub] REQ-12345 | Account Name`

```text
Assignment: one original message
  -> Acknowledgement: reply in the same conversation
  -> Follow-up discussion: Reply All in that conversation
  -> Reassignment, if any: reply in that conversation
  -> Closing: reply in that conversation
```

Keep the subject fixed after the first email. Put "Assigned", "Acknowledged", "Reassigned", or "Completed" in the message body, not in new subject lines. Outlook may add its normal `Re:` prefix.

The requestor, assigned engineer, backup engineer, and approved team recipients can follow the conversation without searching for separate emails. An engineer handover must not create another thread.

## 2. Current behavior verified in the repository

| Surface | Current behavior | Gap |
|---|---|---|
| [Assignment email](../../../hub/views.py) (`notify_engineer_assignment_email`) | ACS sends a new message to newly assigned engineers/backups with subject `[Request Hub] New assignment - <reference>`. | Requestor is not included; reassignment starts another message. |
| [Request thread service](../../../hub/services/outlook_threads.py) | Acknowledgement searches the signed-in mailbox for an assignment subject. Closing searches for a sent acknowledgement, then assignment. | Subject-based discovery is not a durable request-to-message association. Another engineer may not have the same messages. |
| [Reply creation](../../../hub/services/outlook_threads.py) | Uses Graph `createReply`, then changes subject to `Re: <reference> <account>`. | A changed subject is an avoidable conversation-grouping risk; matching subject alone is not proof of threading. |
| [Draft views](../../../hub/views.py) | Missing anchors create new messages; Graph failure can open a `mailto:` compose window. | These paths explicitly permit separate threads. |
| [Communication records](../../../hub/models.py) | `RequestCommunication` records a launch, user, channel, and timestamp. | No root message ID, mailbox identity, draft ID, or confirmed send state. |
| [Existing SQR draft service](../../../hub/services/outlook_drafts.py) | Persists ownership before Graph writes and reconciles uncertain creation using a correlation marker. | Useful prior art for duplicate prevention; reuse its pattern without changing SQR behavior. |

Folder moves within a mailbox are not inherently fatal: existing Graph helpers already request immutable IDs. The main weaknesses are subject-only lookup, mailbox dependence, lack of durable association, and new-message fallbacks.

Status-log updates and the current [SLA command](../../../hub/management/commands/check_sla.py) create in-app notifications, not lifecycle emails. This plan does not add automatic emails for those actions.

## 3. Approaches and recommendation

| Approach | Benefits | Trade-off |
|---|---|---|
| Harden the existing personal-mailbox flow | Smaller change; keep engineer-sent emails and ACS assignment. | Still depends on each actor receiving/retaining an anchor and connecting the right mailbox. Reliable handovers require per-mailbox message mapping. |
| **Use one team mailbox for the request lifecycle** | Stable place for the original message, replies, drafts, and Sent Items; independent of the current engineer. | Requires mailbox permissions and approval to send lifecycle emails from the team address. |

**Recommendation: team mailbox.** It best supports tracking from assignment through closing, including engineer changes. Merely making subjects match is not a complete solution.

Proposed mailbox: the existing ESG Request Hub address, **only after confirming it is a usable Exchange Online mailbox**, not merely an alias or distribution list.

### Decisions requiring confirmation

1. **Recipients from assignment:** Recommend including the requestor and all current responsible engineers from the first email. This changes today's engineer-only assignment notice. Confirm the assignment content is suitable for that audience.
2. **Sender:** Recommend sending from the approved team mailbox, with the acting engineer identified in the body/signature. This changes acknowledgement/closing from personal sender to team sender.
3. **Additional CC rules:** Preserve currently required business CC rules, including the ESS-specific rule, subject to confirming whether they also apply to assignment.
4. **SQR scope:** Recommend keeping SQR submission/revision/approval and internal approval conversations separate initially. Their recipients and potentially sensitive contents differ. Including them needs an explicit audience decision, not automatic Reply All.

These are proposals, not permission to change recipients, provision a mailbox, grant access, or send messages.

## 4. Global constraints

- One original lifecycle message per enrolled request; subsequent lifecycle messages must reference an existing, confirmed sent message.
- Freeze the subject at thread creation, including the account name snapshot. Renaming an account must not rename an active conversation.
- Use Graph reply operations; do not simulate replies by adding `Re:` or assigning a `conversationId`.
- Store Graph IDs with their tenant and mailbox identity. IDs are case-sensitive and mailbox-specific; a recipient's copy is not the same Graph item.
- Request immutable IDs on every relevant Graph call. Moving to an archive mailbox or re-importing an item can still change its identity.
- Automatic sends remain limited to assignment/reassignment. Acknowledgement and closing remain editable drafts that a person sends in Outlook.
- Existing authorization, assignment revision checks, completion prerequisites, lifecycle timing, and in-app notifications remain intact.
- Missing anchors, expired permissions, uncertain sends, and provider failures must be visible. Never silently create another root or open an unthreaded compose window for enrolled requests.
- Draft creation, business acknowledgement, confirmed sending, and recipient delivery are distinct states. A Graph `202 Accepted` response is not proof of sending or delivery.
- No new automatic email for every status change, no inbox-to-status automation, no historical mailbox rewrite, and no broad email-platform migration.
- Client conversation display is not fully controlled by Request Hub. Verify real replies and supported Outlook grouping; do not promise that arbitrary clients, forwards, or manually changed subjects will always group.

## 5. Detailed behavior

### Assignment and recipients

- The first assignment creates and sends the root through Graph in the team mailbox, replacing ACS only for this enrolled request's lifecycle messages.
- Proposed To: requestor and assigned engineer. Proposed CC: backup and approved business CC recipients. The team mailbox retains its own Sent Items copy and receives replies; it need not CC itself.
- Normalize/deduplicate addresses across To/CC. Validate mandatory addresses before sending. Do not copy every historical recipient indiscriminately.
- Reassignment sends a reply with the new current participants. Remove previous assignees from future messages unless they retain an approved role; previously delivered messages cannot be revoked.
- Reuse existing escaped HTML/plain-text assignment content, adjusting wording for the approved audience.

### Acknowledgement, discussion, and closing

- Create acknowledgement and closing with `createReplyAll` against the latest known confirmed sent lifecycle message, falling back only to another verified sent message in the same thread.
- Apply the current authorized recipient policy to the reply draft; never rely blindly on the original Reply All list.
- Keep the generated subject and reply metadata. Preserve quoted conversation context when inserting the new content.
- Reopening an action returns the existing editable draft without overwriting human edits. If recipients/assignment changed, require explicit review before reusing a stale draft.
- Follow-up discussion uses Outlook Reply All. Incoming mailbox synchronization is not required for this first release; replying to the latest known lifecycle message still belongs to the same conversation.
- Preserve the current rule that an unsent acknowledgement draft blocks closing until sent or deliberately deleted. Deletion does not count as a sent acknowledgement.
- Completing a request still follows existing business rules. If closing draft creation fails, keep the completed state and show that the closing email remains pending; provide a safe retry.
- Manual removal of the team mailbox or changing the subject can break central tracking. Document this limitation and instruct users to use Reply All.

### Failure and duplicate handling

- Persist an operation and claim ownership before Graph writes. Use the existing SQR correlation/reconciliation approach as prior art.
- A timeout or ambiguous server response means "unconfirmed", not "failed, send again". Reconcile by stored ID/correlation marker before any retry.
- For a reply whose create result is unknown before a marker can be attached, do not blindly create another reply. Require verified reconciliation or explicit operator recovery.
- Serialize competing operations per request; use unique database keys for root creation and each business email event. Double-clicks and concurrent users must not produce duplicate roots or duplicate drafts.
- Respect Graph throttling/retry guidance. Keep bounded retries and safe error messages; do not log tokens or message bodies.
- If an item is missing, resolve within the recorded mailbox using the stored Internet Message-ID and known thread metadata. Ambiguous or zero matches block the action; subject search alone never chooses the anchor.

## 6. Implementation tasks

Execute only after the decisions above are approved. For each task: add the stated failing tests, run the focused test module, implement the smallest change, and rerun it before proceeding.

### Task 1: Persist thread and operation identity

**Files:** [hub/models.py](../../../hub/models.py), a generated migration under [hub/migrations](../../../hub/migrations), [accounts/recovery.py](../../../accounts/recovery.py), [accounts/test_recovery.py](../../../accounts/test_recovery.py), new `hub/test_request_email_thread_state.py`.

**Proposed interfaces/data:**
- `RequestEmailThread`: one-to-one request, stable correlation UUID, tenant/mailbox identity, frozen subject, root immutable ID, root Internet Message-ID, mailbox-local conversation ID, last confirmed sent message ID, verification state, timestamps.
- `RequestEmailOperation`: thread, unique event key, actor, kind, assignment/lifecycle revision, correlation UUID, draft/message ID, recipient snapshot, state, safe error code, timestamps.
- Event keys identify a business event, not an HTTP attempt: root assignment, each reassignment revision, acknowledgement per assignment revision, and closing per completion lifecycle event.
- States distinguish `pending`, `draft`, `send_pending`, `sent`, `failed`, and `unconfirmed`. Only provider verification moves a message to `sent`.

- [ ] Add tests proving one thread per request, one operation per event, and separate events for reassignment and reopening/completing again.
- [ ] Implement fields, constraints, and migration; do not repurpose `RequestCommunication` as evidence that an email was sent.
- [ ] Explicitly cover new models in the recovery schema guard. Preserve durable thread identity in backups; exclude transient draft/operation state and all credentials.
- [ ] Add restore tests: restored thread pointers are unverified until reconciled, missing operation records do not trigger resends, and existing Outlook credential/draft exclusions remain intact.

### Task 2: Add the mailbox-scoped Graph transport

**Files:** New `hub/services/request_email_mailbox.py`, new `hub/test_request_email_mailbox.py`, [request_hub/settings.py](../../../request_hub/settings.py), environment-variable documentation.

**Interfaces:** `create_root(thread, operation, *, to, cc, body) -> MessageIdentity`; `create_reply(thread, operation, *, anchor, to, cc, body) -> MessageIdentity`; `send_assignment(operation) -> None`; `reconcile(operation) -> MessageState`. Define `MessageIdentity` and `MessageState` in this service with mailbox, message ID, draft/sent state, and validated Outlook URL as appropriate.

- [ ] Add tests for `/users/{configured-mailbox}/messages`, immutable-ID headers, actual reply creation, unchanged subject, quoted content, and validated links.
- [ ] Add tests for HTTP 401/403/429, timeout after creation/send, malformed responses, delayed Sent Items visibility, and duplicate concurrent requests.
- [ ] Implement dedicated application authentication for automatic assignment and shared drafts. Require mailbox-scoped read/write/send authorization approved by the Microsoft 365 administrator; test access is denied to an unrelated mailbox.
- [ ] Keep existing `SQR_OUTLOOK_*` delegated permissions and directory-import credentials unchanged. Do not reuse the personal `/me` endpoint or broaden the directory-import app's permissions.
- [ ] Use existing MSAL/requests and safe-link/error patterns. Reuse narrowly scoped helpers where suitable, without changing unrelated SQR semantics.

**Operational prerequisite:** Authorized staff need access to the shared Drafts folder and the appropriate Send As permission in Outlook. Prove draft links open and send from that mailbox in the actual tenant before enabling the feature. Retain sent copies centrally.

### Task 3: Make assignment and reassignment use the recorded thread

**Files:** [hub/services/outlook_threads.py](../../../hub/services/outlook_threads.py), [hub/views.py](../../../hub/views.py), [hub/test_engineer_emails.py](../../../hub/test_engineer_emails.py), [hub/test_notification_urls.py](../../../hub/test_notification_urls.py).

**Interfaces:** Preserve `notify_engineer_assignment_email(...) -> AssignmentEmailResult` at its existing call sites. Add explicit pending/unconfirmed outcomes and matching feedback. The thread service owns subject creation and the shared recipient policy.

- [ ] Add tests: first assignment creates exactly one root; reassignment creates a reply; unchanged assignees trigger no new email; account rename leaves subject unchanged.
- [ ] Add tests for the approved To/CC policy, missing required email, duplicate addresses, old-assignee removal, and HTML escaping.
- [ ] Route all three existing assignment call sites through the same thread-aware service. Keep in-app assignment notifications unchanged.
- [ ] Commit durable intent with the assignment transaction and dispatch after commit. On send failure, retain a retriable operation and show feedback; never report successful email delivery just because assignment saved.
- [ ] Ensure request lifecycle messages use either the enrolled Graph path or the legacy path, never both.

### Task 4: Connect acknowledgement and closing to the same thread

**Files:** [hub/services/outlook_threads.py](../../../hub/services/outlook_threads.py), [hub/views.py](../../../hub/views.py), [hub/services/request_lifecycle.py](../../../hub/services/request_lifecycle.py), [hub/test_request_outlook_threads.py](../../../hub/test_request_outlook_threads.py), [hub/test_lifecycle.py](../../../hub/test_lifecycle.py), [hub/tests.py](../../../hub/tests.py).

**Interfaces:** Keep the existing request action URLs. Replace `(link, anchored)` for enrolled requests with a result containing the persisted operation and confirmed draft state; errors must not return a new-message fallback.

- [ ] Replace tests that currently expect new roots or `mailto:` fallback with explicit blocked/pending behavior for enrolled requests; keep legacy behavior tested for unenrolled requests.
- [ ] Add tests: primary, backup, and authorized administrator use the same mailbox anchor; unrelated engineers remain denied; closing requires completion.
- [ ] Add tests for repeated clicks, an unsent acknowledgement, a deleted draft, an already-sent draft, changed assignment, missing anchor, and provider outage.
- [ ] Wire both direct Outlook actions and lifecycle acknowledgement/completion actions into this path. Do not require the personal-mailbox sign-in flow for enrolled requests.
- [ ] Preserve acknowledgement/lifecycle timing while separating business acknowledgement from email delivery. A failed draft remains recoverable even if the request has already been acknowledged.

### Task 5: Make tracking and recovery clear in existing screens

**Files:** [hub/views.py](../../../hub/views.py), [templates/hub/request_manager_form.html](../../../templates/hub/request_manager_form.html), [templates/hub/partials/dashboard_admin_rows.html](../../../templates/hub/partials/dashboard_admin_rows.html), request-thread and lifecycle tests.

- [ ] Test distinct labels for business acknowledgement, "Draft ready", "Awaiting send confirmation", "Sent", and "Email needs attention".
- [ ] Offer "Open request email thread" and "Open existing draft" only when a validated link and authorization exist. Do not claim a draft was sent.
- [ ] Preserve existing button availability and business rules while allowing draft recovery; do not disable all access merely because a communication-launch record exists.
- [ ] Surface permission, missing-anchor, and reconciliation failures with a safe recovery action, not an unthreaded compose fallback.
- [ ] Avoid a new email dashboard or storing/rendering entire mailbox contents in Request Hub.

### Task 6: Pilot, legacy adoption, and documentation

**Files:** [README.md](../../../README.md), configuration documentation, existing request-thread tests; migration/recovery tests where applicable.

- [ ] Document mailbox setup, approved audiences, Send As/shared Drafts access, manual Reply All guidance, draft versus sent state, and operator recovery.
- [ ] Add `REQUEST_EMAIL_THREADING_ENABLED`, default off, to enroll new requests only after mailbox validation. Persist enrollment in the thread record.
- [ ] Test that disabling new enrollment does not move already enrolled requests back to ACS/`mailto:`. An emergency pause must stop their email operations visibly, not split their thread.
- [ ] Pilot new requests with approved test recipients before wider enablement.
- [ ] Adopt existing requests only by explicitly verifying a chosen message in the team mailbox and storing its real identity and original subject. Do not send migration emails automatically or claim old scattered messages have been merged.
- [ ] When an existing request has no usable anchor, require an explicit, recorded decision to start a replacement conversation. This is a visible legacy exception, not ordinary fallback behavior.

## 7. Review focus and acceptance criteria

| Scenario | Required result | Coverage |
|---|---|---|
| Two simultaneous assignment sends or repeated clicks | One root and one operation per business event; no blind retry after an uncertain write. | Tasks 1-3 |
| Different closer, reassignment, stale draft recipients | Same recorded thread; correct current audience; no accidental disclosure to a removed participant. | Tasks 3-4 |
| Folder move, missing/deleted anchor, archive move | Normal same-mailbox moves work; unresolved or ambiguous identity blocks safely. | Tasks 2, 4 |
| Draft opened but never sent, timeout, delayed Sent Items | No false "Sent" label and no accidental duplicate; user can recover safely. | Tasks 2, 4-5 |
| Backup restore, feature rollback, legacy adoption | No automatic resend, new root, or hidden return to unthreaded fallback. | Tasks 1, 6 |

**Automated validation:** Use disposable PostgreSQL and test-only settings as described in the README. Mock Graph/ACS; do not use live production mail or credentials.

```powershell
python manage.py makemigrations --check --dry-run
python manage.py test hub.test_request_email_thread_state hub.test_request_email_mailbox hub.test_request_outlook_threads hub.test_engineer_emails hub.test_notification_urls hub.test_lifecycle hub.tests.OutlookThreadingTests accounts.test_recovery --settings=request_hub.settings_ci --noinput
```

The two new test modules in this command are implementation deliverables; they do not exist yet. Run focused modules per task and the combined command after integration.

**Real-mailbox acceptance test:** With approved test mailboxes, perform assignment -> acknowledgement -> manual Reply All -> reassignment -> acknowledgement by the new engineer -> completion/closing. Verify:

1. Exactly one original lifecycle email was created for the request.
2. Every subsequent lifecycle email is a genuine reply; inspect message headers as well as subject. `In-Reply-To`/`References` must connect it to the conversation.
3. The team mailbox shows one conversation and contains the sent lifecycle history; initial participants see the expected grouping in supported Outlook clients.
4. Subject changes are not introduced by the app. A new assignee joins the existing thread; do not promise retroactive delivery of earlier messages they never received.
5. The final message contains the closing outcome and request link, while the actual business completion status remains accurate.
6. Retry, outage, permission denial, unsent acknowledgement, and changed-engineer cases create zero extra root messages.

Only claim end-to-end threading after this mailbox test; mocked HTTP calls alone do not prove Outlook's behavior.

## 8. Provider references

- [Microsoft Graph: createReplyAll](https://learn.microsoft.com/en-us/graph/api/message-createreplyall?view=graph-rest-1.0)
- [Microsoft Graph: immutable IDs and sent-message lookup](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)
- [Microsoft Graph: sending a saved draft](https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0)
- [Microsoft Graph: shared and delegated mail folders](https://learn.microsoft.com/en-us/graph/outlook-share-messages-folders)

**Next step:** Review the four decisions in section 3, especially requestor inclusion from assignment and the team-mailbox sender. Approve or revise this draft before implementation.
