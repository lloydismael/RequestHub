# Request Hub Chatbot-Only Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for
> native implementation, or superpowers:subagent-driven-development if the user
> explicitly selects delegated execution. Steps use checkbox (`- [ ]`) tracking.
> This document is a plan, not authorization to start implementation.

**Goal:** Replace only the content of the opened chat popup with a native,
Foundry-ready assistant for application guidance, navigation, and cross-department
knowledge, without redesigning Request Hub or its closed chat head.

**Architecture:** Keep the existing floating widget shell and mount a scoped native
chat component in place of its iframe behind a rollout flag. Authenticated Django
endpoints expose approved application guides and a minimal live member directory.
A bounded read-only tool loop connects those sources to the existing Foundry
deployment; no additional Azure service or frontend framework is required.

**Tech Stack:** Existing Django, PostgreSQL, templates, vanilla JavaScript,
Bootstrap Icons, and semantic application colors; supported Foundry inference SDK
and Azure Identity only when adding the provider integration.

**Spec:** [Approved popup-only design](../specs/2026-11-05-request-hub-assistant-design.md).
The corrected popup-only mockup is the visual reference; the initial docked-panel
and sample-dashboard mockup is superseded.

## Global Constraints

- Redesign only the inside of the opened chat window.
- Keep existing pages, navigation, tables, forms, layout, and closed launcher unchanged.
- No docking, page reflow, standalone assistant page, or department-management screen.
- Retain the current widget placement, desktop bounds, mobile breakpoints, and opt-in.
- `User.show_chatbot` remains false by default; respect active authentication.
- Read-only assistance; destinations open only after an explicit user click.
- Directory fields: display name, department, and labeled application role only.
- Directory membership: active Request Hub accounts across departments; no Graph sync.
- Missing names become "Unnamed member"; never substitute login identifiers or email.
- Department descriptions are organization-approved source content, not inferred facts.
- Message length: 4,000 characters; total submitted request body: 32 KiB.
- Conversation: at most 12 user/assistant exchanges in the current server-side session.
- At most three tool rounds, five calls total, and 60 directory rows sent to the model.
- Total model operation deadline: 45 seconds.
- One in-flight model request per user; 20 attempts per user per 10-minute window.
- Global capacity ceiling: initially two concurrent model operations across all workers.
- Session-only memory, plain JSON responses, truthful pending/error states, no fake streaming.
- No secrets, raw chat content, or directory payloads in routine logs.
- No automatic provisioning, paid-resource creation, model changes, or live inference.
- Test against disposable PostgreSQL, never the existing live database or live `.env`.
- Add only required SDK dependencies and keep `requirements.txt` / `requirements.lock`
  compatible; preserve the repository's current direct-pin lockfile format.

## Review Focus

1. Opening/closing chat on wide tables must not shift the underlying page or launcher;
   verify bounding boxes and screenshots in Task 6.
2. A role change, logout, second tab, or New chat during inference must not resurrect
   an obsolete conversation; exercise stale-generation behavior in Tasks 3 and 5.
3. Missing names, duplicate department casing, and names containing markup must not
   leak login identifiers, fabricate membership, or become HTML; test in Tasks 2 and 6.
4. A structurally plausible model reply with forged sources, URLs, or nested tool
   instructions must not bypass server authority; test in Task 5.
5. Worker failure or multiple workers must not evade quotas or permanently lock chat;
   use PostgreSQL concurrency and expired-lease tests in Task 3.

## File structure and ownership

