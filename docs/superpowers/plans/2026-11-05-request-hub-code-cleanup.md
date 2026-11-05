# Request Hub Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the confirmed unreachable request-detail implementation and unused private helpers while reconciling declared dependencies with the chosen Django 6.1 runtime.

**Architecture:** Keep the existing Python 3.12 container and CI runtime. First align the direct pins in `requirements.lock` to `requirements.txt` and update README's Django version; then prune only confirmed unreachable Python code and the now-orphaned request-detail template, preserving the existing named redirect URL. Leave ambiguous templates, profile policy code, and operator artifacts untouched.

**Tech Stack:** Python 3.12, Django 6.1, PostgreSQL, pip, Django `manage.py test`, Node.js tests.

**Spec:** `docs/superpowers/specs/2026-11-05-code-cleanup-design.md`

## Global Constraints

- Keep the existing Python 3.12 runtime.
- The user selected Django 6.1; Django's official installation FAQ lists Python 3.12 as supported.
- Keep all direct dependency ranges in `requirements.txt`; resolve and pin compatible versions in `requirements.lock`, without adding dependencies or loosening unrelated constraints.
- Do not change request lifecycle, permissions, authentication, emails, SQR, recovery, deployment configuration or API/URL behavior.
- Do not change the disabled profile-completion middleware or its setting.
- Keep the `request-detail` named URL and its permanent redirect to collaborative request management.
- Preserve `ProfileCompletionMiddleware`, request-reassignment behavior, recovery routes, Outlook delegated draft implementation, and all operator scripts.
- Do not load `.env`, use production credentials, or connect to a production database.
- Set `PYTHON_DOTENV_DISABLED=1` in the validation shell before running project commands.
- Use isolated local test settings and a disposable PostgreSQL database for database-backed/full-suite tests. If one is unavailable, run only safe focused checks and report the full-suite validation as blocked.
- Do not deploy or push.

## Review Focus

- A Python 3.12 resolver must find direct pins that satisfy every declared constraint, and `pip check` must report no broken installed requirements (Task 1).
- Anonymous users opening `/` must still see the role login page, and the active login page must remain unchanged after `LandingView` removal (Task 2).
- Existing `/requests/<integer>/` links must continue to return a permanent redirect to the collaborative management URL (Task 2).
- Removing the legacy Graph draft helper chain must not break the routed delegated Outlook draft, formatted preview or EML flow (Task 2).
- Removing unused dashboard/SQR helpers must not change active dashboard ordering, SQR performance results or exports (Task 2).

---

### Task 1: Align Python dependency pins and runtime documentation

**Files:**
- Modify: `requirements.lock`
- Modify: `README.md` (Django badge and Tech Stack row)

**Interfaces:**
- Consumes: `requirements.txt` direct requirements and the spec's Python 3.12 / Django 6.1 target.
- Produces: a `requirements.lock` whose direct pins satisfy every corresponding range in `requirements.txt`; README identifies Python 3.12 and Django 6.1 without claiming Django 6.1 is LTS.

- [ ] **Step 1: Create the disposable Python 3.12 environment**

Run: `py -3.12 -m venv .venv`, then activate it with `.\.venv\Scripts\Activate.ps1`.

Set `$env:PYTHON_DOTENV_DISABLED = '1'` in the validation PowerShell session.

Expected: `python --version` reports Python 3.12.x, and project commands do not load `.env`. Keep this environment inside the isolated worktree.

- [ ] **Step 2: Reproduce the current resolver conflict**

Run: `python -m pip install --dry-run --ignore-installed -r requirements.txt -r requirements.lock`

Expected: nonzero exit; pip reports conflicting direct pins, including Django and the other packages identified in the audit. Do not change either manifest before recording this baseline.

- [ ] **Step 3: Resolve the declared requirements and update the existing direct-pin lock format**

Resolve `requirements.txt` with pip in the disposable Python 3.12 environment. Update only direct pins in `requirements.lock` that do not satisfy their declared ranges; preserve the current one-pin-per-direct-requirement format and do not add packages or unrelated transitive pins.

- [ ] **Step 4: Update the README Django version**

Change the badge and Tech Stack row from Django 5.2 to Django 6.1, retain Python 3.12, and remove the inaccurate LTS label. Do not change unrelated release-version or deployment instructions.

- [ ] **Step 5: Verify dependency resolution and the environment**

Run: `python -m pip install --dry-run --ignore-installed -r requirements.txt -r requirements.lock`

Expected: exit 0 with all direct requirements resolved.

Run: install `requirements.lock` and `requirements-ci.txt` in the same disposable Python 3.12 environment, then `python -m pip check`.

Expected: installation succeeds and `pip check` reports `No broken requirements found.`

Compare every direct pin against its `requirements.txt` specifier.

Expected: no lock pin violates its declared direct requirement.

### Task 2: Remove confirmed unreachable code without changing routes

**Files:**
- Modify: `hub/test_redirect_security.py`
- Modify: `accounts/tests.py`
- Modify: `accounts/views.py`
- Modify: `hub/views.py`
- Modify: `README.md` (request route description)
- Delete: `templates/hub/request_detail.html`

