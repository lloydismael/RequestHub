# Page Load and Navigation Performance Implementation Plan

> **For agentic workers:** Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The original request authorized investigation/planning; the follow-up authorized source implementation in the current checkout, not deployment.

**Goal:** Remove long startup waits and make authenticated page loading and navigation measurably faster without changing business behavior.

**Architecture:** Keep server-rendered Django and PostgreSQL. Stabilize App Service startup, establish route/browser/query baselines, then bound page work using database filtering/aggregation/pagination and page-specific assets. Reuse existing health endpoints, immutable static-file delivery, and permission checks.

**Tech Stack:** Django 5.2, PostgreSQL, Gunicorn, WhiteNoise, Bootstrap, vanilla JavaScript, Azure App Service Linux containers.

**Spec:** [Approved design](#approved-design), recorded below from the approved investigation discussion.

## Approved design

Use staged startup, measurement, and code optimization. Preserve the existing visual identity, permissions, full-dataset report totals, search semantics, exports, business-calendar calculations, and edit state. Prioritize startup reliability, authenticated performance measurements, bounded dashboard/SQR work, and lighter navigation effects. Do not upgrade Azure SKUs, add Redis/CDN, or rewrite navigation as an SPA without new evidence and approval. Reuse `/healthz/` and `/readyz/`; investigation confirmed these already exist and respond successfully, so a new health endpoint is unnecessary.

### Approaches considered

1. **Staged startup, measurement, and code optimization — selected.** Addresses measured startup delay and observed code scaling risks while retaining the existing stack.
2. **Azure/startup configuration only.** Fastest operational improvement, but leaves dataset growth and browser rendering costs untouched.
3. **Partial-page navigation redesign.** Could reduce shell reloads, but increases history, script lifecycle, permissions, accessibility, and stale-state complexity. Defer until simpler changes are measured.

## Global constraints

- The investigation was read-only. Subsequent source implementation was authorized; production settings, data/schema and infrastructure changes still require separate approval.
- Preserve Admin, PM-ESG, PM-ESS, engineer, backup-engineer, and requestor access rules.
- Pagination must not truncate report totals, searches, filters, or exports.
- Preserve Manila dates, working-day/holiday calculations, acknowledgement rules, soft-delete/restore behavior, and null sorting.
- Do not publicly cache authenticated HTML or private JSON. Do not weaken login/password hashing for speed.
- Keep existing theme, reduced-motion/effects preferences, keyboard navigation, and normal browser link semantics.
- Validate in an isolated test/staging database; the configured development database was not loopback, so local database profiling was deliberately skipped.
- Preserve pre-existing edits to `README.md`, `docker-compose.yml`, and `docs/azure-app-service-deployment.md`.
- Correct-tenant telemetry access is required before claiming a per-route or SQL root cause.

## Review focus

1. Searches matching records beyond the first page still return those records; aggregates and exports still cover the full authorized filtered set.
2. Midnight/holiday boundaries, overdue thresholds, missing dates, and ascending/descending null ordering remain identical.
3. Engineer/requestor/PM scopes and soft-deleted records never leak through pagination, polling, or caches.
4. Live refresh does not overwrite an in-progress edit, stale search response, focus, or horizontal scroll position.
5. JavaScript-disabled, reduced-motion, back/forward, theme-switching, and browser-cache-restored pages remain usable.

---

## Investigation evidence — 5 November 2026, approximately 01:14–01:22 UTC+08

### Production identity

- Subscription: confirmed by the user, `Microsoft Azure Sponsorship (esgrequesthub)`.
- App: `esgrequesthub`, resource group `ESGRequestHub`, Southeast Asia.
- Plan: `ASP-Premium-DEV`; despite its name, the actual SKU is **Basic B2**, capacity 1.
- Database: `requesthub-postgre`, PostgreSQL 18, General Purpose `Standard_D2ads_v5`, same region; 32 GB Premium storage, provisioned IOPS 120.
- `/release/` returned **v55.4**, revision `0d4124a6e553e3efdfd63de13d18084ccdf97424`. Workspace HEAD was `5495d1c`; therefore workspace findings are not proof of identical deployed code. Compare the release revision before implementation. Existing documentation's v50.7 snapshot is not the live release.

### Measurements

Seven-day Azure metrics were collected at hourly granularity. Means below are **unweighted means of populated hourly averages**, not request-weighted latency or p95. Peaks use the metric's maximum aggregation. Brief events and endpoint-specific problems require finer-grained/request telemetry.

| Layer | Metric | Sample mean | Observed peak |
|---|---|---:|---:|
| App Service plan | CPU | 7.12% | 43% |
| App Service plan | Memory | 48.77% | 65% |
| App Service plan | HTTP queue length | 0 | 0 |
| Web app | HTTP response time | 0.287 s | 77.121 s |
| PostgreSQL | CPU | 3.15% | 26.79% |
| PostgreSQL | Memory | 17.41% | 28.87% |
| PostgreSQL | Disk IOPS consumed | 0.055% | 6% |
| PostgreSQL | Disk queue depth | 0 | 0 |
| PostgreSQL | Active connections | 10.59 | 28 |

- Initial public login probe: **41.866 s TTFB**, 41.897 s total, HTTP 200.
- Four subsequent login probes: TTFB **2.204 s, 0.168 s, 0.134 s, 0.154 s**. The last three indicate fast warm anonymous responses, not authenticated dashboard performance.
- Unauthenticated dashboard redirect: **0.118 s**, HTTP 302. This bypasses dashboard work and is not a dashboard benchmark.
- Activity log recorded successful restart actions at **17:13:49, 16:32:21, and 14:50:49 UTC on 4 November**, plus earlier restarts. The first slow probe occurred near a restart. Strong evidence of startup/restart-associated delay, but startup logs are needed to attribute the 41.9 seconds to a specific startup step. This is not proof that idle unload caused that sample.
- Always On: **false**. Health check path: **unset**. HTTP/2 setting: **false**; measure protocol behavior before treating it as a bottleneck.
- Runtime migrations: **disabled** (`RUN_MIGRATIONS=false`). Runtime collectstatic and changelog flags were absent; workspace entrypoint defaults both to enabled, with changelog synchronization allowed up to 60 seconds. Confirm deployed image behavior before changing these.
- Production `/healthz/` returned 200 `ok`; `/readyz/` returned 200 `{"ready": true}`.
- PostgreSQL Resource Health: **Available**. App Service Resource Health: **Unknown**, not proof of an outage or healthy application.
- Actual production hashed stylesheet references already return **gzip** and **`public, immutable`** caching. Compressed app CSS was approximately 40 KB; shared glass CSS approximately 9 KB. The unversioned asset's 60-second cache is not the page's actual hashed-asset policy.

### Code findings and existing good behavior

| Finding | Evidence | Interpretation |
|---|---|---|
| Dashboard caps rendered rows only after materializing the full filtered queryset | `hub/views.py`, `DashboardView._build_admin_dashboard_context`; `hub/forms.py`, `AdminRequestFilterForm.filter_sequence` | Work/memory scales with total matching records even with a 100-row display cap. Actual contribution to production latency is unmeasured. |
| Dashboard filtering, search, sorting and metric buckets traverse Python lists | `hub/views.py`, admin dashboard helpers | Candidate for SQL filtering/aggregation; retain business-calendar and search semantics. |
| SQR builds a full submission list and PM report summaries before selecting rendered content | `hub/views.py`, `SqrListView.get_context_data`, especially `all_submissions = list(...)` | Candidate for bounded tracker reads and on-demand report work. |
| Shared base loads dashboard-specific styling globally | `templates/base.html`, stylesheet declarations | Workspace `app.css` is ~242 KB, dashboard CSS ~165 KB, glass CSS ~47 KB, theme CSS ~32 KB uncompressed; bytes are not equivalent to transfer cost because production compression/caching work. CSS parsing still merits measurement. |
| Navigation effects can extend perceived settling time | `static/css/app.css`, `request-hub-page` transitions at 360 ms; `static/js/liquid-glass.js`, staggered entrances | Motion does not hold the network request, but can delay perceived completion. Measure before shortening. |
| Navigation badges repeat database work | `hub/context_processors.py`; dashboard also counts unread new-ticket notifications | Avoid duplicate request-local badge counts; no shared private cache needed. |
| Live polling already avoids unnecessary rebuilding | `DashboardLiveDataView` version check; dashboard poll every 15 s and skips hidden tabs | Preserve these optimizations. Changed versions still rebuild dashboard data and partials. |
| Reports already use SQL aggregation | `RequestReportView` operational `.values().annotate()` queries | Do not indiscriminately rewrite working report aggregation. Profile repeated chart queries and activity-report paths first. |

### Limits and failed diagnostic paths

- AppLens failed with `InvalidAuthenticationTokenTenant`; retry with the expected tenant reported `No accessible tenants found`.
- App Insights route query failed with `InvalidTokenError: The provided authentication is not valid for this resource`. Correct-tenant token acquisition required interactive authentication and was stopped; no account logout/login or permissions were changed.
- SCM deployment history returned **403 Forbidden**. The bundled script initially used an unsupported CLI deployment command; the corrected command also encountered SCM access restrictions.
- ARM resource metadata, activity logs, and Azure Monitor metrics succeeded with the explicitly selected subscription. These are the evidence used here.
- Authenticated production route timings, SQL timings/plans, actual row counts, browser LCP/INP, and long-task traces remain **unmeasured**. The shared local browser showed a dashboard but did not provide network/performance traces.
- No production load test, database mutation, dependency installation, restart, or configuration change was performed.

**Assessment:** Startup/restart-associated waiting is the strongest measured issue. Always On is a clear idle-unload risk. Workspace dataset handling and visual settling are credible code-level improvement candidates. Current metrics do **not** justify scaling App Service/PostgreSQL or increasing database IOPS; individual slow queries remain possible despite low utilization.

---

## Success targets and measurement protocol

Targets are proposed acceptance budgets, not current results or guaranteed improvements:

- Warm authenticated route p95 TTFB **<800 ms**; collect at least 30 comparable navigations per representative role/route, separate from cold startup.
- LCP **<2.5 s**, INP **<200 ms**, CLS **<=0.1** at the 75th percentile where field data is available; controlled lab checks supplement, not replace, field measurements.
- Main content remains immediately usable; navigation animation settles within **150–200 ms** and reduced-motion renders without delayed entrances.
- Startup: propose **<=10 s** from a controlled staging restart to readiness, excluding intentional image pull/provisioning. If startup architecture cannot meet that budget, explicitly report the bound and protect users with deployment warm-up rather than masking it.
- Bounded tracker/list queries and rendered rows; dashboard/SQR aggregates still use the full authorized filtered population.
- No permission, business-rule, export, live-update, accessibility, or theme regressions.

Record first visit, warm visit, idle return, and post-deployment separately. Cover dashboard, request detail/manage, SQR tracker/reports, operational/activity reports, and profile. Use the same account role, data snapshot, filters, device/network conditions, and release revision for before/after comparison. Inspect browser Navigation/Resource Timing, long tasks and paint, and route/database spans. Never export credentials, cookies, personal data, or SQL parameters in reports.

## Task 1 — Establish trustworthy authenticated baselines

**Files:** Create `hub/test_page_performance.py`; create `hub/services/page_performance.py` and `hub/middleware.py` only if existing telemetry cannot supply route/database breakdowns; modify `request_hub/settings.py` and dependency manifests only if instrumentation requires it. Keep diagnostics off by default.

**Interface:** Optional `PagePerformanceMiddleware(get_response)` records sanitized route name, elapsed time, query count and summed query execution duration. A diagnostic `Server-Timing` header exposes `app` and `db` timings only in an explicitly enabled staging mode. No SQL text, parameter values, identity, tokens, or cookies.

- [ ] Verify deployed revision against workspace and obtain correct-tenant access to aggregate requests/dependencies; distinguish an empty telemetry table from inaccessible telemetry.
- [ ] Add tests `test_timing_disabled_by_default`, `test_enabled_timing_excludes_private_values`, `test_exception_preserves_original_response`, and `test_no_forced_debug_query_capture` before optional middleware implementation.
- [ ] Run `python manage.py test hub.test_page_performance --settings=request_hub.settings_ci` in the isolated test environment; new middleware tests should fail before implementation.
- [ ] If telemetry is incomplete, implement staging-only timing using monotonic time and Django `connection.execute_wrapper`, not global `DEBUG` or `force_debug_cursor`; record that SQL time is included in app time, not additive to it.
- [ ] Repeat the targeted command, then record the role/route/browser baseline and select the measured slowest authenticated path for Task 4 or 5 first.
- [ ] Review and commit only that independently verified diagnostics deliverable when implementation is authorized.

## Task 2 — Stabilize startup without changing capacity

**Files:** Reuse `request_hub/urls.py` and `hub/test_release_health.py`; update `docs/azure-app-service-deployment.md` around existing edits; inspect `entrypoint.sh`, `Dockerfile`, and `scripts/deploy-production.sh` before any startup-code change.

**Interface:** Existing `/healthz/` is database-free liveness; `/readyz/` uses `SELECT 1` and returns generic 503 on database failure. Proposed App Service health path: `/readyz/`, so deployment readiness includes database connectivity.

- [ ] Extend release-health tests with `test_healthz_does_not_access_database`, GET/503 contract, and no sensitive failure body; verify tests before changes.
- [ ] Correlate restart activity with approved releases, startup logs, image pulling, static collection and changelog synchronization; do not assume restart actions are platform crashes.
- [ ] With separate operational approval, enable Always On and configure `/readyz/` as the App Service health path; retain B2 capacity and existing database SKU.
- [ ] Keep runtime migrations disabled. Move static collection into image build only when its build configuration, manifest validity and production asset delivery are proven; then explicitly disable runtime collection. Move changelog sync to a bounded release step only after duplicate-run/failure behavior is verified.
- [ ] Run `python manage.py test hub.test_release_health --settings=request_hub.settings_ci` and existing deployment-script checks; confirm health/readiness and hashed assets in staging.
- [ ] Measure controlled staging restart/readiness and warm login again; monitor production after an approved rollout. Always On prevents idle unload, not deployment restarts.
- [ ] Record previous settings and use the prior immutable image/settings for rollback; review and commit any source/documentation changes separately.

## Task 3 — Bound browser asset and visual-settling costs

**Files:** Modify `templates/base.html`, `templates/hub/dashboard.html`, `templates/hub/sqr.html`, `templates/hub/report.html`, `static/css/app.css`, `static/css/liquid-glass.css`, and `static/js/liquid-glass.js` only as their measured costs justify; create `hub/test_page_assets.py`.

**Interface:** Base retains shared shell/theme assets; `extra_head`/page script blocks provide route-specific assets. Navigation uses a 150–200 ms token and does not delay `location` changes or suppress native modifier/download links.

- [ ] Add tests `test_non_dashboard_pages_omit_dashboard_bundle`, `test_chart_bundle_once_per_required_page`, `test_shared_theme_assets_remain`, and `test_content_visible_without_scripts` before scoping assets.
- [ ] Map selectors before moving dashboard CSS: shared requestor, management or other styles must remain available on their consuming pages.
- [ ] Scope genuinely page-specific assets, defer noncritical scripts while preserving inline dependency order, and lazy-load optional reports/modal work. Preserve existing immutable WhiteNoise URLs and compression.
- [ ] Shorten cross-page motion and stagger without opacity-gating primary content. Do not remove themes or glass effects indiscriminately; retain existing no-blur scrolling optimizations.
- [ ] Run `python manage.py test hub.test_page_assets accounts.test_themes --settings=request_hub.settings_ci`; compare browser LCP, long tasks and navigation settling before/after on desktop and a constrained mobile profile.
- [ ] Check reduced-motion/effects, no JavaScript, theme switching, back/forward cache, focus, and Ctrl-click/download semantics; review and commit the verified asset/motion change.

## Task 4 — Bound dashboard reads while preserving behavior

**Files:** Modify `hub/views.py`, `hub/forms.py`, `hub/services/dashboard_queries.py` (new focused query helpers), dashboard row/list templates, and create `hub/test_dashboard_performance.py`.

**Interface:** Query helpers return authorized filtered querysets, full-population aggregate data, and a Django paginator page; the view continues to publish current metrics/links plus `page_obj`. Default page size **50**, accepted sizes **25/50/100**, deterministic primary-key tie-break, existing 100-row maximum. A page change must bypass the unchanged-data shortcut or include its query fingerprint.

- [ ] Write fixtures with at least 151 records and tests `test_search_finds_beyond_first_page`, `test_metrics_cover_full_filtered_population`, `test_null_sorting_matches_existing`, `test_role_scope_and_soft_delete`, and `test_date_holiday_boundaries_match_existing`.
- [ ] Add `test_query_count_does_not_grow_per_row`, `test_page_fetch_is_bounded`, and `test_live_page_change_not_suppressed_by_version`; fail them against the current list-materialization behavior.
- [ ] Move equivalent filters/search/sort into SQL, aggregate metrics separately, and fetch only the selected page with existing related joins. Preserve full-request multi-term search, its metric-filter exception, and business-calendar calculations; do not replace working-day logic with naive date subtraction.
- [ ] Publish pagination controls with filters/sort retained, and keep live response contracts and search ownership stable. Do not cache full private dashboard HTML.
- [ ] Run `python manage.py test hub.test_dashboard_performance --settings=request_hub.settings_ci`, then relevant existing dashboard/lifecycle/redirect tests selected from `hub.tests` and `hub.test_lifecycle`.
- [ ] Compare full page and changed/unchanged live poll times on identical staging data; review and commit only once results show bounded page reads and parity.

## Task 5 — Separate SQR tracker reads from report computation

**Files:** Modify `hub/views.py`, `templates/hub/sqr.html`, SQR partials and tracker scripts; create `hub/services/sqr_queries.py` and `hub/test_sqr_page_performance.py`.

**Interface:** Tracker uses bounded authorized rows (same 25/50/100 size contract as dashboard) and full authorized aggregates. Report-only computation runs for `tab=reports`/the existing report endpoint. Detail/edit/history data is fetched by authorized record ID when needed, not embedded for every record.

- [ ] Write tests `test_tracker_does_not_compute_report_only_payload`, `test_search_and_status_filter_span_all_pages`, `test_full_scope_counts_preserved`, `test_pm_assigned_default_preserved`, `test_engineer_scope_preserved`, and `test_export_not_limited_to_current_page`.
- [ ] Add tests for inline edit/undo/history after a page change, current search becoming stale during a poll, and preserving focused inputs/horizontal scroll on refresh.
- [ ] Move tracker filters/search and pagination server-side before removing all-row DOM data; retain linked-request access, per-role controls and PM default-assigned behavior. Keep calculated monetary values and approval/delivery/report semantics unchanged.
- [ ] Evaluate moving PM report computations to the existing report endpoint rather than rebuilding the entire SQR page context; do not break snapshot/history contracts or financial totals.
- [ ] Run `python manage.py test hub.test_sqr_page_performance hub.test_sqr_performance hub.test_sqr_prototype hub.test_multiple_sqrs hub.test_sqr_inline_status hub.test_sqr_on_hold --settings=request_hub.settings_ci`.
- [ ] Measure tracker HTML bytes, nodes, request/query duration, and report load time with production-like anonymized staging data; review and commit this deliverable independently.

## Task 6 — Tune proven database hot paths and duplicate badges

**Files:** Modify `hub/context_processors.py`, relevant query helpers/views, and add `hub/test_notification_performance.py`; create migrations only for indexes justified by measured query plans.

**Interface:** Request-local notification summary reuses an existing badge count within the same HTTP request; no cross-user/global cache. Candidate helper `get_notification_badges(request) -> dict` preserves the existing context keys and toast cursor.

- [ ] Add tests `test_badge_summary_reused_within_request`, `test_unread_count_and_toast_cursor_preserved`, `test_role_restrictions_preserved`, and `test_separate_users_do_not_share_counts`.
- [ ] Capture sanitized slow SQL and inspect plans on staging; use `EXPLAIN (ANALYZE, BUFFERS)` only for approved read-only queries with statement timeouts, never uncontrolled production writes/load tests.
- [ ] Reuse duplicate badge work; inspect status/date/order/filter predicates and existing foreign-key/composite indexes before adding anything. The toast cursor already has a recipient/event/id index; do not duplicate it. `source__icontains` cannot be fixed by assuming a normal B-tree text index suffices.
- [ ] Add a narrowly justified composite/partial index only if its plan and benchmark show a benefit; account for migration locking, rollback and insert/update overhead. Use concurrent production index creation where supported and appropriate.
- [ ] Run `python manage.py test hub.test_notification_performance --settings=request_hub.settings_ci` and targeted request/report tests for each changed query; measure latency rather than optimizing query count alone.
- [ ] Leave database sizing/IOPS unchanged unless peak-period evidence demonstrates saturation; review and commit only proven changes.

## Task 7 — Review polling and report paths after primary optimizations

**Files:** Existing dashboard/SQR poll code, `static/js/notification-toasts.js`, `hub/views.py`, operational/activity report paths, and focused tests in the modules above.

- [ ] Retain the 15-second visible-tab polling cadence initially, unchanged-version fast path and in-flight guard; measure workload before considering push updates or a longer cadence.
- [ ] Add cancellation/backoff only if measurement identifies overlapping/stale requests; distinguish search ownership and version fingerprints by filters/page/role.
- [ ] Add tests for `test_unchanged_poll_skips_html_render`, `test_changed_poll_preserves_edit_state`, and full-scope aggregate parity under simultaneous changes.
- [ ] For reports, optimize only measured duplicate/slow aggregations and load nonvisible charts on demand. Operational reports already use SQL aggregation; profile activity-report date filtering and chart payloads independently.
- [ ] Re-run owning targeted tests, verify report exports/full chart totals and browser edit/focus behavior, then review/commit.

## Task 8 — Acceptance and guarded rollout

**Files:** Update this plan's results section and directly related deployment documentation after implementation; no new tooling unless an existing validation path is insufficient.

- [ ] Run targeted suites for each deliverable first; run the broader existing suite when shared query/permission behavior changes warrant it.
- [ ] Repeat the same authenticated role/route and browser matrix; publish p50/p95 TTFB, app/SQL durations, rows fetched, HTML/asset bytes, LCP/INP/CLS, and startup-to-readiness.
- [ ] Deploy only with separate approval using the existing immutable-image release process; verify `/release/`, `/healthz/`, `/readyz/`, hashed assets and migration state.
- [ ] Monitor peak usage and idle return for at least 24–48 hours. Distinguish release restarts from errors; alert on sustained latency/5xx rather than anonymous login averages alone.
- [ ] Roll back the specific image/settings on regression. Retain permission/search/financial-total correctness as release blockers even if speed improves.

### Conditional next decisions

- Consider short-lived scoped aggregate caching only if repeated aggregate queries remain expensive, with mutation invalidation and role/filter/date/version keys. Do not cache private rendered pages publicly.
- Consider partial-page navigation only if server and assets are fast yet repeated full document rendering remains dominant; require a separate design for lifecycle cleanup, history, stale state, focus and authorization.
- Consider scaling only when minute-level peak CPU/memory/queue/IOPS/connection metrics correlate with slow routes. Current evidence supports optimization first, not an infrastructure upgrade.

## References

- [Configure an App Service app — Always On](https://learn.microsoft.com/azure/app-service/configure-common): idle unload after 20 minutes when disabled; root keep-alive requests every five minutes when enabled.
- [App Service health checks](https://learn.microsoft.com/azure/app-service/monitor-instances-health-check).
- [Azure Monitor OpenTelemetry](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-enable?tabs=python): verify the supported instrumentation method for the actual custom-container deployment; the presence of an App Insights setting alone does not prove route/database telemetry is flowing.

## Execution status — source implementation results

Source implementation is complete in the current checkout, without committing, pushing or deploying. Operational checklist items requiring production access/approval remain pending.

| Task | Verified source deliverable |
|---|---|
| 1 | Opt-in development/staging request and SQL timings, sanitized console logging and `Server-Timing`; production diagnostics remain disabled by default. |
| 2 | Docker build precollects hashed/compressed static assets; runtime collection defaults to false. Existing liveness/readiness contracts remain intact. |
| 3 | Primary content is visible without application JavaScript; entrance and page motion use 180 ms, with entrance staggering capped at 20 ms. Shared CSS remains loaded because it contains global navbar/chrome styles; removing it would risk the agreed design. |
| 4 | Admin/PM dashboard uses SQL filtering, aggregates, sorting and bounded page reads. All dashboard roles have 25/50/100-row pagination; other roles retain their existing full-population report logic. Unicode casefold searches use narrow text/ID projection where PostgreSQL 16 cannot reproduce Python normalization. |
| 5 | SQR tracker fetches bounded rows, computes full-scope counts and avoids report-only work. Classic filters/search span the full authorized dataset. Prototype paging preserves pipeline counts, edit state and load-more behavior. Legacy no-page API requests remain compatible. |
| 6 | Notification counts/cursors are reused within a request, never across users. No speculative index or capacity changes were made. |
| 7 | Dashboard versions include filter/page fingerprints; stale deferred row updates are discarded. Live search preserves sort links. SQR edits/undo refresh authoritative pipeline totals without rebuilding the editor. Filter navigation restores focus without reopening onboarding. |
| 8 | Full isolated PostgreSQL suite: **495 passed / 498 total**. Five Node refresh regressions passed. Real Edge checks passed for pagination, full-dataset searches, focus restoration, load-more draft preservation and visible primary content. Final Docker build and production-mode local readiness/immutable gzip assets passed. |

### Pagination UI follow-up

Pagination now appears below the Dashboard and SQR tables, with compact Previous/Next buttons and a page/record count. The row-size selector and Go button were removed from the UI; existing page-size URLs and server limits remain supported. Three rendered-layout/control tests and the existing live-refresh regression wrapper passed (four targeted Django tests). The full-suite results above predate this presentation-only follow-up.

### Local benchmark — not production p95

Using the same 2,000 synthetic requests and the original HEAD view versus the optimized view, with a warm-up and three measured renders:

| Metric | Original | Optimized |
|---|---:|---:|
| Median view + template render | 336.97 ms | 246.14 ms |
| Rendered rows | 100 | 50 |
| Full-population ongoing total | 2,000 | 2,000 |
| HTML bytes | 716,342 | 405,589 |
| SQL query count | 28 | 29 |

This is approximately **27% faster rendering and 43% less HTML** for the paginated local workload. It is not an identical-row-count microbenchmark or a production speed guarantee. The improvement bounds rich row hydration/rendering rather than claiming fewer SQL statements. A local production-mode container became ready in 4.82 seconds with migrations/changelog synchronization disabled for that check; this does not establish Azure restart latency.

### Existing failures and release gates

These failures were reproduced in an archived original `5495d1c` checkout, not introduced by this work:

- `hub.test_changelogs.ChangelogTests.test_management_has_changelogs_tab`
- `hub.test_changelogs.ChangelogTests.test_management_marks_automatic_entries`
- `hub.test_changelogs.ChangelogTests.test_new_manual_entry_still_requires_previous_version`

They were deliberately not repaired as unrelated existing issues. Before production rollout, align/review against the deployed revision, resolve the existing release-test failures or explicitly accept them, restore correct-tenant telemetry access, approve Always On and health-check configuration, and measure authenticated production routes/web vitals. Moving changelog synchronization out of startup remains deferred until its secure release-time database access and failure recovery are verified. No Azure settings, production data/schema or resource sizes were changed; isolated test databases were disposable.