| File | Responsibility |
|---|---|
| `hub/assistant_types.py` (new) | Typed public DTOs and provider/tool boundary types |
| `hub/assistant_knowledge.py` (new) | Verified guides, named-route registry, approved department catalog |
| `hub/assistant_directory.py` (new) | Read-only account projection and normalized department queries |
| `hub/assistant_runtime.py` (new) | Database-backed quotas/leases and session-state lifecycle |
| `hub/assistant_provider.py` (new) | Foundry settings validation and bounded SDK calls |
| `hub/assistant_service.py` (new) | Read-only tool dispatch, orchestration, source/card validation |
| `hub/assistant_views.py` (new) | Authenticated JSON endpoints and structured HTTP errors |
| `hub/context_processors.py` | Chat-only flag, safe same-origin API URLs, allowlisted page context |
| `accounts/middleware.py` | Preserve forced-password policy with JSON errors for assistant APIs only |
| `hub/models.py` | One transient `AssistantUsageLimit` model; no department/member remodel |
| `hub/migrations/0058_assistant_usage_limit.py` (generated) | Quota/lease schema; regenerate numbering if the migration head changes |
| `hub/urls.py` | Assistant API routes only; no new application page |
| `accounts/recovery.py`, `accounts/test_recovery.py` | Explicit transient-model exclusion and recovery regression |
| `templates/includes/assistant_widget.html` (new) | Native contents mounted within the existing chat body |
| `templates/base.html` | Feature-gated native/legacy mounting and scoped asset inclusion |
| `static/js/assistant.js`, `static/css/assistant.css` (new) | Native chat behavior and styles scoped to the widget |
| `request_hub/settings.py`, `.env.example` | Safe disabled-by-default provider/rollout configuration |
| `hub/test_assistant_knowledge.py` (new) | Guide permissions, directory fields, catalog validation |
| `hub/test_assistant_runtime.py` (new) | PostgreSQL leases, quotas, state isolation, stale generations |
| `hub/test_assistant_chat.py` (new) | Provider, tool loop, and API error/permission contracts |
| `hub/test_assistant_widget.py` (new) | Template branch and Node-based JavaScript regressions |
| `requirements.txt`, `requirements.lock` | Only supported provider/identity dependencies |
| `README.md` | Configuration, content maintenance, limitations, rollout and rollback |

Leave `static/css/app.css` and `static/css/account-themes.css` unchanged unless a
strictly widget-scoped conflict is demonstrated. Do not copy the prototype's demo
dashboard, example data, review controls, fixed answers, or global CSS into the app.

## Shared contracts

Use immutable dataclasses internally and explicit serialization at HTTP boundaries.
No arbitrary ORM instance serialization or untyped provider dictionaries throughout
the application.

```text
SourceRef(key: str, title: str, revision: str, kind: Literal["guide", "department", "directory"])
DestinationCard(key: str, title: str, description: str, url: str)
MemberSummary(display_name: str, department: str, application_role: str)
DepartmentSummary(key: str, name: str, summary: str | None,
                  responsibilities: tuple[str, ...], member_count: int)
MemberPage(items: tuple[MemberSummary, ...], page: int, page_size: int, total: int)
Guide(key: str, title: str, purpose: str, steps: tuple[str, ...],
      destination_keys: tuple[str, ...], source: SourceRef)
AssistantReply(text: str, destinations: tuple[DestinationCard, ...],
               departments: tuple[DepartmentSummary, ...],
               members: MemberPage | None, sources: tuple[SourceRef, ...])
ToolCall(id: str, name: str, arguments_json: str)
ProviderTurn(text: str | None, tool_calls: tuple[ToolCall, ...],
             source_keys: tuple[str, ...], usage: dict[str, int] | None)
ConversationState(id: str, generation: int, exchanges: tuple[dict[str, str], ...])
```

Define `ProviderMessage` as a typed union for system, user, assistant, and tool
messages, including provider tool-call IDs. Define `ToolDefinition` with name,
description, and validated argument schema. Define `ToolResult` with text-safe data,
authorized sources/cards, and `directory_row_count`. Keep those types in
`assistant_types.py`; do not maintain parallel DTO definitions.

Public JSON:

```json
{
  "status": "ok",
  "request_id": "<UUID>",
  "conversation_id": "<server-owned UUID>",
  "reply": {
    "text": "Short answer grounded in current sources.",
    "destinations": [],
    "departments": [],
    "members": null,
    "sources": []
  }
}
```

Errors use non-2xx HTTP status and
`{"status":"error","code":"...","message":"...","request_id":"..."}`.
Never return an HTML login redirect or provider exception/secret to the chat client.

## Validation environment

Follow the disposable-database instructions in the repository README. The existing
`request_hub.settings_ci` only changes the password hasher: it does **not** make
the configured database safe or replace it with SQLite.

Commands below assume a suitable existing virtual environment, test-only `DB_*`
settings, test-only Django settings, and unset ACS/Graph/Foundry credentials.
On this Windows workspace, the existing interpreter is `.venv\Scripts\python.exe`.
Use an equivalent verified interpreter if that environment changes.

