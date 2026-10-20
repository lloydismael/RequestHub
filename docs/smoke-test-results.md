# Smoke Test Results - 2026-10-20

## Summary

The authenticated profile crash was reproduced and fixed. The final isolated
accounts/hub suite ran 219 tests: 218 passed, one PostgreSQL-only check skipped,
and no failures or errors. Django system checks and editor diagnostics for the
changed Python files reported no issues.

## Changes

- `ProfileUpdateView.get_object()` now retrieves the concrete authenticated
  `User` record. Django's lazy user wrapper previously failed the form-editor's
  exact record-type check. The shared record allowlist remains unchanged.
- Added profile rendering coverage for all seven roles, profile save/reopen,
  preference persistence, and anonymous access.
- Corrected stale CSS cache-version and manifest-hashed script assertions.
- Made the authentication test create its canonical admin fixture when absent,
  rather than depending on migration seed data.

## Automated Evidence

- Synthetic concrete/lazy user comparison reproduced the exact
  `SchemaValidationError: ['Record type is not allowed for this form.']`.
- Focused profile boundary check passed after the fix.
- Profile regression slice: four tests passed.
- Previously failing login/authentication/SQR rendering slice: nine tests passed.
- Final suite: `test accounts hub --noinput --verbosity 0`, 219 tests run,
  `OK (skipped=1)`, using the project's `.venv/Scripts/python.exe`.

The suite ran through a process-local launcher that bypassed dotenv loading,
replaced the database with in-memory SQLite, disabled migrations for accounts,
hub and Django's database apps, used temporary media storage, cleared ACS/Graph
integration settings, and selected Django's in-memory email backend. The real
static manifest storage was retained. No application configuration files or
retained database records were changed for testing.

Existing tests exercise request and lifecycle workflows, notifications, reports,
SQR updates, performance data, form-editor schemas/versioning/answer persistence,
and authorization cases. This is regression coverage, not proof of every route,
role combination, or external integration.

## Live Browser Evidence

The existing authenticated admin session returned HTTP 200 for:

- `/accounts/profile/`, `/dashboard/`, `/sqr/`, `/management/`
- `/notifications/`, `/reports/`
- `/management/forms/`, `/management/forms/profile/`
- `/dashboard/live/`, `/sqr/performance-data/`, `/sqr/reports/data/`

Profile Personal Info, Avatar, Password and Settings tabs switched correctly.
SQR tracker/performance panels and search inputs were exercised without writes.
The profile was also checked at a 390-by-844 viewport: inputs rendered and the
document did not horizontally overflow. No browser page exceptions or HTTP
responses of 400 or higher were observed during the final interaction check.
This does not certify all asset loads, charts, dialogs, or mobile layouts.

## Limits And Follow-Up Gates

- PostgreSQL execution remains blocked: no local listener was found on ports
  5432/5433, dedicated smoke credentials were absent, and the application's
  configured database was non-local. It was deliberately not used for tests.
- Run the same suite with normal migrations against an explicitly isolated
  PostgreSQL test database before claiming database/migration compatibility.
- Live browser checks used one admin session; other roles have automated
  coverage, including all-role profile rendering, not complete browser proof.
- Real email/Teams/Graph delivery, destructive backup restore, complete password
  and upload workflows, exports/imports in the browser, and an exhaustive
  role-by-route matrix were not verified in this pass.
- No disposable live records were needed. No real users' passwords or profile
  data were changed, and no live records required cleanup.