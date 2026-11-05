# Request Hub Assistant redesign

Date: 2026-11-05
Status: Popup-only design approved for implementation planning on 2026-11-05.
Implementation requires a separate go-ahead; no application changes have been made.

## 1. Intent and confirmed requirements

Replace the content of the existing Copilot Studio chat popup with a native assistant that helps
people navigate Request Hub, understand its features, and learn about departments
and their members.

The user confirmed:

- The assistant must know the application and help users navigate it.
- Users should be able to learn about different departments.
- Membership comes from registered Request Hub users, not the wider company directory.
- Use an already-provisioned Azure AI Foundry model; the user will connect it later.
- The redesign applies only to the chatbot when its existing floating chat head is
  opened. The Request Hub page/application must not be redesigned.
- Keep the existing closed launcher and floating-popup interaction. An optional
  expansion enlarges only the chat overlay, not the application layout.
- This task is planning and mockups, not production implementation.

## 2. Current implementation and constraints

- `templates/base.html` contains the lazy-loaded Copilot Studio iframe, launcher,
  open-state persistence, and focus handling.
- `static/css/app.css` contains the widget layout, mobile behavior, and dark mode.
  `static/css/account-themes.css` also styles the launcher.
- `accounts.User.show_chatbot` controls visibility and defaults to false. Preserve
  that opt-in and existing preferences for themes, larger text, and reduced motion.
- `accounts.User` has name fields, an application role, and a free-text department.
  It does not contain department responsibilities, reporting lines, or job titles.
- User management is admin-only. Its queryset must not become a general assistant
  endpoint: create a deliberately smaller, read-only directory projection.
- Page access is role-based and enforced in views and shared mixins. The assistant
  must not widen page or record access.
- The production entrypoint runs Gunicorn WSGI with four workers and two threads
  per worker. Avoid assuming that the ASGI module means async streaming is deployed.
- Recovery uses an explicit hub-model allowlist and rejects unclassified models.
  Any new persistent model must have an explicit recovery policy and tests.

## 3. Approaches considered

### A. Native in-page companion -- selected

A native conversation surface replaces the iframe inside the existing floating
chat window. It overlays the page exactly where the current widget opens; it does
not dock, push, resize, or restyle the underlying page. Expansion enlarges only
the overlay. Narrow screens retain the existing full-screen-chat behavior.

Advantages: contextual help, fewer navigation interruptions, shared application
authentication and appearance, complete control over interaction and errors.
Tradeoff: less reading space than a dedicated page; the floating window covers a
portion of the page while open.

The initial comparison mockup illustrated a changed dashboard and a docked panel.
The user explicitly rejected that scope. Those dashboard visuals and docking
behavior are not part of this design.

### B. Dedicated assistant workspace -- not selected

A separate page provides room for longer guidance and department exploration.

Advantages: simpler page-level integration and spacious browsing.
Tradeoff: users leave the task they are trying to understand; weaker fit for the
primary navigation goal.

Both use a Django backend and the existing model deployment. Retaining the
Copilot Studio iframe would not provide the requested native interface and direct
model integration. A separate Foundry Agent Service, new frontend framework,
vector database, or Azure AI Search service is not required for this bounded scope.

## 4. Proposed product behavior

These are design recommendations, not additional requirements attributed to the user.

### Application navigation

- Answer requests such as "Where are service quotations?" with a short explanation
  and a server-generated destination card.
- Generate URLs from an allowlist of Django named routes using `reverse()`.
- Filter destinations using the user's current permissions, including applicable
  feature conditions. Recheck access in the destination view.
- Navigate only after the user activates a destination. Do not silently redirect,
  click buttons, submit forms, change filters, or invoke mutation endpoints.
- Page context is an allowlisted route key and display label. Do not scrape the
  DOM, send page records, or forward arbitrary URLs/query strings to the model.
- Navigation across pages should preserve the bounded conversation in the current
  authenticated session; the launcher retains the user's open/closed preference.

### Application knowledge

- Maintain a small, version-controlled catalog of verified guides for Dashboard,
  SQR, activity logs, reports, notifications, profile preferences, and management.
- Each entry has a stable key, title, permitted audience, purpose, verified steps,
  route references, and a revision identifier.
- Derive instructions from the current behavior, not blindly from potentially
  outdated README text. Update guides and tests with related feature changes.
- Restrict admin-only instructions and destinations to their permitted audiences.
- Cite the relevant guide in the answer. A source opens a read-only guide view
  inside the assistant; its audience restrictions also apply to direct API access.
