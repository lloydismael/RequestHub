# Form Editor — Feature and Implementation Plan

Status: Proposed; planning only. No application code or database changes are included.

## 1. Goal and agreed scope

Add **Management → Form Editor** for users with the **Admin account type**. Provide a visual, no-code editor for business and management forms across Request Hub, prioritizing **New Request** and **New SQR Submission**.

Agreed scope: all business and management forms. Authentication, password screens, security controls, and system business rules remain protected. “Full customization” means presentation, safe field configuration, custom fields, layout, and supported conditional rules—not arbitrary code execution or removal of workflow/data-integrity constraints.

Configuration is application-wide by default. Role-specific presentation is supported within existing authorization boundaries; per-customer-account overrides are not part of the initial release.

## 2. Existing application anchors

| Area | Current implementation | Planning implication |
|---|---|---|
| Management | [management template](../templates/hub/management.html) has Users and Accounts tabs; [UserManagementView](../hub/views.py) handles management | Add Form Editor as a third tab. Preserve existing user and account operations. |
| Admin access | [AdminRequiredMixin](../hub/mixins.py) checks the Admin role | Use the same role requirement for every editor endpoint. Staff/superuser flags alone must not silently grant access. |
| Request forms | [RequestForm](../hub/forms.py), dashboard creation, request edit/collaborative management | Share configuration across registered surfaces without bypassing actor-specific logic. |
| Request rendering | [dashboard template](../templates/hub/dashboard.html) contains special priority, deployment-calendar, and date rendering | Replace special-case rendering only after equivalent components and behavior exist. |
| SQR forms | [SqrSubmissionForm](../hub/forms.py) and SQR tracker/review/stage forms | Register each distinct form/surface, retaining its permissions and validation. |
| SQR rendering | [SQR template](../templates/hub/sqr.html) includes creation and edit modals with JavaScript dependencies | Preserve field identities, modal behavior, dynamic selections, and script hooks. |
| Business records | [models](../hub/models.py) include Request/SQR relationships and constraints | Keep typed core model fields authoritative. Store new custom answers separately. |
| Routing | [hub URLs](../hub/urls.py) | Use dedicated editor routes rather than mixing schema writes into user/account formset handling. |

Observed rules to preserve include support-priority requirements, deployment dates and derived request dates, engineer eligibility/capacity handling, SQR Request ID and approver requirements, SQR folder-link requirements, scope-dependent SSE man-hours, and uniqueness of linked SQR requests. Exact capabilities must come from server validation and model constraints, not only current visual required markers.

## 3. Form coverage

Create an explicit, code-owned registry with stable form keys, underlying form class, render surfaces, permitted roles, record owner, special widgets, and field capabilities.

| Group | Forms to register |
|---|---|
| Priority launch | New Request; New SQR Submission |
| Request management | Request edit; admin/collaborative management; status update; status-log editing |
| SQR management | Submission editing; tracker edit; review/approval; quotation and order revenue tracking; proposal; delivery; performance; billing/revenue |
| Activity | Engineer activity log creation/editing |
| Management | User creation/editing; Accounts creation/editing and bulk formsets |
| Utility business forms | Request/report filters; supported SQR import options; other user-facing business forms found during inventory |

Inventory completion is a release gate: map every user-facing business form to its registry entry and every create/edit/modal/inline/bulk surface. Reuse shared schemas where semantics match, with explicit surface overrides where they do not. Do not treat hidden Django CSRF fields or formset management inputs as editable fields.

User/profile/password credential fields embedded in management forms remain protected even when adjacent management fields are customizable. Framework Django-admin screens are outside this application-tab feature unless separately approved.

## 4. Editor experience

Keep the application's Bootstrap, Bootstrap Icons, glass-card styling, and light/dark theme conventions. General UI guidance applies; the optional local UI/UX search was skipped because Python was unavailable.

### Form catalog

- Search and group forms; show Draft/Published/Default status, version, last editor, and last change.
- Actions: Edit, Preview, Version History, Restore Defaults, and copy presentation settings to a compatible form.
- Explicit coverage badges distinguish enabled forms from planned integrations; never imply an unintegrated form is customizable.

### Builder

Three panels on desktop: **field/section toolbox → form canvas → selected-item properties**. On narrow screens, use stacked panels or accessible drawers.

- Add, duplicate, reorder, group, and archive custom fields and sections.
- Drag-and-drop with Move Up/Down and keyboard alternatives.
- Configure title, introduction, section headings, submit-button text, and approved success-message text.
- Configure labels, help text, placeholders, safe defaults, optional/required state where allowed, field width, and role-based presentation.
- Preserve stable field keys and core bindings; show a lock and reason for protected properties.
- Provide unsaved-change warnings and explicit Save Draft, Preview, Publish, and Restore actions.

### Supported custom field types

