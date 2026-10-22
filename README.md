<div align="center">

<p align="center">
  <img src="https://raw.githubusercontent.com/lloydismael/request-hub/dev/static/img/phil-data-full-logo.png" width="1000" alt="Phil-Data Logo">
</p>


# Request Hub

**A role-based request and activity management platform built on Django.**  
Coordinate engineering work, enforce SLA timelines, and gain operational visibility all from a single web portal.

[![Docker](https://img.shields.io/badge/Docker-lloydismael12%2Frequest--hub-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/lloydismael12/request-hub)
[![Latest Tag](https://img.shields.io/badge/Latest-v51.9-0ea5e9)](https://hub.docker.com/r/lloydismael12/request-hub/tags)
[![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-7952B3?logo=bootstrap&logoColor=white)](https://getbootstrap.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Azure-336791?logo=postgresql)](https://azure.microsoft.com/en-us/products/postgresql/)
[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-lightgrey)](./LICENSE)

</div>

---

## Admin Form Editor

Admin accounts can open **Management → Form Editor** to save and publish form presentation and custom fields, preview changes, and restore prior versions. Core validation and credential controls remain protected. See the [usage and coverage guide](docs/form-editor.md).

## Latest Release Snapshot

- **Current image tag:** `lloydismael12/request-hub:v51.9`
- **App version shown in profile:** `v51.9`
- **Latest update included:**
      - Admin form editor and activity-log initialization fix, including current workspace updates. Immutable tag only — do not retag `latest`.

---

## Table of Contents

- [UI Preview](#ui-preview)
- [Features](#features)
- [Workflow](#workflow)
- [Role Permissions](#role-permissions)
- [Data Model](#data-model)
- [Application Flow](#application-flow)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Local Development](#local-development)
- [Docker Setup](#docker-setup)
- [Environment Variables](#environment-variables)
- [Database & Migrations](#database--migrations)
- [Scheduled Jobs](#scheduled-jobs)
- [Deployment](#deployment)
- [Security](#security)
- [License](#license)

---

## UI Preview

> Role-aware dashboards, glass-morphism UI, dark mode, and responsive layouts across all pages.

| Page | Description |
|------|-------------|
| 🔐 **Login** | Clean auth page with branded background and forced password-change flow |
| 📊 **Dashboard** | Filterable request table with SLA indicators, stat pills, and status badges |
| 📄 **Request Detail** | Full lifecycle view status log, communication actions, SQR submission |
| 🛠️ **Manage Request** | Admin/PM form for assigning engineers, changing status, and writing updates |
| 📈 **Reports Operational** | Charts for requestor volume, engineer load, engagement types, product categories |
| ⏱️ **Reports Activity** | Billable vs non-billable hours, location mix, engineer hour breakdown |
| 👤 **Profile** | User profile page with avatar, account-wide themes with banner gradients, and contact details |
| 🔔 **Notifications** | In-app notification center for assignments and workflow events |

---

## Features

### Personal Settings
- **My Profile → Settings** groups account preferences into Appearance, Accessibility, Experience, Performance, and Session cards.
- Change multiple switches, then choose **Save Changes**. **Discard Changes** restores the saved values; unsuccessful saves keep your selections for retry. Saving reloads the page to apply preferences.
- Display layout applies immediately to the current device only. Theme changes are saved separately in **Themes**.
- Admin-only backup and recovery tools appear in a separate Recovery Center. Uploaded backups must pass validation and impact preview before confirmed replacement.

### Backup & Recovery
- Administrators can open **My Profile → Settings → Recovery Center** to create and download persisted snapshots, inspect recent recovery activity, and search/recover deleted requests.
- **Validate & Preview** checks uploaded JSON backups before any replacement. Review current versus incoming record counts, then type **RESTORE** and confirm to proceed. Preview authorization expires and cannot be reused after a completed restore.
- A verified safety snapshot is saved before replacement. Restoration runs transactionally, verifies results, and resets database sequences; failed replacement leaves current data intact and retains the safety snapshot.
- New snapshots use a versioned format with integrity metadata. Checksums detect accidental corruption, not authenticity. Use only trusted backups.
- Application snapshots exclude user accounts, credentials, and media file contents. Existing users referenced by records must still exist. Legacy backups have narrower coverage and may be rejected when replacement would cascade into uncovered records.
- Stored snapshots share the application database. Download and protect off-host copies separately; scheduling, automatic retention, and external storage are not configured by this feature.
- Deployment requires `python manage.py migrate` to create snapshot, preview-token, and recovery-event tables, followed by `python manage.py collectstatic --noinput`. Recovery locks related tables during snapshot validation and restoration; large operations can temporarily delay other writes. The default backup/upload limit is 25 MB (`BACKUP_MAX_BYTES` in Django settings).
- Validation uses a transactional restore rehearsal that is rolled back, so invalid relationships or constraints are discovered without committing hub changes. Both manual and pre-restore safety snapshots must pass this rehearsal.

### Account Themes
- Open **My Profile → Themes** (also available from the profile banner or Settings).
- Choose Blue, Sunset, Forest, Crimson, Slate, Aurora, Rose, or Teal using mouse or keyboard. Preview updates buttons, highlights, and the profile banner; **Cancel preview** restores the saved theme.
- **Apply Theme** saves only your account's theme, independently of profile details and settings, and makes it available across devices. The existing light/dark toggle remains unchanged.
- Existing banner selections become account themes using the existing `banner_gradient` preference; no database migration is needed.

### 📝 Request Management
- Create requests with reference codes, priority (Medium / High), engagement type, and product category
- Assign primary and backup engineers with capacity enforcement (max 5 ongoing; max 3 when a deployment is active)
- SLA due-date auto-calculation (Medium = 5 days, High = 3 days)
- Overdue detection and visual indicators on the dashboard
- Status log history and admin-authored request updates

### 📊 Dashboard & Reporting
- **Admin/PM dashboard**: filterable, sortable request table with stat pills (All / Ongoing / Completed / Overdue)
- **Engineer dashboard**: Assigned vs Backup tabs, personal activity report graph
- **Requestor dashboard**: personal metrics, request creation, and progress tracking
- **Reports page (Operational)**: stacked bar charts requests by requestor, by engineer, by engagement type, by product category
- **Reports page (Activity)**: billable vs non-billable hours, work location mix (donut), activity type breakdown, paginated engineer log
- Chart **expand button** on every chart card for full-screen view

### 🔔 Notifications & Communication
- In-app notification center for assignment events and workflow changes
- Communication action log (Teams, Outlook, Phone) with channel tagging
- Optional Teams chat topic field per request

### 🧾 SQR (Service Quotation Request)
- Engineers and reviewers manage SQR entries directly from the tracker
- Tracks quotation details, SSE/PM man-hours, managed support amount, discounting, approval metadata, and revenue fields
- Status labels in the tracker:
      - `submitted` ? **For Processing**
      - `for_revision` ? **For Revision**
      - `reviewed` ? **Approved**
- Approved flow supports two user options:
      - automatically copy the formatted email body upon selecting the ready draft option, then launch the local mail app with recipients and subject populated; paste with Ctrl+V (Outlook must be the default `mailto` handler)
      - download a formatted `.eml` file with the branded quotation layout
- Selecting either option saves the approval, refreshes the status badge and approval dates, then opens the selected advisory. Cancel leaves the status unchanged.
- Ready drafts include project details, engineer/reviewer, service man-hours and rates, subtotal, discount, final total, validity, and supporting links. Preview and `.eml` use the same HTML builder; the `.eml` embeds the logo.
- Automatic copying starts during the selection click, waits for approval to save, and copies the saved quotation before launching Outlook. If automatic copying is blocked or unsupported, the preview opens with manual copy and `.eml` options. Browsers may require confirmation before opening Outlook.
- Copying requires browser clipboard permission and rich-text support in Outlook. If HTML copying fails, plain text is offered; if clipboard access is blocked, select the manual-copy body or download `.eml`. Copy/download failures do not revert approval. No email is sent automatically.
- Managed support date behavior:
      - `AI` = Post-service warranty end date
      - `AJ` = Support start date (manual value or computed fallback)
      - `AK` = Support end date (`AJ + 365 days`)
      - `AJ` and `AK` show `NA` when column `P` has no managed support value

### 👤 User & Profile Management
- Profile photos stored in the database (no external media bucket required)
- Configurable banner gradient (Blue, Sunset, Forest, Crimson, Slate, Aurora, Rose, Teal)
- Forced password-change on first login or admin reset
- Microsoft Graph user sync via `fetch_phildata_users` management command

---

## Workflow

```
Create  ──────▶  Review & Assign  ──────▶  Acknowledge  ──────▶  Work  ──────▶  Complete  ──────▶  SQR
(requestor       (Admin / PM-ESG:          (engineer      (assigned tab:          (end_date   (post-engagement
 roles:           engineer + due date,      confirms       log activity,           set,        quotation + revenue
 requestor,       lifecycle                assignment)     comms via               lifecycle     tracking, submitted
 requestor-ess,   created → assigned)                      Outlook / Teams,        completed)    → reviewed)
 PM-ESS,
 PM-ESG)
```

- **SLA** is auto-calculated at assignment (Medium = 5 days, High = 3 days); `check_sla` flags overdue daily.
- **Assignment capacity**: max 5 ongoing per engineer; max 3 while a deployment is active.
- **Status updates** are recorded on every save (StatusLog) and notify assignees; completion is restricted to Admin / PM-ESG and the assigned engineer.

---

## Role Permissions

| Permission | Requestor | Requestor-ESS | PM-ESS | PM-ESG | Engineer | On Hold | Admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Create request | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| View own requests | ✅ | ✅ | ✅ | ✅ | ✅ (assigned) | ➖ (read-only) | ✅ |
| View all requests | ❌ | ❌ | ➖ (all + mine tabs) | ✅ | ❌ (assigned only) | ❌ | ✅ |
| Assign engineers | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ |
| Update request status | ➖ (own only) | ➖ (own only) | ➖ (own only) | ✅ | ✅ (assigned) | ❌ | ✅ |
| View assigned requests | ❌ | ❌ | ✅ | ✅ | ✅ | ➖ (read-only) | ✅ |
| Log engineer activity | ❌ | ❌ | ❌ | ❌ | ✅ | ➖ | ❌ |
| Submit SQR | ❌ | ❌ | ❌ | ✅ | ✅ | ➖ | ✅ |
| Access reports | ❌ | ❌ | ❌ | ✅ | ➖ (own graph) | ❌ | ✅ |
| Manage users | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Export CSV | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ |

> ✅ full access · ➖ scoped or restricted · ❌ no access. All checks are enforced server-side (see `hub/mixins.py`).

> **PM-ESS** = Project Manager (ESS division) sees both "All Requests" and "My Requests" tabs.  
> **PM-ESG** = Project Manager (ESG division) full admin-level request management.  
> **On Hold** = Engineer account suspended from new assignments; retains read-only access to current tickets.

---

## Data Model

```
User (accounts/models.py) ── roles: requestor | requestor_ess | pm_ess | pm_esg | engineer | on_hold | admin
  username · email · phone_number · department · banner_gradient · profile_photo → StoredFile
  ├─ 1:N  Request.requestor ───────── (requestor, requestor_ess, pm_ess, pm_esg)
  ├─ 1:N  Request.engineer / backup_engineer ── (engineer only)
  ├─ 1:N  EngineerActivityLog · StatusLog · RequestCommunication · Notification
  └─ 1:1  StoredFile (profile photo, DB-backed binary: name, data, content_type, size)

Account ── 1:N Request · 1:N EngineerActivityLog
  name (unique)

Request (hub/models.py)
  reference_code (auto, unique) · account → Account · account_manager
  priority: medium | high (SLA +5 / +3 days)
  engagement: opportunity | training | support | inquiry | deployment | project_management | certification
  product_category: Azure | M365 | VMware | Omnissa | Hybrid | Dell | HP | Network | Veeam | Others
  status: ongoing | completed · lifecycle_stage: created | assigned | acknowledged | ongoing | completed
  engineer / backup_engineer → User · start_date · due_date · end_date · description · teams_chat_topic
  is_deleted (soft delete) · assignment_revision
  ├─ 1:N  RequestLifecycleEvent ── stage transitions (actor, owner labels, idempotency_key)
  ├─ 1:N  StatusLog ── author → User, message
  ├─ 1:N  RequestCommunication ── user → User, channel: outlook | teams
  └─ 1:N  EngineerActivityLog (optional link) · Notification · SqrSubmission (linked_request)

EngineerActivityLog
  engineer → User · account → Account · request → Request (optional)
  activity_type: learning | internal_support | on-call_support | pre-sales | project_management | training | deployment
  location: wfa | office | onsite · actual_hours · is_billable · details
  status: planned | in_progress | completed

SqrSubmission (+ SqrSubmissionChange / SqrSubmissionHistory audit trail)
  reference_code (auto, unique) · linked_request → Request
  submitted_by / engineer / pm_esg_reviewer → User · customer + project fields
  status: submitted (For Processing) | for_revision (For Revision) | reviewed (Approved)
  proposal: submitted_pending | negotiation_review | closed_won | closed_lost | closed_canceled
  delivery: on_track | off_track | at_risk | completed | cancelled
  amounts: quotation_total · discount_rate · sse / pm / managed_support · hourly_rate
  dates: validity_due · po_pnl · delivery milestones · sqr_folder_link · revenue_overview

Notification ── recipient → User · related_request → Request · event: system | new_request | assignment · is_read
```

---

## Application Flow

```
Browser Request
  │
  ▼
Django URL Router (request_hub/urls.py)
  ├─▶ /accounts/* ── login, profile, notifications
  │     Middleware: PasswordChangeRequiredMiddleware · ProfileCompletionMiddleware
  ├─▶ /dashboard/ ── DashboardView (role-dispatched)
  │     Admin/PM-ESG → full request table + filters
  │     Engineer → assigned/backup tabs + personal graph
  │     Requestor/PM → personal metrics + request list
  ├─▶ /requests/* ── RequestDetailView · RequestAdminUpdateView (Admin/PM-ESG) · RequestUpdateView (creators, own)
  │     StatusLog write on every save · SLA check on due_date · Notification signals (hub/signals.py)
  ├─▶ /reports/ ── RequestReportView (Admin/PM-ESG; operational / activity tabs)
  │     Chart.js 4.4 stacked bar + doughnut · EngineerActivityLog CRUD · SQR integration · CSV export
  ├─▶ /sqr/* ── SQR tracker, proposal / revenue / delivery updates, .eml download + Outlook draft
  └─▶ /admin/ ── Django admin (superusers only)

Signals & Background Jobs
  ├── post_save Request → Notification for assignees
  ├── check_sla (daily) → mark overdue, send email via ACS
  └── fetch_phildata_users (management cmd) → sync users from MS Graph
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12 Django 5.2 LTS |
| **Database** | PostgreSQL (Azure Flexible Server in production) |
| **Frontend** | Django Templates Bootstrap 5.3 Bootstrap Icons Chart.js 4.4 |
| **Auth** | Django `AbstractUser` custom role system MSAL (Microsoft Graph) |
| **Email** | Azure Communication Services (ACS); sender address comes from `ACS_EMAIL_SENDER` |
| **Containerisation** | Docker Docker Compose Gunicorn (WSGI) |
| **Media Storage** | Database-backed `StoredFile` model (no S3/blob required) |
| **Deployment** | Azure App Service (container) see [docs/azure-app-service-deployment.md](docs/azure-app-service-deployment.md) |
| **CI / Image** | `lloydismael12/request-hub` on Docker Hub |

---

## Project Structure

```
request-hub/

+-- accounts/                   # Users, auth, profile media
   +-- models.py               #   User (roles, profile photo) + StoredFile (DB media)
   +-- views.py                #   Login, profile, notifications
   +-- backends.py             #   Email-or-username auth backend
   +-- middleware.py           #   Password-change + profile-completion guards
   +-- storage.py              #   DatabaseMediaStorage for profile photos
   +-- forms.py · urls.py · admin.py · tests.py · migrations/

+-- hub/                        # Core business logic
   +-- models.py               #   Request, Account, RequestLifecycleEvent, StatusLog,
                               #   RequestCommunication, EngineerActivityLog,
                               #   SqrSubmission (+Change/History), Notification
   +-- views.py                #   Dashboard, detail, reports, SQR, activity, exports
   +-- forms.py                #   Request, admin, activity, SQR forms
   +-- mixins.py               #   Role-based access mixins
   +-- signals.py              #   Notification triggers
   +-- constants.py            #   Shared choices / constants
   +-- urls.py
   +-- services/               #   microsoft_graph, notifications, request_lifecycle
   +-- management/commands/
       +-- check_sla.py        #   Daily SLA overdue checker
       +-- fetch_phildata_users.py  # MS Graph user sync
   +-- tests.py · test_lifecycle.py · test_migrations.py

+-- request_hub/                # Django project config
   +-- settings.py
   +-- urls.py
   +-- wsgi.py

+-- templates/
   +-- base.html               # Shared layout (navbar, dark-mode, notifications)
   +-- landing.html
   +-- accounts/               # Login, profile, notification templates
   +-- hub/                    # Dashboard, detail, report, SQR templates

+-- static/
   +-- css/app.css             # Custom styles (glass-card, rpt-*, dbd-*, rmf-*)
   +-- js/                     # Theme, charts, navbar, toasts, transitions
   +-- img/                    # Logos and backgrounds

+-- docs/
   +-- azure-app-service-deployment.md

+-- Dockerfile
+-- docker-compose.yml
+-- entrypoint.sh
+-- manage.py
+-- requirements.txt
+-- requirements.lock
+-- LICENSE
```

---

## Local Development

### 1. Create virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure environment

```powershell
Copy-Item .env.example .env
# Fill placeholders only. Never put application login usernames or passwords in .env.
```

### 3. Apply migrations & run

```powershell
python manage.py migrate
python manage.py runserver
```

Open `http://127.0.0.1:8000/`

---

## Docker Setup

### Pull and run from Docker Hub

```powershell
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env lloydismael12/request-hub:v51.9
```

### Build locally

```powershell
$containers = docker ps --format "{{.ID}} {{.Ports}}" | Where-Object { $_ -match "0\.0\.0\.0:8000->8000/tcp|:::8000->8000/tcp" }
$containers | ForEach-Object { docker rm -f (($_ -split ' ')[0]) }
docker build --pull --no-cache --build-arg APP_VERSION=v51.9 -t lloydismael12/request-hub:v51.9 .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -e APP_VERSION=v51.9 lloydismael12/request-hub:v51.9
```

### Push to Docker Hub

```powershell
docker push lloydismael12/request-hub:v51.9
# Do not retag or push latest until CVE scans on v51.9 are clean.
```

### Compose (with local PostgreSQL)

```powershell
docker compose up --build
docker compose exec web python manage.py migrate
```

> If you are running the image on another machine, make sure the `.env` file exists on that machine and the `--env-file` path points to the correct local file.

---

## Environment Variables

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key. Required when `DJANGO_DEBUG` is `False`; must not be empty or `insecure-development-key`. |
| `DJANGO_DEBUG` | `True` for local HTTP development, `False` for production (fail-closed). |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed host names |
| `DJANGO_PRIMARY_DOMAIN` | Canonical domain(s), comma-separated; auto-added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` (default `esgrequesthub.com`) |
| `DJANGO_PUBLIC_BASE_URL` | Absolute base URL for assignment, SQR notification, and closing email links, independent of the incoming host (default `https://esgrequesthub.com`) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins, e.g. `https://esgrequesthub.com` |
| `DJANGO_DEFAULT_FROM_EMAIL` | Default from address (default `ESG Request Hub <no-reply@esgrequesthub.com>`) |
| `DB_NAME` | PostgreSQL database name |
| `DB_USER` | Database username. Required when `DJANGO_DEBUG` is `False`. |
| `DB_PASSWORD` | Database password. Required when `DJANGO_DEBUG` is `False`. |
| `DB_HOST` | Database host (e.g. Azure PostgreSQL hostname). Required when `DJANGO_DEBUG` is `False`. |
| `DB_PORT` | Database port (default `5432`) |
| `ACS_EMAIL_CONNECTION_STRING` | Azure Communication Services connection string |
| `ACS_EMAIL_SENDER` | ACS sender address (verified sender `DoNotReply@esgrequesthub.com`) |
| `PHILDATA_TENANT_ID` | Microsoft Entra tenant ID |
| `PHILDATA_CLIENT_ID` | App registration client ID |
| `PHILDATA_CLIENT_SECRET` | App registration client secret |
| `PHILDATA_DOMAIN` | Default company domain (see `.env.example`) |
| `PHILDATA_GRAPH_SCOPE` | Default `https://graph.microsoft.com/.default` |

> Never commit real secrets, tokens, passwords, or connection strings to source control.

> Application login usernames and passwords must not be stored in `.env`. Admin password resets issue a one-time temporary password instead of a shared default. `DJANGO_DEFAULT_USER_PASSWORD` is rejected if present.

> `.env.example` is the only committed template. Copy it to `.env` (gitignored and dockerignored) and fill values locally or in Azure App Service / Key Vault.

---

## Database & Migrations

```powershell
# Apply all pending migrations
python manage.py migrate

# After model changes
python manage.py makemigrations
python manage.py migrate

# Inspect current state
python manage.py showmigrations
```

---

## Scheduled Jobs

### SLA Monitoring (run daily)

```powershell
# Local
python manage.py check_sla

# Docker
docker compose exec web python manage.py check_sla
```

Schedule with Windows Task Scheduler, Linux `cron`, or Azure Container Apps scheduled jobs.

### Microsoft Graph User Sync

```powershell
# Fetch up to 25 users, sample 10
python manage.py fetch_phildata_users --limit 25 --sample 10

# Include all users (not only the default company domain)
python manage.py fetch_phildata_users --include-non-domain
```

---

## Deployment

Full Azure App Service container deployment guide:

[docs/azure-app-service-deployment.md](docs/azure-app-service-deployment.md)

**Key steps:**
1. Push image to Docker Hub (`lloydismael12/request-hub:v51.9` or your next release tag). Do not retag `latest` until scans are clean.
2. Set App Service container to the target tag
3. Configure all environment variables in App Service Configuration (`DJANGO_SECRET_KEY`, `DB_HOST`, `DB_USER`, and `DB_PASSWORD` are required when `DJANGO_DEBUG=False`)
4. Ensure Azure PostgreSQL Flexible Server firewall allows the App Service outbound IPs
5. Run migrations via the App Service console or a startup script

---

## Security

- Store Django, database, ACS, and Graph secrets in `.env` or Azure App Service / Key Vault, never in source, HTML, or image layers
- `.env` is gitignored and dockerignored; only `.env.example` (empty placeholders) is committed
- Rotate any exposed credentials immediately
- Production boot is fail-closed: missing `DJANGO_SECRET_KEY`, `DB_HOST`, `DB_USER`, or `DB_PASSWORD` refuses to start when `DJANGO_DEBUG` is false
- Login failures are throttled at 5 attempts / 15 minutes per IP+username
- Database media files are served only under the `profile_photos/` prefix; other prefixes 404
- Admin password reset issues a one-time temporary password and sets `must_change_password`
- `PasswordChangeRequiredMiddleware` enforces password rotation on flagged accounts
- `ProfileCompletionMiddleware` blocks access until profile fields are filled
- All role checks are enforced server-side via `LoginRequiredMixin` + custom role mixins
- Referer-based return redirects and back links accept only the current request host; HTTPS requests reject HTTP destinations. Missing or unsafe values use named dashboard, SQR, or notification routes. Request-management POST redirects use canonical named routes rather than the incoming path.
- Redirect/CSRF regression checks: `python manage.py test hub.test_redirect_security`. After publishing changes, rerun CodeQL on the updated branches and confirm alert closure; local tests do not dismiss GitHub alerts.
- PostgreSQL connections use SSL in production (enforced by Azure Flexible Server)
- Static files served by WhiteNoise, no user-uploaded files exposed via the filesystem
- `GET /healthz/` returns `ok` for container probes without authentication

---

## Continuous Integration

The `Container Security` GitHub Actions workflow runs on every push to `dev` or `master`, pull requests targeting `master`, and manual dispatch. Application and template changes are included; there are no path filters. Superseded CI runs are cancelled.

Three independent jobs validate each change:

- **Django Validation** uses Python 3.12 and disposable PostgreSQL 16. It installs `requirements.lock`, checks dependency compatibility and Django configuration, checks production security settings, detects missing migrations, collects static files, and runs the complete Django test suite with migrations enabled.
- **Python dependency audit** checks dependencies with `pip-audit`.
- **Build, scan, and publish SBOM** builds the Docker image without pushing it, generates an SPDX SBOM, and fails on fixable HIGH/CRITICAL vulnerabilities found by Trivy. Generated scan reports and SBOM artifacts are retained for 14 days, including after scan failures. Fork PRs retain report artifacts without attempting a privileged code-scanning upload.

CI uses only test credentials and an isolated database. It does not publish container images, deploy to Azure, or require production secrets. The separate production-configuration check disables debug mode; application tests keep debug mode enabled to avoid production HTTPS redirects.

To run manually, open **Actions > Container Security > Run workflow** and select the branch containing this workflow. Commit and push local changes before testing them on GitHub.

To reproduce Django validation, install `requirements.lock` in a Python 3.12 virtual environment and configure a disposable PostgreSQL database with database-creation privileges. Do not use a production database or a live `.env`. Set test-only `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=True`, `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver`, `DJANGO_PUBLIC_BASE_URL=https://esgrequesthub.com`, and the `DB_*` connection values. The canonical URL is needed by existing email-format assertions; it does not connect CI to production. For a local non-TLS test database, use `DB_SSLMODE=disable` and `DB_CONN_MAX_AGE=0`. Leave ACS and Graph credentials unset.

Run these commands in order:

```text
python -m pip check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test --noinput --verbosity 2
```

Separately set `DJANGO_DEBUG=False` and run `python manage.py check --deploy --fail-level WARNING`, then restore the test configuration.

After successful GitHub runs, configure branch protection for `master` to require all three job checks and pull-request review. Branch protection is a repository setting and is not enabled by the workflow itself.

---

## License

Copyright © 2026 Phil-Data Business Systems Inc. All rights reserved.

This software is proprietary. See [LICENSE](./LICENSE) for the full terms.
Visibility of this repository does not grant any right to use, copy, modify, or distribute the software.

---

<div align="center">

Built and maintained by **Phil-Data Business Systems Inc.**  
Docker Hub [`lloydismael12/request-hub`](https://hub.docker.com/r/lloydismael12/request-hub)

</div>