- Say when no verified guide exists instead of inventing a feature or workflow.

### Department and member knowledge

- All active, authenticated users may view the approved cross-department directory.
  This is a new limited directory permission, not access to the management page.
- Show active, registered users only. Allowed member fields are display name,
  department, and clearly labeled **application role**.
- Exclude email, phone, username/login identifier, account flags, sign-in history,
  private activity, assignments, passwords, tokens, and other security information.
  Additional directory fields require a later explicit scope decision.
- Build display names from name fields. If absent, use "Unnamed member"; do not
  silently substitute an email address or username.
- Read membership from the database on each directory question. Do not upload or
  train on the whole directory or return unrestricted model rows.
- Normalize department lookup keys by trimming whitespace and case-folding.
  Do not guess that different abbreviations denote the same department.
- Paginate member results, initially 20 per page, with the matched count and
  explicit "more results" control. Distinguish full results from a partial page.
- Department labels with no overview may still show their actual registered
  members, while stating that a description has not been published.
- Users without a department appear only under "Department not specified";
  the model must not infer their membership.

### Department descriptions: approved content, without another app screen

Maintain a small version-controlled department catalog alongside the application
guides. Each approved entry has a unique normalized key, display name, summary,
responsibilities, revision, and review date. The organization supplies the facts;
an application release publishes the approved catalog.

Membership remains on `User.department`; this is not a foreign-key migration or
an organizational restructuring project. The department browser inside the chat
combines actual department labels from active members with approved overviews,
including an approved department with zero registered members.

Do not add a department-management page, change the existing Management UI, or
introduce a persistent overview model in this scope. Unapproved drafts remain
outside the runtime catalog. Validate canonical keys and required metadata.
Do not generate department claims, invent team leaders, or seed synthetic data.

Descriptions must be supplied and approved by the organization before a
department-responsibility question can receive a factual answer. Until then,
membership works and the description is explicitly unavailable.

## 5. Interface specification

### Visual direction

Redesign only the inside of the opened chat window. Keep the existing Request Hub
header, sidebar/navigation, dashboard, tables, forms, page dimensions, spacing,
and unopened floating launcher unchanged.

Use the existing Segoe UI/system typography, Bootstrap Icons, and semantic
Request Hub colors. Favor a clear surface, restrained blue accents, readable
spacing, and modest borders over heavy gradients and animation.

The corrected popup-only mockup is the visual reference. The earlier whole-page
comparison is superseded. All example names and department content are fictional.
Scope new styles and DOM changes to the chat component; do not introduce general
body, navigation, table, typography, or page-layout overrides.

### Surfaces

- Launcher: retain the existing button, position, appearance, preferences, and
  accessible open/close state.
- Header: "Hub Assistant", concise purpose, New chat, Expand/Restore, and Close.
- Context indicator: current page label with a clear statement about shared context.
- Empty state: shortcuts for finding a page, learning a workflow, and exploring
  departments and people.
- Conversation: readable messages, validated navigation cards, department cards,
  member lists, and source references.
- Composer: labeled multiline input, send control, pending status, and a visible
  error/retry path that preserves the user's draft.
- Department browser: searchable department list and paginated members; available
  without requiring the model to invent or format directory records.
- No attachment, voice, or permanently stored conversation-history affordances.

### Responsive and accessible behavior

- Desktop: retain the existing floating window's width/height constraints and
  bottom-right placement, normally up to 430px wide and 620px tall.
- Preserve the current mobile breakpoint and explicit mobile-view preference,
  where the chat already becomes full-screen. Do not resize the underlying page.
- Optional expanded mode enlarges the chat overlay within the viewport. It is
  modal, with focus containment and background inertness; it is not a new page.
- The normal floating window is non-modal and must not trap focus. If keyboard
  focus moves into page content covered by the popup, close the popup without
  stealing that focus. Explicit Close restores focus to the launcher.
- Escape closes the active assistant surface; closing restores focus to its
  launcher. New chat does not unexpectedly move focus.
- Keep the composer visible above the software keyboard; use dynamic viewport
  height, safe-area insets, and scrollable conversation content.
- Preserve existing app mobile-view and appearance preferences.
- Respect reduced motion, large text, light/dark mode, and custom account themes.
- Use accessible names for icons, a logical keyboard order, clear focus indicators,
  and polite status announcements rather than announcing every generated token.
