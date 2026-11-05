# Request Hub: cleanup audit and proposed plan

Date: 2026-11-05. Audited working tree at commit `d573b0b`.
Status: **planning only; no application code, dependencies or data changed.**

## Goal and scope

Remove obsolete code and scratch artifacts without changing permissions, request lifecycle, SQR workflows, emails, recovery or deployment behavior. Review covered tracked Python application code, routes, templates, static assets, dependency manifests, Docker packaging and maintenance scripts. This is a bounded source audit, not proof that every feature works or every unreferenced item is dead.

Existing untracked work in `docs/superpowers/plans/2026-11-05-github-actions-optimization.md` was preserved. The supplied README attachment was read-only. No credentials were read, no production database was contacted, no repair script was executed, and no dependencies were installed. Delegated audit attempts could not start because of a model-availability error; the evidence below was collected directly.

## Confirmed compatibility problem: address before removal

[requirements.txt](../requirements.txt) and [requirements.lock](../requirements.lock) disagree. A local `packaging` specifier comparison confirmed these five incompatible pins:

| Package | Requirements constraint | Lock pin |
|---|---|---|
| Django | `>=6.1.2,<6.2` | `5.2.17` |
| msal | `>=1.39.0,<2` | `1.37.0` |
| cryptography | `>=50.0.2,<51` | `50.0.1` |
| msgpack | `>=1.2.4,<2` | `1.2.3` |
| python-dotenv | `>=1.2.4,<2` | `1.2.2` |

[Dockerfile](../Dockerfile) installs the lockfile; README local setup installs the requirements file. These installation paths therefore do not describe the same supported runtime. The inspected main virtual environment has Python 3.14.6 / Django 5.2.17; the alternate environment has Python 3.13.13 / Django 4.2.30; the Dockerfile targets Python 3.12.13. These are terminal facts, not claims about the IDE-selected interpreter. The audit did not verify package availability or Python compatibility for the proposed higher versions.

**Proposal:** decide the supported Python/Django combination first, then reconcile manifests and regenerate a compatible lockfile. Preserve the tested application behavior; do not silently upgrade Django as part of dead-code deletion. Re-run dependency resolution and `pip check` in a disposable environment and verify the container's Python target. Update the README development instructions with that decision.

## Cleanup candidates and safeguards

High confidence below means strong source evidence, not approval to delete. Search absence is supplemented by route, configuration or registration evidence where available. External callers, operator scripts and stored form definitions still require review.