Run the named tests red, implement, then run them green. Do not install tools or
dependencies during planning. At execution time, install only after a manifest
change or a missing-dependency failure. Commit task-owned changes only if the
execution authorization includes commits; otherwise leave a reviewable diff.

---

### Task 1: Authorized application knowledge and route catalog

**Files:** Create `hub/assistant_types.py`, `hub/assistant_knowledge.py`,
`hub/test_assistant_knowledge.py`. Read `hub/urls.py`, `hub/mixins.py`, the relevant
view authorization checks, and existing navigation templates.

**Interfaces**

- `allowed_destinations(user: User, query: str, current_page_key: str | None) -> tuple[DestinationCard, ...]`
- `get_guide(user: User, key: str) -> Guide`
- `approved_department_overviews() -> tuple[DepartmentSummary, ...]`
- `normalize_department(value: str) -> str`
- `validate_catalog() -> None`, raising a specific validation error on invalid data.

- [ ] Write `AssistantKnowledgeTests` for route resolution, every application role,
  forbidden direct guide lookup, unknown topics, invalid catalog keys, and missing
  department facts. The concrete expectations include:

  ```python
  self.assertEqual(normalize_department("  Engineering "), "engineering")
  self.assertNotIn("management", {card.key for card in allowed_destinations(requestor, "", None)})
  self.assertEqual(management_card.url, reverse("hub:management"))
  with self.assertRaises(PermissionDenied):
      get_guide(requestor, "management")
  ```

- [ ] Run `.venv\Scripts\python.exe manage.py test hub.test_assistant_knowledge.AssistantKnowledgeTests --settings=request_hub.settings_ci --noinput`.
  Expect a failure for the missing catalog implementation, not a database/configuration error.
- [ ] Implement the typed catalog and functions. Resolve only explicitly supported
  named routes, filter guide content by audience, and verify feature conditions
  against existing view rules. Share or surgically extract existing access helpers
  where necessary; do not broaden permissions or invent a second role policy.
- [ ] Author verified guides for the seven areas in the spec. Leave department
  descriptions absent until supplied and approved; an empty catalog is valid and
  must not be filled with the mockup's sample text.
- [ ] Run the same tests green and review the diff before checkpointing.

### Task 2: Minimal live cross-department directory

**Files:** Create `hub/assistant_directory.py`; extend `hub/test_assistant_knowledge.py`.
Consume the catalog/types from Task 1 and existing `accounts.User`.

**Interfaces**

- `list_departments(user: User, *, query: str = "", page: int = 1) -> tuple[tuple[DepartmentSummary, ...], int]`
- `get_department_overview(user: User, key: str) -> DepartmentSummary`
- `find_department_members(user: User, *, department_key: str, query: str = "", page: int = 1) -> MemberPage`
- All functions enforce active authentication; search accepts at most 100 characters,
  pages are positive integers, and invalid input raises a typed validation error.

- [ ] Write `AssistantDirectoryTests` with active/inactive accounts, blank names,
  blank departments, mixed-case/whitespace labels, HTML-like names, zero-member
  approved overviews, unknown keys, and 21-member pagination:

  ```python
  self.assertEqual(len(first_page.items), 20)
  self.assertEqual(first_page.total, 21)
  self.assertEqual(len(second_page.items), 1)
  self.assertEqual(unnamed_member.display_name, "Unnamed member")
  self.assertEqual(
      set(asdict(first_page.items[0])),
      {"display_name", "department", "application_role"},
  )
  self.assertNotIn(inactive_name, {member.display_name for member in first_page.items})
  ```

- [ ] Run `.venv\Scripts\python.exe manage.py test hub.test_assistant_knowledge.AssistantDirectoryTests --settings=request_hub.settings_ci --noinput`; verify the intended red result.
- [ ] Implement explicit ORM projections, normalized department grouping, stable
  ordering, live counts, and pagination. Member search uses approved name fields,
  not hidden email/username matches. Distinct abbreviations stay distinct.
  Blank departments use a reserved, validated "Department not specified" key.
- [ ] Merge approved descriptions with live membership counts. Return `summary=None`
  when no approved description exists. Re-query rather than caching personal data
  in the knowledge catalog; source timestamps must describe retrieval time accurately.