**Interfaces:**
- Consumes: Task 1's Django 6.1 environment; the existing `hub:request-detail` URL name and permanent redirect remain public behavior.
- Produces: request-detail URL compatibility and role-login behavior covered by tests, with no `RequestDetailView`, orphaned request-detail template, or confirmed definition-only private helper chain.

- [ ] **Step 1: Add a regression test for the legacy request-detail URL**

In `RedirectSecurityTests`, add:

```python
def test_legacy_request_detail_url_redirects_to_collaborative_management(self):
    response = self.client.get("/requests/42/")
    self.assertEqual(response.status_code, 301)
    self.assertEqual(
        response["Location"],
        reverse("hub:request-manage-collab", args=[42]),
    )
```

- [ ] **Step 2: Add a regression test for the root login route**

In `LoginPageTests`, add:

```python
def test_root_route_renders_role_login_for_anonymous_user(self):
    self.client.logout()
    response = self.client.get("/")
    self.assertEqual(response.status_code, 200)
    self.assertTemplateUsed(response, "accounts/login.html")
```

Keep the existing landing-template secret-hygiene test.

- [ ] **Step 3: Run the route and login regression tests before deletion**

Start a uniquely named disposable PostgreSQL 16 container on a loopback-only unused port. Set `DB_HOST=127.0.0.1`, `DB_PORT` to that port, `DB_NAME=request_hub_ci`, `DB_USER=postgres`, `DB_PASSWORD` to a disposable value, `DB_SSLMODE=disable`, and `DB_CONN_MAX_AGE=0`; never copy values from `.env`.

Run the new tests in `hub.test_redirect_security.RedirectSecurityTests` and `accounts.tests.LoginPageTests` with `--settings=request_hub.settings_ci` against this database.

Expected: both tests pass on the current implementation, establishing the behavior to preserve.

- [ ] **Step 4: Remove the unreachable view/template and its newly unused imports**

Delete `RequestDetailView` from `hub/views.py` and `templates/hub/request_detail.html`; remove `DetailView` from imports only if no remaining view uses it. Keep the `request-detail` URL pattern and permanent redirect in `hub/urls.py`.

Remove `LandingView` and its now-unused `TemplateView`, `ROLE_CHOICES`, and `ROLE_LABELS` imports from `accounts/views.py`. Do not delete `templates/landing.html`.

- [ ] **Step 5: Remove only confirmed definition-only helpers**

In `hub/views.py`, remove `_get_sqr_scope_items`, `_get_sqr_performance`, `_admin_sort_account_manager_key`, `_admin_sort_engineer_key`, `_admin_sort_backup_engineer_key`, and `_admin_sort_date_key`. Preserve `_split_sqr_scope_items` and `_get_sqr_performance_export_columns`.

Remove the unused legacy Graph draft chain (`_get_graph_access_token`, `_extract_graph_error_message`, `_create_sqr_approval_graph_draft_json`, `_create_sqr_approval_graph_draft_mime`, `_create_sqr_approval_graph_draft`) only after a fresh reference search confirms there are no callers outside that chain. Preserve active Outlook routes, `_build_sqr_approval_mailto_url`, and dependencies used by other code.

Remove AST-confirmed unused imports (`json` in `accounts/views.py` and `SqrReviewForm` in `hub/views.py`) only after confirming no callers or intentional public re-exports. Keep recovery routes/classes and form-registry entries.

- [ ] **Step 6: Update the stale README route description**

Document that the legacy request-detail URL permanently redirects to collaborative request management; do not imply that `RequestDetailView` still handles requests.

- [ ] **Step 7: Run focused business-flow regression tests**

Run: `python manage.py test hub.test_redirect_security accounts.tests.LoginPageTests hub.test_outlook_drafts hub.test_request_outlook_threads hub.test_sqr_performance hub.test_sqr_prototype hub.tests.DashboardViewTests hub.tests.DashboardLiveDataViewTests hub.tests.SqrApprovalEmailTemplateTests hub.tests.SqrExportTests hub.tests.SqrPerformanceTrackingTests --settings=request_hub.settings_ci --noinput`

Expected: all selected tests pass against disposable PostgreSQL. This covers the preserved redirect/login paths and active Outlook/SQR replacements.

- [ ] **Step 8: Run final repository validation**

Run: `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py collectstatic --noinput`, `node --test scripts_check/test_dashboard_refresh.js scripts_check/test_notification_toasts.cjs scripts_check/test_sqr_refresh.js`, and the complete Django suite using `request_hub.settings_ci` against disposable PostgreSQL.

Expected: checks succeed, no migrations are generated, static collection succeeds, all Node tests pass, and the full Django suite passes. If disposable PostgreSQL is unavailable, stop before any production-configured test; report the full-suite run as blocked and include only the safe checks that did run.

If Docker is available, run `docker build --tag request-hub:cleanup-audit .` as a local build only; do not push or deploy the image.

Run `git diff --check` and inspect the final diff.

Expected: no whitespace errors or unrelated file changes; all audit exclusions remain untouched.
