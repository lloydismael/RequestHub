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

## Follow-up: Azure connection and disk readings

**Before:** Azure metrics supported only managed identity. An unconfigured source showed a list of empty readings, and the storage card combined free space, percent used and their timestamps in one line.

**After:** [Azure setup](azure-app-service-deployment.md#optional-azure-metrics-for-management--monitoring) supports explicit Azure CLI sign-in on the machine running Django and defaults to system-assigned managed identity on App Service. Local CLI mode is rejected on App Service; neither mode falls back to other credentials. The Azure sample cache is isolated by resource ID and auth mode. Missing configuration, unavailable sign-in and denied access are shown separately for each source; Django and direct PostgreSQL diagnostics continue independently.

App Service and PostgreSQL Flexible Server now have status and connection instructions next to the CPU and storage cards. Used bytes, free bytes and percent used have their own sample times, and zero remains a real reading. Logical database size is always identified as a direct SQL reading, not server disk. The environment example and setup guide cover both environments. Regression tests cover credential selection, authorization failures, independent source state, timestamps, and zero or delayed values. The release capture recipe supports a first-parent revision with or without an existing Monitoring tab.

| Offline Chromium fixture | Before | After |
| --- | --- | --- |
| Both Azure sources reporting | [Before](../hub/changelog_assets/pending-management-monitoring-azure/connected-before.png) | [After](../hub/changelog_assets/pending-management-monitoring-azure/connected-after.png) |
| Neither Azure source configured | [Before](../hub/changelog_assets/pending-management-monitoring-azure/unconfigured-before.png) | [After](../hub/changelog_assets/pending-management-monitoring-azure/unconfigured-after.png) |

The [capture details and SHA-256 checksums](../hub/changelog_assets/pending-management-monitoring-azure/capture.json) record the before revision and fingerprints of the unreleased working-tree inputs. Both sides use the same synthetic administrator and metric fixtures. These are not live Azure measurements or exact-release captures. In disposable PostgreSQL, **Notifications → Changelogs** hid both drafts and denied access to the four PNGs; after test publication, it showed both comparisons and served the original PNG bytes with `no-store`.

## Follow-up: Azure read compatibility

**Before:** Local Azure CLI authentication used the CLI's default subscription, which could select the wrong tenant. Even with a valid token, Azure Monitor rejected the offset-formatted time window, leaving both Azure panels without readings.

**After:** Local token requests use each monitored resource's subscription without switching the CLI default. Both auth modes send the existing 15-minute window as UTC timestamps ending in `Z`. Managed identity remains the App Service default. Regression tests cover two resource subscriptions and the UTC request format. A live localhost configuration check returned timestamped App Service performance, PostgreSQL CPU, and disk used/free/percent readings alongside direct SQL diagnostics.

| Offline Chromium fixture | Before | After |
| --- | --- | --- |
| Azure CLI default tenant denied | [Before](../hub/changelog_assets/pending-management-monitoring-azure-reader/cli-subscription-before.png) | [After](../hub/changelog_assets/pending-management-monitoring-azure-reader/cli-subscription-after.png) |
| Azure Monitor rejects the time window | [Before](../hub/changelog_assets/pending-management-monitoring-azure-reader/utc-timespan-before.png) | [After](../hub/changelog_assets/pending-management-monitoring-azure-reader/utc-timespan-after.png) |

The [capture manifest](../hub/changelog_assets/pending-management-monitoring-azure-reader/capture.json) records the reader and image hashes and synthetic Azure response codes. Both sides render the same Monitoring UI with the saved pre-fix or fixed reader. No real Azure data or exact release revisions were captured. In disposable PostgreSQL, **Notifications → Changelogs** hid the draft and all four PNGs, then showed both comparisons and served byte-identical PNGs with `no-store` after test publication.

No production release was published. Use the [release capture workflow](cd-workflow-testing.md#preparing-screenshot-evidence) for exact-revision evidence, and verify real Azure samples against the Azure portal after configuring the monitored resources.