- [ ] Run both Task 1 and Task 2 tests together:
  `.venv\Scripts\python.exe manage.py test hub.test_assistant_knowledge --settings=request_hub.settings_ci --noinput`.
  Review and checkpoint only these changes.

### Task 3: Session lifecycle and multi-worker request limits

**Files:** Create `hub/assistant_runtime.py`, `hub/test_assistant_runtime.py`.
Modify `hub/models.py`, `accounts/recovery.py`, `accounts/test_recovery.py`.
Generate `hub/migrations/0058_assistant_usage_limit.py` from the current migration head.

**Interfaces**

- `acquire_request(user: User, request_id: UUID, now: datetime) -> RequestLease`
- `release_request(lease: RequestLease) -> None`
- `load_conversation(request: HttpRequest) -> ConversationState`
- `reset_conversation(request: HttpRequest) -> ConversationState`
- `append_exchange_if_current(request: HttpRequest, state: ConversationState, *, user_text: str, assistant_text: str) -> bool`
- `prune_expired_runtime(now: datetime) -> int`, removing expired non-global records
  only after their associated session/lease is inactive; fixed global slots remain.
- `RequestLease` is an immutable internal handle with owner UUID, selected global
  slot, and expiry. `AssistantUsageLimit` stores unique scoped keys, window/count,
  lease owner/expiry, and session generation; no prompt or directory content.

- [ ] Write `TransactionTestCase` coverage for a 21st attempt within 10 minutes,
  one user's overlapping request, a third global request, recovery after a failed
  worker, owner-safe release, independent users/sessions, and stale generations:

  ```python
  self.assertEqual(len(load_conversation(request).exchanges), 12)
  self.assertFalse(append_exchange_if_current(
      request, old_state, user_text="old", assistant_text="obsolete"
  ))
  self.assertNotEqual(old_state.id, reset_conversation(request).id)
  self.assertEqual(global_inflight_count, 2)
  ```

  Add independent database-connection tests proving quotas across workers and
  sequential requests around the window boundary. Keep invalid-input requests
  outside the attempt counter; admitted provider failures count as attempts.
- [ ] Run `.venv\Scripts\python.exe manage.py test hub.test_assistant_runtime accounts.test_recovery --settings=request_hub.settings_ci --noinput`.
  Expect only newly introduced runtime/recovery assertions to fail.
- [ ] Implement atomic shared limits, two fixed global slots, 60-second recoverable
  leases, and the 45-second request deadline. Acquire locks in stable order and
  never keep a database transaction open during provider calls. Expired leases may
  be reclaimed; an old owner may not release a replacement owner's slot.
- [ ] Implement session-owned IDs, authorization fingerprints, and a 64 KiB
  serialized history cap in addition to the 12-exchange cap. Retain whole exchanges,
  discarding oldest first. Recheck current generation before saving a response.
  Serialize fresh session reads/saves under a shared session-scoped lock and prevent
  later session middleware from overwriting newer state with a stale snapshot.
  Test New chat and role/logout changes while another response is pending.
  Opportunistically prune expired runtime records when acquiring a new quota window;
  include tests proving active-session generation records are never pruned.
- [ ] Explicitly exclude `hub.assistantusagelimit` from recovery's transient-model
  list, document its exclusion, and test that old snapshots still restore while
  runtime records are not serialized. Generate, do not handwrite, the migration.
- [ ] Run the same test selection green, plus
  `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=request_hub.settings_ci`.
  Review and checkpoint; do not migrate the running application.

### Task 4: Foundry-ready server adapter and safe configuration

**Files:** Create `hub/assistant_provider.py`, `hub/test_assistant_chat.py`.
Modify `request_hub/settings.py`, `.env.example`, dependency manifests, and README.

**Interfaces**

- `provider_readiness() -> Literal["not_configured", "configured"]`
- `FoundryProvider.complete(messages: Sequence[ProviderMessage], tools: Sequence[ToolDefinition], *, deadline: float) -> ProviderTurn`
- Typed `AssistantProviderError` carries a safe public error code and a private
  diagnostic category, not raw request/response data.
- Configuration: `AI_ASSISTANT_NATIVE_ENABLED=False`, `AZURE_AI_BASE_URL`,
  `AZURE_AI_DEPLOYMENT`, `AZURE_AI_AUTH_MODE` (`managed_identity` or `api_key`),
  `AZURE_AI_TOKEN_SCOPE` for the verified Entra endpoint audience, and optional
  `AZURE_AI_API_KEY` from secret configuration. Never infer endpoint family from
  the model name or assume every Foundry endpoint has the same token audience.