| Candidate | Evidence | Proposed treatment |
|---|---|---|
| [RequestDetailView](../hub/views.py#L5087) and [request_detail.html](../templates/hub/request_detail.html) | Class has no caller/import found; `hub.urls` maps `request-detail` to a permanent redirect to collaborative management instead. The old class is the only discovered runtime reference to the template. | High-confidence obsolete view/template pair. Add a redirect regression test first; remove the class and template together. **Keep the named URL and redirect** for bookmarks, notifications and external links. Update README's old flow description. |
| [LandingView](../accounts/views.py#L141) | No caller/import found. Project landing and login routes both use `RoleLoginView`. Its `dispatch` always redirects, so its role-card context is not rendered by that dispatch. | High-confidence obsolete view. Remove only the unused class and newly orphaned imports. Handle the landing template separately because a test reads it. |
| [Legacy dashboard sort helpers](../hub/views.py#L1316) | `_admin_sort_account_manager_key`, `_admin_sort_engineer_key` (1796), `_admin_sort_backup_engineer_key` (1804), `_admin_sort_date_key` (1812) each appear only at their definition in scanned application sources. Active SQL sorting is in `hub/services/dashboard_queries.py`. | High-confidence helper candidates. Verify all supported sort keys against PostgreSQL and preserve ordering/null behavior before removal. |
| [SQR helper candidates](../hub/views.py#L229) | `_get_sqr_scope_items` (229) and `_get_sqr_performance` (2895) each appear only at their definition. The latter builds a dictionary over all performance rows and has an unused `prefetched` argument. | High-confidence helper candidates. Remove after confirming current approval context and performance/export paths; preserve `_split_sqr_scope_items` and export helpers, which have callers. |
| [Old Graph draft chain](../hub/views.py#L1670) | `_create_sqr_approval_graph_draft` (1760) has no discovered external caller; token, error, JSON and MIME helpers form its internal chain. Current routed draft flow uses `hub/outlook_views.py` and `hub/services/outlook_drafts.py`. | Verify-and-remove candidate. Check each helper's references independently before deleting the chain; preserve delegated OAuth drafts, ACS delivery, formatted previews and EML downloads. Do not remove shared `msal`/`requests` dependencies. |
| [ProfileCompletionMiddleware](../accounts/middleware.py#L60) | No reference found outside its definition; absent from configured `MIDDLEWARE`. `_is_exempt` is used only by this inactive middleware. | **Policy decision required:** intentional retirement or missing enforcement? Do not enable or delete it just to optimize. Preserve active password-change middleware and shared path helpers. Review the model method and exempt setting separately. |
| [Small unused symbols/imports](../hub/mixins.py#L21) | `RequestorRequiredMixin` and `hub/form_editor.py:is_baseline_build` have definition-only matches. AST inspection found unused `json`, `BackupDataView`, `RestoreDataView` imports in `accounts/views.py`, and an unused `SqrReviewForm` import in `hub/views.py`. Accounts URLs import recovery views directly. | Low-risk cleanup after checking intentional public re-exports. Remove imports, **not** recovery views or `SqrReviewForm`: both remain in active routing/form registries. Preserve `__future__.annotations`. |
| [Unreferenced template variants](../templates/hub/request_admin_form.html) | No discovered full-name runtime/include/test reference for `hub/request_admin_form.html`, `hub/sqr_review_form.html`, `hub/partials/dashboard_admin_status_pills.html`, `hub/sqr_approval_email_draft.html`, `hub/emails/request_acknowledgement.html`. Active request admin view uses `request_manager_form.html`. | Verify-and-remove candidates, not blanket deletions. Search dynamic template construction, emails, stored form configuration and deployment overrides; add render/behavior coverage for their replacements. |
| [Scratch quotation inspectors](../_inspect_quo.py), [_inspect_quo_styles.py](../_inspect_quo_styles.py), [_inspect_quo_summary.py](../_inspect_quo_summary.py), [views_diff.patch](../views_diff.patch), [apply_pm_ess.py](../apply_pm_ess.py) | Tracked root artifacts with no discovered runtime/automation references. All three inspectors require `static/media/quo.xlsx`, which is absent locally and not tracked. `apply_pm_ess.py` rewrites source using old regex blocks. Five files total 23,915 bytes. Docker already excludes them. | Archive/remove with owner approval. Inspectors cannot run as written in this checkout. Never execute the rewrite script to verify it. These are repository-hygiene savings, **not** current image/runtime savings. |
| [One-off request repair](../scripts_check/repair_906.py) and [forensics](../scripts_check/forensics_906.py) | Repair explicitly targets request 906/revision 1 and writes lifecycle events. These are operator entry points, not imported runtime modules. | Require confirmation the incident is closed; archive operational evidence before removal. Never replay on live data as a cleanup check. Preserve the Node regression tests in this directory. |

## Items to retain

- **All current static CSS/JS/image assets:** every tracked static file had at least one full-relative-name reference in the scanned application/template/test corpus. This does not prove every selector or JS function is needed, but it does not justify whole-file deletion. `sqr-tracker-prototype` assets are referenced, despite their name.
- **Signals and Django admin classes:** single textual occurrences can be normal registration. `AccountsConfig.ready()` imports sign-in receivers; `HubConfig.ready()` imports request signals. Decorated receivers and admin registrations are not dead code.
- **Models and migrations:** do not remove migrations, historical seed operations, soft-deleted data or recovery models. Historical migrations may import old symbols or reproduce state required for a fresh database.
- **Management commands and production scripts:** these are invoked by operators, schedules and automation, not ordinary Python callers. Retain SLA checks, user sync, changelog sync, account pruning, deploy/migrate scripts and their tests unless owners retire them explicitly.
- **[landing.html](../templates/landing.html):** no active route currently renders it, but `accounts.tests.SecretHygieneTests` reads it directly. Retire/replace that test intentionally before removal; do not silently break it.
- **[clear_engineer_outlook_lock_on_reassignment](../hub/views.py#L1307):** deliberately returns zero and is called by three update flows plus imported by tests. Not unused. Changing acknowledgement policy is outside cleanup scope.
- **Generated static/media directories and virtual environments:** local artifacts are not source cleanup targets. `staticfiles/` and `media/` are excluded from Git and Docker context; `.venv313` has its own ignore rule and Docker's wildcard excludes virtual environments. Do not delete shared local files to improve repository metrics.

## Recommended approach

1. **Conservative cleanup (recommended):** align dependency declarations, then remove confirmed uncalled helpers/imports and retired view/template pairs in small batches. Requires the least behavioral change and gives easy rollback.
2. **Broader retirement:** also remove inactive policy code, operator incident scripts and all unreferenced template variants after owner/configuration review. Higher risk of dropping intentional features or external entry points.
3. **Large refactor:** split views/styles and redesign imports now. Much higher review surface; do not combine with dead-code removal. The large files are active, not evidence of dead code.

The recommendation is a proposal, not an approved implementation choice.

## Execution plan after approval

### Phase 0 — establish a reproducible baseline

- Isolate cleanup from existing work; record the current commit and keep unrelated changes untouched.
- Confirm the supported Python/Django versions and profile-completion policy. Reconcile requirements/lockfile without unreviewed major-version upgrades.
- Use an explicitly disposable PostgreSQL database and mock external mail/Graph integrations. Do not use production `.env` values for tests.
- Run the existing complete Django suite once against that baseline, including migration tests. Record failures before changes. Node checks run separately without installing packages.

### Phase 1 — low-risk source pruning

- Add regression coverage for `request-detail` redirect and landing/login behavior before retiring views.
- Remove confirmed unused imports and uncalled helpers; review public re-exports and Django registrations.
- Retire `RequestDetailView` plus its template and `LandingView` in a small reviewable batch. Keep URL compatibility.
- Run accounts auth/recovery, request lifecycle/access, dashboard sort/performance, SQR email/performance/export and form-registry tests relevant to each batch.

### Phase 2 — gated artifact and variant retirement

- Resolve profile-policy intent, dynamic template/DB-backed form references, Graph helper ownership and incident-script retention.
- Remove approved obsolete template variants and scratch artifacts. Never remove tests merely because they stop passing; rewrite only coverage tied to intentionally retired behavior.
- Run all three Node regression files, current page-asset tests and static collection to a temporary output directory.
- Update README entry points/setup instructions and directly related feature docs. Do not rewrite unrelated documentation.

### Phase 3 — verify and measure

- Run the complete suite on disposable PostgreSQL after all batches; run migration drift detection and a fresh migration test. Validate the container build and release/health/readiness endpoints without deploying.
- Smoke-test requestor, engineer, PM-ESS, PM-ESG and admin flows: login, dashboards, request management, SQR edit/review/export, Outlook draft/EML, notifications and recovery.
- Compare tracked file count/bytes, compressed static output and container size. Compare dashboard/SQR query counts and timings using existing performance tests. Make no speedup claim without measurement.
- Use separate commits per logical batch when explicitly requested. Roll back with Git revert; no schema/data deletion is proposed, so cleanup rollback should not require a database restore.

## Validation actually performed

- Parsed **169 tracked Python application files**, including migrations/tests, with `ast.parse`: no syntax errors.
- Django `check`: **no issues (0 silenced)** using dotenv-disabled settings and an in-memory database override.
- **7 Django smoke tests passed:** `hub.test_release_health`, `hub.test_page_assets.LiveRefreshScriptTests`, `hub.tests.ProductCategoryChoiceTests`. These avoid creating a database; readiness connection was mocked.
- **10 Node tests passed:** `scripts_check/test_dashboard_refresh.js`, `scripts_check/test_notification_toasts.cjs`, `scripts_check/test_sqr_refresh.js`.
- Compared lockfile pins to requirements constraints: the five mismatches above are confirmed.
- No full PostgreSQL suite, authenticated browser smoke test, dependency install/resolution, external email/Graph test or Docker build was performed. Production-only paths and deployment compatibility remain unverified. The passing smoke tests are not proof the entire app works.

## Expected benefit

Lower maintenance burden, fewer misleading legacy paths and reproducible dependency setup. Dead uncalled functions generally do not affect request-time queries; removing already-Docker-excluded scratch files does not shrink the current runtime image. Meaningful performance improvement requires measured active-path work, kept separate from safe cleanup.