Short text, long text, integer, decimal, date, checkbox, single-select, multi-select, and URL. Use existing approved reference selectors for account/user/request lookups; do not allow arbitrary database query configuration.

File uploads are a later capability requiring explicit storage, size/type limits, access controls, and attachment lifecycle design. Arbitrary HTML, CSS, JavaScript, executable formulas, and unrestricted regular expressions are not supported.

### Conditional behavior

Allow a bounded declarative rule set: equals/not-equals, contains for appropriate types, membership, empty/nonempty, and numeric/date comparisons; combine with AND/OR. Actions: show/hide and conditionally require eligible fields.

Evaluate the same rule representation on the server and client. Reject cyclic or invalid references, incompatible comparisons, and rules that hide a required system input without a legitimate server-supplied value. User input must never override protected role/identity/status fields.

Hidden custom fields are not required and submitted forged values are ignored. On edit, hiding a field preserves its previous stored answer unless an explicit, approved clearing rule applies. Visibility never substitutes for authorization.

## 5. Safe customization boundaries

| Field category | Allowed | Protected |
|---|---|---|
| Core business fields | Labels, help, order, sections, compatible widget presentation, approved defaults and visibility | Model binding, stable key, incompatible type changes, baseline validation and workflow rules |
| Optional core fields | Safe visibility and stricter required rules within declared capabilities | Rules that contradict existing role-specific behavior or make submission impossible |
| Core dropdowns | Display labels, ordering, safe retirement where explicitly supported | Renaming stored codes, deleting historical values, adding arbitrary workflow enum values |
| Custom fields | Supported type/options/validation/layout/conditions | Destructive type changes after use; permanent answer deletion through the editor |
| System/security fields | Display-only explanatory text where appropriate | IDs, references, ownership, roles/permissions, credential fields, timestamps, derived calculations, workflow transitions, CSRF/formset internals |

Archive custom fields/options instead of deleting historical answers. Preserve old option labels in versioned schemas. After answers exist, incompatible type changes create a new field key rather than reinterpret old data. A warning explains whether a change affects only future submissions or also the current editing interface.

## 6. Proposed architecture

### Data model

- **FormDefinition**: unique registry key, display name, draft revision, active published version, timestamps. The registry—not editable database metadata—defines backing models, routes, and security capabilities.
- **FormVersion**: immutable schema snapshot once published, schema-format version, version number, editor, publish metadata, and change summary. Draft updates use a revision token to detect competing edits.
- **FormAnswerSet**: linked definition/version, business record, originating surface, and validated custom answers keyed by immutable field IDs. Store typed core answers in their existing models, not duplicated JSON.
- **FormEditorAuditEvent**: actor, action, definition/version, timestamp, and configuration diff. Avoid copying credentials or sensitive submitted values into this audit stream.

Choose record links deliberately: explicit foreign keys where feasible, or an allowlisted content-type/object-ID adapter with ownership validation and cleanup tests. Formsets store custom answers per row, not against the parent formset. Filters/previews do not create persistent answers; import jobs need their own configuration adapter.

For saved records, persist the form version used at creation and version metadata for later edits. Existing records without version metadata use the generated baseline. Historical views use the original version labels/options. Editing old records uses a defined compatibility mapping, preserving retired values and archived answers; newly required custom fields apply to new submissions unless an explicit backfill/edit policy is published.

### Service boundaries

Proposed modules:

- `hub/services/form_registry.py`: definitions and safe per-field capabilities.
- `hub/services/form_schema.py`: schema validation, migration, conflict checks, publication, and diffs.
- `hub/services/form_runtime.py`: Django field adaptation, role/rule evaluation, custom validation, and answer persistence.
- Dedicated editor views, shared field/section template partials, and scoped builder/runtime JavaScript and CSS.

The runtime extends existing Django forms; it does not replace lifecycle services or model validation. Build editor controls from registry capabilities. Do not accept arbitrary model names, field bindings, template paths, or executable content from schema requests.

### Publishing and runtime

1. Generate a baseline schema that reproduces current fields, ordering, widgets, and constraints.
2. Save drafts without affecting users.
3. Preview through the actual runtime renderer with sample data/role context; preview is non-persistent and cannot send notifications or perform business writes.
4. Publish only after full schema/rule/capability validation; update the active version transactionally and audit the change.
5. Pin the published version when a user opens a form. On POST, accept a known compatible pinned version within a documented expiry window; otherwise show a reload message while preserving recoverable input. Never silently revalidate against a different version.
6. Save the existing business record and custom answers in one transaction; trigger existing side effects only after successful persistence as currently intended.
7. Roll back by republishing a prior snapshot as a new version. Restore Defaults also creates a new version; neither action deletes records or answers.

Cache published schemas by form key and version. Invalidate the active-version lookup after publish/rollback across workers; avoid a query per field. If no customization exists, use the baseline. Invalid/unavailable schemas must not silently skip required custom validation: fail closed with a recoverable error and log the configuration failure.

