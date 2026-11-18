# Code Scanning Hardening Implementation Plan

> **For agentic workers:** Use the subagent-driven-development or executing-plans skill. Each batch is independently testable. Do not commit or push during local implementation unless separately requested.

**Goal:** Remediate the remaining GitHub code-scanning findings, preserve authorized workflows, and verify safeguards already present in the current revision.

**Architecture:** Reuse PostgreSQL for atomic authentication quotas. Enforce authorization and state transitions in Django, render assignment names as text, and use an HTML parser only in test helpers.

**Tech stack:** Django, PostgreSQL, openpyxl, standard-library HTMLParser, Node and Playwright, GitHub Code Scanning.

**Approved design:** Preserve both sign-in screens; use shared account and source limits; require password changes before admin access; enforce department and engineer read-only fields on the server. Lock approved quote fields in the submission editor until an admin or assigned reviewer explicitly chooses **For Revision**; keep remarks and legitimate proposal-stage tools available. Preserve request history on removal, preserve the reader and delivery workflows, and attribute engineer activity to its author. Do not push, deploy, migrate a live database, or dismiss alerts.

## Global constraints

- Work against a disposable PostgreSQL database with synthetic data and an isolated Python environment. Do not use the deployed app or database for testing.
- Keep the existing login routes, staff checks, CSRF, safe redirects, aliases, and case-insensitive login. Do not write credentials or raw throttle keys to logs.
- GitHub listed 20 open alerts on `master`: 17 AI Scan at `6e537eec` and three CodeQL at `d2ab79d`. The checkout at triage was `d2ab79d`. Report local verification and default-branch scan status separately.
- Include fixes, tests, migration/configuration, maintenance, and documentation in release notes. Use genuine before/after evidence and verify Notifications visibility; do not mark an unreleased entry as published.

## Review focus

- Blocked sign-in attempts must not verify a password; a valid permitted sign-in must not reset a different account or source's failures.
- Opening an SQR for revision must clear approval metadata; a historical Undo or Restore cannot attach old approval to newly changed quote data.
- Existing read access does not grant authority to edit reviewer-only fields or another engineer's activity.
- Request removal must preserve its record and dependent history, and restore must work through the existing admin path.
- Assignment labels must remain literal text on both save and Undo, including empty values; test script extraction must still preserve inline JavaScript.

## Task 1: Shared authentication and profile controls — 78, 79, 80, 62, 59; recheck 58 and 60

- [ ] Add focused regressions to `accounts/tests.py` and `accounts/test_sign_in_logs.py` for shared counters across app/admin sign-in, pre-hash decisions, case and alias parity, successful sign-in, mandatory password change before admin access, and unchanged department for non-admins. Assert the missing-account path performs a dummy hash without timing sleeps. Run the selectors before implementation and confirm they fail for the missing controls.
- [ ] Put quota persistence in `accounts/models.py`, an additive accounts migration, and a focused `accounts/login_throttle.py` service. Reserve permitted attempts transactionally with short-lived database rows and collision-safe keys; cap a client source across usernames and keep the existing five-attempt, 15-minute account policy. Establish one order for checking keys, absolute expiry, bounded cleanup, and generic failure handling. Do not create an unbounded set of account rows for nonexistent usernames. Document the source quota and expiry behavior.
- [ ] Integrate reservation before password verification in `accounts/backends.py`; use `accounts/forms.py`, `accounts/views.py` and the Django admin configuration for generic feedback without changing admin eligibility or redirect behavior. Keep the existing trusted-proxy resolver in `accounts/sign_in_log.py`. Enforce the admin password-change requirement in `accounts/middleware.py` with explicit password-change/logout exceptions, and disable the department field at Django form level.
- [ ] Run focused auth, existing IP and avatar regressions, admin login and temporary-password flow, then check migration drift. Verify that database failure cannot fall back to unlimited credential checks.

## Task 2: SQR approval and reviewer authorization — 81, 76; recheck 63

- [ ] Add focused cases in the existing SQR view tests for an approved owner's unchanged quote and allowed remarks, authorized explicit reopening and edits, nonassigned PM restrictions, preserved reader access, and no unauthorized changes or history in Save, Undo, Restore, and related protected actions. Verify protected writes and status checks use the current row, not a stale form instance. Run before implementation and confirm the new policy assertions expose the gaps.
- [ ] Define the shared reviewer decision and explicit approved-quote field set near the SQR code in `hub/views.py` or a focused SQR service. Enforce the agreement in `SqrEngineerUpdateView`, `SqrInlineFieldUpdateView`, `SqrInlineFieldUndoView`, `SqrHistoryRestoreView`, and directly related proposal/revenue writes. Use the existing For Revision action and make the status check, approval cleanup, save and history consistent under a row lock. Preserve authorized post-approval proposal/delivery work; check visible editing affordances against the policy.
- [ ] Run ownership, review/revision, On Hold, proposal/revenue, undo/history, configured form and existing inline numeric regressions.

## Task 3: Request recovery and activity authorization — 77, 72, 73, 75

- [ ] Add regressions to the existing request-management and recovery tests for confirmation POST retaining the row and history, authorized restore, engineer read-only values round-tripping unchanged, activity author attribution, and consistent edit access on both the modal GET and POST. Run before the production change.
- [ ] In `hub/views.py`, handle `RequestDeleteView.form_valid()` using soft deletion, keep removal idempotent within the existing scoped queryset, and align or explicitly reject the alternate DELETE method. Update `templates/hub/request_confirm_delete.html` if its copy contradicts recovery.
- [ ] Use one scoped log-query helper for `CollaborativeRequestManageView` modal and POST. Admin and PM-ESG can edit; eligible engineer roles can edit their own logs; other collaborators cannot edit. New engineer activity belongs to the actor; preserve attribution on existing rows and explicit authorized admin workflows.
- [ ] In `hub/forms.py`, apply the engineer restriction to customer/account and the recorded request date in the collaborative bound form, without blocking the separate editable deployment scheduling date or authorized requestor/admin edits. Run request, activity, assignment, configured-form and recovery regressions.

## Task 4: Safe rendering, CodeQL, and release evidence — 74, 65, 70, 71; recheck 56 and 57

- [ ] Add or extend an existing local JavaScript regression for assignment labels after Save and Undo. In `templates/hub/sqr.html`, write user-provided names as text and preserve the blank placeholder. Run the related tracker tests before and after.
- [ ] Move or reuse the existing standard-library parser pattern in a common test helper for script extraction; remove script elements from the local browser fixture without regex or treating the helper as a production sanitizer. Replace the sites in `hub/test_mobile_ui.py` and `accounts/test_christmas_login.py`. Run the parser, seasonal-login and mobile-browser regression selectors.
- [ ] Run existing spreadsheet tests for literal XLSX/CSV text and unchanged numeric cells. Run the combined affected Django and Node regressions, migration drift, system checks and `git diff --check`. Record any stack-version limitations and remaining independent GitHub verification.
- [ ] Update the README security guidance and unreleased security changelog. Package or record exact-revision comparisons according to the existing release workflow. Test that a synthetic eligible release appears in Notifications and incomplete evidence stays draft; verify this specific release on its actual revision when integrated. No release publication is part of this plan.