- Aim for at least 44 CSS-pixel primary touch targets and WCAG AA contrast.
- Test at 375px, tablet widths, desktop widths, and 200% zoom without horizontal
  overflow within the assistant or covered focus targets.

### Response and error states

Version one uses bounded JSON requests, a truthful "Working..." state, and a final
answer. It does not fake streaming with a typewriter animation. Genuine streaming
is a future transport improvement requiring deployment capacity validation.

Show distinct states for: ready, pending, empty result, unknown topic, session
expired, permission denied, connection not configured, service timeout, rate limit,
and provider failure. Never label an unconfigured provider as connected.

No automatic retry of a costly model request. Offer an explicit retry. Closing the
panel is not a guarantee of upstream cancellation; results from an abandoned or
cleared conversation must never reappear in a different conversation.

## 6. Backend and trust boundary

```text
Native chat component
  -> authenticated, CSRF-protected Django assistant endpoint
     -> validate request, current permissions, quota, and conversation ownership
     -> bounded orchestration with the existing Foundry model
        -> allowlisted, read-only application tools
           -> verified guide catalog / live directory / published overviews
     -> validate sources and card identifiers against the tool results
  <- answer text + server-built cards + source references
```

Keep the integration in focused assistant view/service modules rather than adding
another large inline script to the base template or embedding logic throughout
the main views module. Template changes are limited to mounting the native chat
inside the existing widget and loading its scoped assets. Backend additions support
the chat only; do not add application navigation items or redesign any app screen.

Initial tools:

1. `find_app_destination(query, current_page_key)`
2. `get_application_help(topic_key)`
3. `list_departments(query, page)`
4. `get_department_overview(department_key)`
5. `find_department_members(department_key, query, page)`

Identity and permissions come from `request.user`, never tool arguments. Tool
schemas reject unknown keys and invalid types. Use bounded ORM queries; no raw SQL,
arbitrary Python, arbitrary HTTP fetches, file access, or mutation tools.

The model may request tool calls, but the server decides what can run. Treat user
messages, stored descriptions, and tool content as data, not privileged instructions.
Only current, authorized tool results may become sources or cards.

Use text-safe rendering for answer content and validated structured UI components.
Do not inject raw model HTML or accept model-authored navigation URLs.

## 7. Foundry integration contract

Reuse the existing deployment. No resource provisioning, model deployment,
fine-tuning, hosted agent, or additional paid Azure service is part of this plan.

Before activation, the operator supplies through environment/app settings:

- Global assistant enable flag.
- Exact inference base URL and its documented endpoint family.
- Deployment name, not merely the underlying model name.
- Supported API and tested model capabilities: text chat and tool calling.
- Authentication configuration: prefer Entra ID/managed identity in Azure;
  optionally use an API key stored only in server-side secret configuration.

Use the officially supported SDK for the actual endpoint. Project and resource
endpoints can require different token audiences; do not mechanically append a
path or reuse a token audience without checking the deployment documentation.
Use the OpenAI-compatible v1 client only after endpoint compatibility is verified.

Do not ask for API keys in chat, return secrets to the frontend, or store them in
department content, browser storage, logs, or source control. Readiness checks
report configuration state without returning secret values.

If the selected model does not support the required tool-calling contract, stop
activation with a clear compatibility error. Agree a revised integration before
substituting another model or silently changing behavior.

A disconnected installation must still render the native UI and show a useful
configuration state. Published guides and the read-only directory may remain
available as explicitly labeled non-AI features.

Official reference:
[Foundry model inference and endpoint authentication](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-chat-reasoning).

## 8. State, limits, and operations

Proposed conservative launch defaults:

- Message length: 4,000 characters; total submitted request body: 32 KiB.
- Conversation: at most 12 user/assistant exchanges and a bounded serialized size,
  in the existing server-side Django session; no durable conversation tables.
- Tie conversation state to user identity and an authorization fingerprint. Clear
  it on explicit New chat, logout/session expiry, or a role/access change.
- Session storage follows the existing session cleanup policy. Do not claim that
  expiry physically deletes database rows immediately.
- Re-fetch directory data for follow-ups; prior assistant messages are not evidence
  of current membership or permission. Avoid persisting raw directory tool payloads.
- At most three tool rounds, five calls total, and 60 directory rows supplied to
  the model per request. Larger results remain available through direct pagination.
- Total model operation deadline: 45 seconds. Bound provider response and input
  budgets using parameters supported by the selected model.
