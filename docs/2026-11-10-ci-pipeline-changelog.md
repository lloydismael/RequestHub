# CI/CD improvements — November 10, 2026

Status: proposed pipeline changes for a reviewed PR; not a published production release. Source publication was approved after validation. No production deployment, runtime upgrade, staging resource, or merge is part of this change.

## Changes

| Area | Before | After |
| --- | --- | --- |
| Screenshot evidence | Python 3.14 tooling and historical capture ran sequentially in the release-image job. | A non-blocking trusted-master CI job uses Python 3.12 and cached capture tooling/assets alongside validation. Release imports evidence from its exact upstream run and attempt. |
| Restored evidence merge | Later curated metadata without comparisons overwrote complete restored comparisons for the same commit, forcing repeated historical capture. | Preserve provenance-complete cached comparisons when merging metadata-only entries (including empty comparison lists), while retaining curated titles/descriptions, actual-parent validation, image validation, and recipe-fingerprint invalidation. Regression coverage verifies reuse and rejection paths. |
| Evidence handoff | Release generated and packaged evidence locally. | A bounded manifest/image bundle verifies checksums and real Pillow decoding while preserving commit coverage, revision and first-parent provenance; malformed schema or missing/rejected evidence retains changes as actionable drafts. No new production importer is introduced. |
| Dependencies | Only direct runtime dependencies were pinned; audit tooling upgraded on each run. | Generated, complete hash-verified locks cover runtime, test, capture, and pinned tooling dependencies, with a minimal release-side image decoder lock and a Windows-only supplemental dependency lock. Existing direct runtime versions are preserved; reconcile the pre-existing Azure Identity/OpenAI input floors with their locked 1.25/2.29 runtime versions, as approved. Bootstrap tooling is not upgraded unpredictably during the runtime build. |
| Container testing | Host Django tests, Docker build, SBOM, and vulnerability scans. | Both image jobs additionally check actual container startup, production configuration, disposable-database migrations, revision, readiness, login, and packaged static assets. |
| Workflow validation | Offline mocked workflow/deployment regressions. | Add digest-pinned Actionlint/ShellCheck plus evidence-import and container-probe regressions; run real historical Chromium capture tests in their capture environment. |
| Triggers | Ready-for-review repeated validation already run for draft PRs; disabled/ineligible CD allocated a preflight runner. | Remove the redundant ready-for-review trigger; reject disabled/ineligible upstream CD at job level. Preserve edited events for base retargeting and fail-safe dev/PR routing. |
| Documentation | Some instructions described parallel tests, runtime static collection, and optional manifest generation. | Correct these contracts, document lock regeneration, smoke resource ownership, exact-run evidence validation, and timing limitations. |
| GitHub policy | Master had no required checks/reviews; production had no branch policy in the reviewed metadata. | Separately approved live settings require the three existing CI checks and one PR approval, with production restricted to master. These settings were applied and read back independently of the YAML, including strict up-to-date checks bound to the GitHub Actions app and dismissal of stale approvals. |
| Smoke review fixes | Smoke cleanup left anonymous database volumes and readiness could match the temporary initialization socket. | Remove owned PostgreSQL containers with their disposable volumes and probe readiness over TCP at `127.0.0.1`. Regression coverage exercises successful and failed smoke cleanup. |

## Preserved safeguards

- Exact release image is still scanned before publishing; no rebuild occurs after scanning.
- Production OIDC, controlled migrations, temporary single-IP firewall ownership/cleanup, current-master checks, numbered-tag/digest verification, finite readiness checks, and image rollback remain.
- Active production deployments are not cancelled. Image rollback never reverses database migrations.
- Application tests stay serial pending reproduction and repair of earlier parallel failures.
- Every substantive commit remains in the cumulative changelog; evidence is not waived with skip directives.

## Evidence and Notifications

No production version has been allocated to these proposed changes. The implementation evidence is packaged in `hub/changelog_assets/pipeline-2026-11-10/`: genuine repository reports compare first parent `05624c0d3daa50a8b57f296f5ec1b7b9cf1bc253` with implementation commit `f81bf9496d87996710e9221cb7605a38b6f3051f`. They are file-metadata reports, not UI screenshots or proof of live deployment. Once reviewed commits reach master, CI captures remaining maintenance/evidence commits and the release packages validated pairs for the existing startup synchronization.

Validate importer rejection/safe fallback, existing packaged evidence enrichment, and Notifications → Changelogs visibility using synthetic fixtures and an isolated PostgreSQL database. Complete published entries appear to signed-in readers; incomplete evidence stays draft and is not exposed as a published notification. Record actual before/after captures and the deployed version only after an authorized release.

## Timing interpretation

The recent eight-deployment baseline had median screenshot capture of 134 seconds and tooling installation of about 44 seconds. Moving those operations alongside CI removes sequential release-image work, but `workflow_run` still waits for the capture job. Container smoke checks add intentional validation time. Measure actual end-to-end CI-to-production latency after rollout; local tests do not establish live speed savings.

Staging/slot upgrades, a fully asynchronous protected production evidence importer, scheduled deployed-image scanning, alerting integrations, and parallel-test isolation repairs remain separate follow-ups.

## Verification

- Full application suite: 782 tests passed against disposable PostgreSQL using a clean, complete Python 3.12 environment, collected static files, and UTF-8 Node subprocess input. This includes evidence enrichment, drafts/publication, and Notifications visibility.
- Exact-run artifact importer: 13 regressions passed, including corrupt-but-checksummed images, malformed manifest fields, wrong run/attempt/revision, and missing-artifact draft fallback.
- Container probe/cleanup: 6 regressions passed, including partial-start cleanup; the actual candidate image passed configuration, migrations, startup, revision, readiness, login, and static probes. Only owned test containers/networks were removed.
- Historical Chromium captures: 25 tests completed on Linux Python 3.12, with one existing platform skip; final dependency audit reported no known vulnerabilities.
- Offline workflow/fast-gate contracts: 5 tests passed; digest-pinned Actionlint and ShellCheck passed.
- A fresh Windows hash-mode install reproduced the missing-platform-dependency failure, then passed with the supplemental lock and `pip check`. Existing direct runtime pins remain unchanged.
- Independent review findings were reproduced and corrected with regression tests. GitHub policy readback confirmed strict required checks, one approval, administrator enforcement, and the sole production branch policy `master`.
- The actual packaged implementation reports were decoded and verified with distinct before/after checksums. An additional isolated-database check confirmed that their release draft is absent from Notifications until importing complete evidence, then publishes once and becomes visible to a signed-in reader.
- Combined review follow-up: 26 historical-capture tests completed with one existing platform skip, including restored-cache merging, curated metadata/evidence precedence, and rejection of missing images, stale Git provenance, and changed recipe fingerprints. Seven smoke regressions, shell syntax and ShellCheck passed. All 94 targeted changelog tests passed against an isolated PostgreSQL cluster after collecting static assets, including evidence publication and Notifications visibility.
- Cache-merge review fix: reproduced missing/empty comparisons before the fix; afterward, 17 manifest tests, 26 historical-capture tests (one existing skip), four workflow-contract tests, and 12 isolated-PostgreSQL evidence/publication/Notifications tests passed. Real before/after captures survived metadata-only manifest rebuilding without recapture; changing the recipe still invalidated them. Static assets were collected before the Notifications checks. This is local regression evidence, not a deployed release verification.

These checks do not publish the pending commits, deploy production, prove live CI latency, or fabricate release-specific screenshot provenance.
