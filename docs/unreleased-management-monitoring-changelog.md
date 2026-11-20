# Unreleased: Management Monitoring

**Before:** Management ended at Changelogs. Checking web app and database health required separate tools.

**After:** Request Hub Admins can open **Management → Monitoring** for two independently refreshed sources:

- **Live diagnostics (every five seconds while visible):** The responding Django worker reports completed requests, average latency, estimated p95 and 5xx responses from the last minute. p95 above 10 seconds is shown as a lower bound. The dashboard also shows browser round-trip time and PostgreSQL reachability, query latency, logical database size, connections, transaction rate, lock waits and buffer-cache hit rate. Health checks and Monitoring polls are excluded from worker statistics. Failed refreshes keep the original timestamps of any live readings.
- **Azure Monitor (one-minute samples, which may arrive late):** PostgreSQL server CPU, memory, used/free disk, I/O, connections and backup storage, plus App Service request volume, response time, 5xx responses, memory and CPU time across instances. Azure's site-wide HTTP metrics include Monitoring traffic. Each reading is timestamped. Transient failures can keep recent samples, including zero, marked delayed; denied or disabled sources are cleared. The optional [Azure setup](azure-app-service-deployment.md#optional-azure-metrics-for-management--monitoring) uses scoped Monitoring Reader access for the App Service's system-assigned identity. No database migration or additional PostgreSQL grant is required.

The dashboard includes pause, manual refresh, responsive charts and keyboard-accessible sample tables. Its assets load only on the Monitoring pane. Both diagnostic endpoints are Admin-only and non-cacheable. Read-only PostgreSQL probes use a server-side statement timeout; connections use libpq timeouts. Azure samples have an independent cache and each worker allows only one Azure refresh at a time.

## Evidence and verification

[Before: Management without Monitoring](../hub/changelog_assets/pending-management-monitoring/management-monitoring-before.png) · [After: Monitoring dashboard](../hub/changelog_assets/pending-management-monitoring/management-monitoring-after.png) · [Capture details and SHA-256 checksums](../hub/changelog_assets/pending-management-monitoring/capture.json)

These offline Chromium images use the actual templates, the same synthetic administrator and the same viewport. The before image uses the saved pre-feature Management template; the after image uses the working tree. Page scripts were disabled: the images show the dashboard before readings load, not live measurements or exact release revisions.

Regression coverage exercises access, PostgreSQL queries, Azure resource ID length limits and sample parsing, zero and stale values, polling, session expiry and the mobile layout. CI runs the Monitoring JavaScript checks. The historical capture recipe adds a real application shell and a companion report of safe file fingerprints derived from the change's first-parent diff. In disposable PostgreSQL, a reader could not see the draft or open either PNG. After test publication, **Notifications → Changelogs** showed the entry and served both original PNGs with `no-store`.

No production release was published. Use the [release capture workflow](cd-workflow-testing.md#preparing-screenshot-evidence) for exact-revision evidence, and verify real Azure samples against the Azure portal after configuring the monitored resources.