## 7. Management integration and access

- Add a real third tab with `active_tab == 'forms'`; replace current “anything except accounts means Users” conditions with explicit states.
- Support a persistent tab URL such as `/management/?tab=forms` and direct form-editor links.
- Keep editor reads/writes in dedicated routes: catalog, draft load/save, preview, publish, history, rollback, and reset.
- Require login and the Admin account type on every endpoint, including preview/history and AJAX calls. Keep all existing business-form permissions unchanged.
- Use CSRF protection, explicit methods, payload/field/rule limits, escaped text output, safe URL handling, optimistic concurrency, and publish transactions.
- User/Account management adapters retain formset management data, per-row prefixes, uniqueness checks, protected user handling, and self-access safeguards.

## 8. Delivery phases

### Phase 1 — Inventory and compatibility foundation

Map all forms and surfaces; declare capabilities and protected fields; capture baseline behavior and representative role journeys. Add versioned schema models, registry, migration/backfill strategy, and baseline-equivalence tests. No visual behavior changes yet.

### Phase 2 — Management tab and builder

Add catalog, field/section editor, draft save, preview, publish, version history, rollback/reset, concurrency protection, and configuration audit. Keep unreleased form integrations disabled and clearly labeled.

### Phase 3 — New Request and New SQR rollout

Integrate both create forms and their corresponding edit surfaces/compatibility adapters. Preserve deployment calendar, support priority, account autocomplete, avatar selectors, SQR dependent choices, approver logic, existing notifications, and validation-error reopening. Enable custom-answer storage and version pinning.

### Phase 4 — Complete business/management coverage

Integrate remaining request/SQR stages, logs, management formsets, inline editors, filters, and import options. Display custom answers in detail/history views and support safe permission-aware export with stable column identities. Explicitly distinguish presentation-only utility forms from persisted-record forms.

### Phase 5 — Release verification

Exercise role restrictions, schema limits, old-record compatibility, concurrent edits, worker cache invalidation, rollback, accessibility, and performance. Document supported/protected capabilities and the operating procedure. Enable through a staged feature flag; disabling the editor stops configuration changes without abandoning required validation for already published custom schemas.

A two-form launch is an incremental milestone, not completion of the agreed all-business-form scope.

## 9. Acceptance criteria

- Only Admin-account users see and can access Form Editor; direct access by other roles is denied.
- Users and Accounts tabs still select correctly and preserve their search/edit/create/bulk behavior.
- Admins can change labels/layout, add a custom field, configure a safe condition, save a draft, preview, publish, and roll back without a code deployment.
- Drafts do not alter live forms; published changes survive restarts and are consistent across workers.
- New Request and New SQR use the published schema while keeping current business validation, permissions, special widgets, and save/notification behavior.
- Invalid or prohibited configuration is rejected server-side with field-level errors.
- Custom answers persist, reopen correctly, appear on authorized detail views, and retain their historical version meaning.
- Concurrent draft edits do not silently overwrite one another; publication while a user is filling a form follows the documented pinned-version policy.
- Archived fields/options, legacy values, and old records remain readable/editable without forced data loss.
- Bulk management forms and inline business updates enforce the same applicable custom rules as normal forms.
- Every inventoried business/management surface has an integration test before being advertised as supported.
- Builder controls are labeled, keyboard-operable, and have non-drag alternatives; errors remain visible and multi-error submissions focus a linked summary. Verify responsive layouts, dark/light contrast, and reduced motion.

## 10. Validation and documentation plan

Use the existing Django test runner once a Python environment is available. Add targeted form-editor tests first; then run directly affected accounts, request lifecycle, SQR history/performance, import/export, and management tests. No new test framework is required.

Test schema/type/condition validation, role authorization, forged hidden/protected inputs, draft/live separation, transactional saves, custom-answer round trips, version expiry, legacy edit policy, resets/rollback, concurrent publication, and cache behavior. Compare default-schema rendering and submissions against captured baseline behavior.

At implementation time, update the README with the feature entry and add an admin usage guide covering field capabilities, protected constraints, publishing, old-record handling, rollback, and supported coverage.

Planning validation: repository paths and integration points were inspected. No application tests are needed for this documentation-only change. Python was unavailable, so no runtime or optional UI guidance script was executed.

## 11. Decisions for implementation approval

Recommended defaults in this plan require approval before implementation:

- Application-wide configuration, with role presentation overrides; no customer-account-specific schemas initially.
- Admin users may both edit and publish, with immutable configuration audit history; optional separate publisher approval later.
- New required custom fields do not retroactively block old records unless an explicit migration/backfill policy is approved.
- Define pinned-version expiry and emergency invalidation policy before rollout.
- Upload custom fields and schema import/export remain later enhancements.

Do not provide a fixed delivery estimate until the full surface inventory and compatibility adapter work are sized.