- One in-flight model request per user; 20 attempts per user per 10-minute window.
- Use a shared database-backed `AssistantUsageLimit` lease/window record with
  atomic acquisition, rather than a process-local cache or browser-only throttle.
  Expired leases recover after worker failure and use request-specific ownership.
- Global capacity ceiling: initially two concurrent model operations across all
  workers, enforced through the same shared lease mechanism. Confirm suitability
  under production-like load before increasing it.

Do not hold database transactions open during network calls. Clean expired usage
records without retaining prompt content. Record correlation IDs, timings, tool
names, outcomes, and provider token usage when available; omit raw prompts,
responses, directory payloads, and secrets from routine logs.

Guides and department overviews are backed up and versioned with the source
catalog, not database recovery snapshots. Runtime quota/lease records must be
explicitly excluded from recovery and safely reinitialized. Keep existing snapshot
compatibility and the recovery schema guard tested.

## 9. Non-goals and anti-goals

- No request/SQR creation, editing, approval, deletion, or automatic form submission.
- No reading private request contents, email, personal activity, or sign-in logs.
- No Microsoft Graph company-directory integration in this release.
- No inference of department responsibilities, reporting lines, or employee titles.
- No whole-database prompt dumps, arbitrary web browsing, file uploads, or training.
- No new React/Tailwind stack, Azure Search service, agent hosting, or vector store.
- No silent removal of the user's chatbot opt-in or changes to unrelated app pages.
- No dashboard, navigation, layout, or whole-application redesign; no docked panel,
  separate assistant page, department-management screen, or redesigned closed launcher.
- No pretending that a static mockup is an AI-connected implementation.

## 10. Acceptance criteria

1. Existing chatbot visibility defaults and preference handling are preserved.
   The closed launcher and underlying page look and behave exactly as before.
   Opening the popup does not move or resize any page content.
2. The native UI replaces the iframe only when the rollout flag is enabled; rollback
   can restore the old widget without deleting data or changing user preferences.
3. Every supported route recommendation resolves to a permitted named route.
   Direct access remains protected; denied routes never appear in tool results.
4. Each guide answer cites an authorized, current guide; unknown features are
   acknowledged as unknown.
5. Department membership matches active Request Hub accounts across departments.
   Pagination, empty departments, missing names, case/whitespace, and membership
   changes are verified without inventing fields.
6. Only the approved directory projection reaches the model and browser.
   Unpublished department facts and excluded account fields remain inaccessible.
7. Malicious tool arguments, injected instructions, forged sources/links, and HTML
   payloads cannot cause writes, unauthorized disclosure, or script execution.
8. Anonymous access, CSRF failure, role changes, session expiry, cross-user access,
   rate limits, stale responses, timeouts, and provider failures have tested outcomes.
9. The 45-second deadline, per-user throttle, and global concurrency ceiling hold
   across multiple workers, including exception and crashed-worker recovery paths.
10. Desktop, mobile, dark/custom themes, keyboard-only use, reduced motion, and zoom
    pass targeted UI checks. Screenshot and bounding-box comparisons verify that
    existing page content and the closed launcher are unchanged.
11. Backup/restore schema guards and older-snapshot compatibility remain valid.
12. A consented smoke test against the actual deployment proves the inference and
    tool contract before any claim that Foundry is connected. Offline tests alone
    are not evidence of live model compatibility.

## 11. Implementation sequence

1. Establish verified route/guide and directory access contracts, with tests.
2. Add the approved department content catalog and its validation.
3. Build the native popup contents and structured answer components with a
   test-only provider, preserving the existing widget shell and all app pages.
4. Implement session-bound orchestration, quotas, errors, and the Foundry adapter.
5. Operator configures the existing deployment; run permission, quality, and load
   checks using synthetic fixtures or approved data.
6. Roll out to a pilot behind the global flag and user opt-in; monitor before wider
   enablement. Remove legacy embed code only in a later, deliberate cleanup.

## 12. Decisions left to the builder and operator

Builder discretion within this design: internal chat spacing, module/file
boundaries, pagination markup, and test fixture organization. Existing page layout,
launcher appearance, widget placement, and mobile breakpoints are not discretionary
redesign surfaces.

Operator inputs before activation: endpoint family/base URL, deployment name,
authentication method, supported capabilities, approved department descriptions,
and approval to send the selected directory projection to the Foundry deployment.

Items requiring design approval now: the popup-only visual scope; organization-
approved department descriptions published through the source catalog; minimal
cross-department directory fields; read-only scope; session-only conversation
memory; and non-streamed version-one transport.