- [ ] Write `AssistantProviderTests` using a fake SDK transport: missing config
  returns `not_configured`; a configured client uses the deployment name and exact
  verified base URL; tool-call IDs survive conversion; unsupported capabilities,
  timeouts, authentication failures, and provider limits produce typed errors.
  Assert no network call when disabled or unconfigured and no secrets in logs.
- [ ] Run `.venv\Scripts\python.exe manage.py test hub.test_assistant_chat.AssistantProviderTests --settings=request_hub.settings_ci --noinput`; verify the expected red result.
- [ ] Confirm the chosen endpoint's official SDK/API contract at execution time.
  Add only compatible provider/identity dependencies to both manifests, resolve
  through the package manager, then run `pip check` in the disposable environment.
  Do not upgrade unrelated packages or introduce a frontend build system.
- [ ] Implement bounded text/tool-call conversion, explicit timeout handling,
  disabled automatic SDK retries, and capability validation. Enforce a maximum
  final answer of 8,000 characters before storing/rendering; reject oversized
  provider data with a visible error. Bound intermediate responses and the input
  budget to the verified model's limits; over-budget requests fail explicitly.
- [ ] Document exact configuration placeholders and the difference between
  `configured` and a successful live connection. Run the same tests green and
  checkpoint. Do not provide or request credentials in chat.

### Task 5: Read-only tool orchestration and assistant JSON endpoints

**Files:** Create `hub/assistant_service.py`, `hub/assistant_views.py`.
Modify `hub/urls.py`, `accounts/middleware.py`, `request_hub/settings.py`;
extend `hub/test_assistant_chat.py`.
Consume Tasks 1-4 without duplicating their validation or permissions.

**Interfaces**

- `dispatch_tool(user: User, call: ToolCall, *, current_page_key: str | None) -> ToolResult`
- `answer_message(user: User, message: str, state: ConversationState, *, current_page_key: str | None, provider: FoundryProvider, deadline: float) -> AssistantReply`
- `assistant_csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse`
  returns the JSON error contract only for assistant route names; delegates every
  other request to Django's existing default CSRF failure response.
- Read-only tools: `find_app_destination`, `get_application_help`,
  `list_departments`, `get_department_overview`, `find_department_members`.
  They map to the previously defined Python interfaces; user identity is injected
  by the server, not included in tool schemas.

| Method / path | Contract |
|---|---|
| `GET /assistant/state/` | Current conversation, approved starters, non-secret readiness |
| `GET /assistant/guides/<slug:key>/` | Authorized guide for the in-popup source viewer |
| `GET /assistant/departments/?q=&page=` | Searchable approved/live department list |
| `GET /assistant/members/?department=&q=&page=` | Paginated approved member projection |
| `POST /assistant/messages/` | `{message, current_page_key, conversation_id, request_id}` |
| `POST /assistant/conversation/reset/` | Invalidate old generation and return new state |

- [ ] Write `AssistantToolTests` and `AssistantApiTests` covering authentication,
  active-user/opt-in/rollout gating, CSRF, all supported roles, empty/malformed JSON,
  the 4,000-character and 32 KiB limits, forged conversation IDs, unsupported tools,
  unknown argument keys, invalid pagination, and 3-round/5-call/60-row boundaries.
  Include model replies that fabricate a route URL, source key, hidden field, or
  instruction to query an unrestricted table.
  Test forced password change before the view is reached: assistant APIs return
  `403 password_change_required`, make no model call, and offer the existing
  profile destination; ordinary application requests keep their current redirects.

  ```python
  self.assertEqual(anonymous_response.status_code, 401)
  self.assertEqual(csrf_failure.status_code, 403)
  self.assertEqual(oversized_body.status_code, 413)
  self.assertEqual(throttled_response.status_code, 429)
  self.assertEqual(len(reply.members.items), 20)
  self.assertTrue(all(source.key in authorized_source_keys for source in reply.sources))
  self.assertEqual(private_model_write_count, 0)
  ```

  Also verify duplicate request IDs do not invoke the provider twice. Completed
  retries return the same retained response only for the same owner/generation;
  an in-flight duplicate receives a conflict.
- [ ] Run `.venv\Scripts\python.exe manage.py test hub.test_assistant_chat --settings=request_hub.settings_ci --noinput`; isolate the intended red failures.
- [ ] Implement strict tool schemas, per-call permission checks, bounded execution,
  and evidence-derived cards/sources. The model never chooses raw links or serializes
  member cards. Reject forged source references instead of displaying invented
  provenance; missing knowledge produces an explicit unavailable/unknown answer.
- [ ] Implement endpoints with structured JSON: 400 invalid input, 401 unauthenticated,
  403 forbidden/CSRF, 404 unknown guide/department, 409 duplicate/stale conversation,
  413 oversized body, 429 rate/concurrency limit, 503 unconfigured/unavailable,
  504 deadline exceeded, and 502 malformed/upstream failure.
  Static guides/directory may work while AI is unconfigured, but are labeled non-AI.
  Register the scoped CSRF failure handler. In the existing forced-password
  middleware, replace redirects with the structured denial only for assistant
  API route names; never exempt the assistant from the password-change requirement.
- [ ] Apply leases and correlation IDs; release in `finally`. Do not persist partial
  failed exchanges. Recheck authorization/generation before storage and response;
  discard abandoned results. Strip raw prior directory answers from model evidence
  and obtain fresh directory results on follow-up questions.
- [ ] Run Tasks 1-5 tests together green, inspect error payloads/logs, and checkpoint.

### Task 6: Native contents inside the existing chat popup

**Files:** Create `templates/includes/assistant_widget.html`,
`static/js/assistant.js`, `static/css/assistant.css`, `hub/test_assistant_widget.py`.
Modify only widget-related parts of `templates/base.html`; add a dedicated
`assistant_widget` function in `hub/context_processors.py` and register it in
`request_hub/settings.py` without modifying notification badge behavior.

**Interfaces**

- Existing IDs `ai-chat-widget-container`, `ai-chat-window`,
  `ai-chat-toggle-btn`, and `ai-chat-close-btn` remain usable.
- Mount the new contents under `.rh-assistant` inside the existing `.ai-chat-body`.
- `initAssistant(root: HTMLElement, config: AssistantConfig): void` initializes
  once; `AssistantConfig` contains same-origin API URLs and the allowlisted page key,
  never model endpoint details or secrets.
- `assistant_widget(request: HttpRequest) -> dict[str, object]` exposes only the
  gated native flag, same-origin named endpoint URLs, and allowed page key. It
  performs no directory queries or SDK calls during page rendering. Serialize
  configuration with Django's `json_script`, not interpolated executable JavaScript.
- Events: `assistant:open` lazily retrieves state; `assistant:close` hides the
  surface without promising upstream cancellation. Keep one owner for open/close
  state; do not run the old iframe observer in the native branch.

- [ ] Write template assertions for anonymous/opted-out/legacy/native cases and
  Node-based DOM tests following the repository's existing JavaScript test pattern:
  one initialization, draft retention on errors, structured text-safe rendering,
  new-chat invalidation, no stale result after reset, and no document-wide observer.
  Assert that rendering an ordinary page does not query directory data, initialize
  an SDK, expose provider configuration, or alter notification-context behavior.

  ```python
  self.assertNotContains(opted_out_response, 'id="ai-chat-widget-container"')
  self.assertContains(legacy_response, 'id="ai-chat-iframe"')
  self.assertNotContains(native_response, 'id="ai-chat-iframe"')
  self.assertContains(native_response, 'class="rh-assistant"')
  ```

  JavaScript assertions must include `assert.equal(modelProducedScriptNodes, 0)`
  for hostile names/replies and `assert.equal(navigationCount, 0)` before a
  destination card is explicitly clicked.
- [ ] Run `.venv\Scripts\python.exe manage.py test hub.test_assistant_widget --settings=request_hub.settings_ci --noinput`; confirm the new behavior is red.
- [ ] Implement the approved popup header, starters, conversation, source viewer,
  directory cards/browser, composer, and errors. Keep the existing launcher markup,
  placement, shell bounds, default opt-in, account theme behavior, and mobile mode.
  Only inner scrolling/content changes; no body-width changes or page redesign.
- [ ] Add robust keyboard/focus behavior, text-safe DOM creation, Enter-to-send with
  Shift+Enter newline and IME-composition protection, pending/retry state, and
  per-generation response checks. Do not persist messages in local/session storage.
  Use storage only for non-sensitive visibility preferences and handle disabled
  browser storage without breaking the widget.
- [ ] If implementing Expand, enlarge only the existing overlay; make that state
  modal with inert background, focus containment, Escape, and restored focus.
  Normal popup remains non-modal. Closing on obscured external keyboard focus
  must preserve the user's newly focused page control.
- [ ] Run widget and existing profile-preference tests together:
  `.venv\Scripts\python.exe manage.py test hub.test_assistant_widget accounts.tests --settings=request_hub.settings_ci --noinput`.
- [ ] In a disposable browser session, compare dashboard, notifications, SQR,
  profile, and management before/after, with the popup both closed and open.
  The launcher and page-element bounding boxes must be unchanged. Check 375px,
  768px, 1440px, 200% zoom, dark/custom themes, reduced motion, large text, keyboard,
  and software-keyboard clearance. Review screenshots and checkpoint.

### Task 7: Acceptance, operator handoff, and reversible rollout

**Files:** Update README; extend the relevant assistant tests only where acceptance
coverage is missing. No production deployment or real data transfer is implicit.

**Interfaces:** The prior tasks' published API and environment contracts; the
existing user preference and disabled-by-default rollout flag.

- [ ] Add integration assertions tying readiness, disabled/native rendering,
  authorization, session reset, quota release after provider exceptions, and
  legacy rollback together. No rollback automatically triggered by provider errors.
- [ ] Run the focused regression command:

  ```powershell
  .\.venv\Scripts\python.exe manage.py test hub.test_assistant_knowledge hub.test_assistant_runtime hub.test_assistant_chat hub.test_assistant_widget accounts.test_recovery accounts.tests --settings=request_hub.settings_ci --noinput
  ```

  Expected: every selected test passes, including Node regressions (not skipped
  for a missing Node runtime). If the focused results reveal coupling, run the
  relevant existing suite before considering the full suite.
- [ ] Run `manage.py check`, `manage.py makemigrations --check --dry-run`, and
  `manage.py collectstatic --noinput` in the disposable environment using the
  chosen interpreter. Expected: no new checks, migration drift, or static failures.
  Build the existing container only when dependency/runtime validation requires it.
- [ ] Prepare 30 synthetic evaluation prompts: 10 navigation, 10 workflow guidance,
  10 department/directory questions. Include unknown topics, inaccessible routes,
  absent descriptions, missing names, cross-department queries, and adversarial
  instructions. Require at least 27/30 grounded correct answers, 100% valid
  authorized destination/source cards, and zero excluded-field disclosure/writes.
- [ ] Obtain the operator's endpoint/deployment/authentication configuration and
  consent for live inference. Verify that the existing deployment supports the
  selected API and tool contract. Run synthetic smoke tests before any permitted
  real-directory query; record capability failures as blockers, not a fallback model.
- [ ] Load-test the existing WSGI/thread configuration: the third simultaneous
  model operation must be rejected promptly, deadlines/leases must hold across
  workers, and ordinary pages must remain responsive. Compare page-request p95
  against the same staging baseline; target no more than 20% regression.
- [ ] Document approved guide/department-content maintenance, session retention,
  known limitations, correlation-based diagnosis, budgets, and manual rollback.
  Start a consented pilot with the native flag plus existing user opt-in. Rollback
  flips only the native flag, restores the legacy iframe branch, and preserves
  user preferences. Legacy removal is a separate follow-up after the pilot.

## Handoff and completion boundaries

This planning deliverable is complete when the approved spec, corrected popup
mockup, and this executable plan are saved. It does not imply the assistant has
been implemented, tested against Foundry, or deployed.

The standalone visual mockup is attached to the planning session. All required
behavior and boundaries are specified in the linked design document, so execution
does not depend on access to that session's prototype files.

Recommended execution method, if requested later: native implementation in small
verified steps. The tasks share permission, state, and response contracts closely;
parallel implementation offers little benefit until those contracts are stable.

The operator can connect the already-provisioned model after the native UI and
offline integration tests are ready. Keep the application and its chat head visually
unchanged outside the opened chatbot throughout every phase.
