# GitHub Actions Speed and Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce redundant CI work and avoidable release latency without weakening validation or deployment safeguards.

**Architecture:** Retain parallel Django validation, dependency auditing, and image scanning. Preserve the trusted master-push-to-production release chain and separate release-image scan. Improve feedback, suppress duplicate development-branch work, and optimize measured orchestration overhead before considering infrastructure changes.

**Tech Stack:** GitHub Actions, GitHub CLI, Python 3.12/Django, PostgreSQL 16, Docker Buildx, Trivy, Bash, Azure App Service, GitHub OIDC.

**Spec:** The user selected the **balanced** approach on November 5, 2026. The design and acceptance criteria below define that scope. This document is a proposed implementation plan, not authorization to deploy or change repository settings.

## Design and scope

### Approaches considered

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Diagnostics and reliability only | Lowest risk; leaves duplicate work and orchestration overhead | Included as the first phase |
| Balanced optimization | Removes unnecessary development work and redundant reads; retains release trust boundaries | Selected by user |
| Build once and promote an immutable artifact | Larger redesign of release metadata, provenance, registry access, and artifact trust | Deferred |

The CI and release images are **not identical** today: the release sets a numbered version and generates `hub/release_changelog_manifest.json` before building. Consequently, promoting the current CI image directly, or removing the release scan because CI passed, would change release behavior. Keep both builds and scans in this plan.

### Goals

- Separate execution time, runner/job waiting, rework caused by failures, and production startup time.
- Avoid two full Container Security runs for each update to a development branch with an open master PR.
- Keep standalone dev pushes validated, and keep every master push fully validated before release.
- Make failures actionable without generating misleading cascades or retrying genuine assertion failures.
- Reduce redundant release orchestration and read-only Azure API calls.

### Non-goals

- No removal of required checks, security thresholds, SBOMs, PostgreSQL coverage, or production health checks.
- No paid runners, self-hosted machines, new registry, or Azure infrastructure changes in the initial implementation.
- No automatic production release for dev pushes or PR runs.
- No blanket documentation path exclusions or selective Django test skipping.
- No repository settings changes, production execution, commits, or pushes merely to publish this plan.

## Evidence collected

Inspected local workflow/helper definitions and live GitHub run/job/step metadata on **November 5, 2026, approximately 05:16–05:20 UTC+8**. GitHub timestamps are UTC. Total durations below are `updated_at - created_at`, not billing totals or isolated step durations.

| Evidence | Measurement | Interpretation |
| --- | --- | --- |
| [Latest dev CI, run 41914627177](https://github.com/lloydismael/request-hub/actions/runs/41914627177), SHA `409567a2` | Passed in **73 seconds**; Django job 63s; image job 69s | Current CI is not uniformly slow |
| [Master CI, run 41911707510](https://github.com/lloydismael/request-hub/actions/runs/41911707510), SHA `90a7061e` | About **81s** total; tests 28s; PostgreSQL initialization 22s; image build 18s; scan 19s | Tests and scans already run efficiently and concurrently |
| [Release 41911915339](https://github.com/lloydismael/request-hub/actions/runs/41911915339), same SHA | About **3.4 minutes** after CI; about **4.7 minutes** from CI creation to release completion | Main latency is downstream of CI |
| Same release, controlled deployment | Azure checks/pulls 13s; migrations skipped; update/restart 5s; production verification **83s** | Startup/readiness dominates the non-migration deployment |
| [Release 41903432756](https://github.com/lloydismael/request-hub/actions/runs/41903432756), SHA `fe09355c` | About **4.9 minutes** after CI; about **6.3 minutes** end-to-end | Migration releases need a separate baseline |
| Same release, controlled deployment | Firewall creation **65s**; full migration window 81s; production verification **85s** | Firewall provisioning, not migration execution, dominated the database window; check/migrate took 9s |
| [Failed dev CI 41912431925](https://github.com/lloydismael/request-hub/actions/runs/41912431925), SHA `d573b0be` | Failed in about **72s**, tests failed after 25s | A correctness blocker rather than slow test execution |
| [Failed run 41909403539](https://github.com/lloydismael/request-hub/actions/runs/41909403539) | **14m45s** total; jobs API and check-suite API returned zero jobs/checks; logs unavailable | Cause unresolved; do not label it a test timeout or approval wait without evidence |
| SHA `f688087a` | Dev push run 41902763849 and PR run 41902766641 both succeeded | Confirmed duplicate full CI on branch/PR updates |

### Confirmed recent blocker

`hub/tests.py:1297`, `NotificationToastTests.test_non_dashboard_page_initializes_global_live_toast_polling`, required:

```python
r'notification-toasts(?:\.[a-f0-9]+)?\.js\?v=3'
```

The asset version changed to `v=4`, producing `AssertionError: Regex didn't match`. Remote commit [409567a2](https://github.com/lloydismael/request-hub/commit/409567a2b715e990d726ee05aa4c6e34a2c8f3c2) already removes that hard-coded version, and its CI passed. Do not reimplement this fix. The inspected local HEAD was `d573b0be`; reconcile the remote fix through the normal reviewed Git process before implementing.

### Existing optimizations to preserve

- `request_hub/settings_ci.py` supplies a fast test-only password hasher.
- Tests use `--parallel auto`, `--durations 10`, and `tblib` for parallel tracebacks.
- pip downloads and Docker dependency layers are cached.
- Docker build metadata is declared late in the Dockerfile.
- `APK_REFRESH` forces fresh OS package processing for each build.
- Production pulls the new image concurrently with Azure checks.
- Migration windows are skipped only when known deployed ancestry and the database-affecting diff permit it.
- Firewall deletion overlaps startup but must complete successfully.
- Release builds read the shared Buildx cache without writing it.
- Trivy action already provides caching behavior; verify restore/download logs before adding another cache layer.

### Repository-level observations

The master protection response had `enforce_admins.enabled=true` but `required_status_checks=null` and `required_pull_request_reviews=null`. The rulesets listing returned no entries. The production environment returned no protection rules or deployment branch policy. These are observations of accessible APIs, not evidence that approvals are delaying current runs. Separately ask the repository owner to reconcile the documented protection requirements; do not remove protections for speed.

CodeQL, GitHub Advanced Security, and Copilot automation are dynamically managed workflows, not YAML files alongside the two application workflows. Their runtimes are not simply added to the Container Security critical path. Review actual merge requirements and runner contention before considering adjustments; never disable them just because their runs appear in the Actions list.

## Global constraints

- Keep Python 3.12, PostgreSQL 16, and `ubuntu-24.04` unless a separately measured compatibility change is approved.
- Keep the three CI check display names stable.
- Keep Trivy `HIGH,CRITICAL`, `ignore-unfixed: true`, and `exit-code: '1'` unchanged.
- Keep production checks on real settings; never deploy `settings_ci`.
- Keep fresh OS package processing, release SBOMs, and scans of the actual numbered release image.
- Keep master SHA freshness checks, tag/digest verification, OIDC, TLS, rollback, backup metadata checks, and owned-firewall cleanup.
- Keep production concurrency serialized with `cancel-in-progress: false`; never cancel an active migration for speed.
- Do not log migration settings, database credentials, app-setting values, or tokens.
- Restore/install dependencies only after manifest changes or a missing-dependency validation failure.
- Use Windows paths for local commands. Bash paths below are for Git Bash or Linux Actions runners.

## Review focus

1. PR lookup fails or is denied: standalone dev validation must still run, not silently skip.
2. Fork PR, draft PR, reopened PR, or edited base branch: heavy PR validation must remain available and unprivileged.
3. Failed/cancelled/missing-job run: reports must show incomplete evidence instead of fabricating execution durations.
4. Missing Azure fields or unknown deployed revision: optimized reads must fail closed or select the full migration window.
5. Stale revision, tag mismatch, migration failure, or failed cleanup: production must retain its current fail/rollback semantics.

## File responsibilities

| File | Responsibility |
| --- | --- |
| `.github/workflows/container-security.yml` | Event classification, stable CI gates, short regression checks |
| `.github/workflows/production-release.yml` | Eligibility/configuration orchestration and final-image publishing |
| `scripts/analyze_actions_runs.py` (new) | Standard-library analysis of exported run/job metadata |
| `scripts/test_analyze_actions_runs.py` (new) | Offline fixtures for timing and failure classification |
| `scripts/test-ci-routing.sh` (new) | Mocked GitHub API event-routing cases |
| `scripts/test-release-preflight.sh` (new) | Offline execution of preflight run blocks with synthetic environment inputs |
| `scripts/deploy-production.sh` | Read-only Azure state checks and safe controlled deployment |
| `scripts/migrate-production.sh` | Read-only backup checks and the owned migration firewall window |
| `scripts/test-deploy-production.sh` | Existing mocked deployment and rollback contract tests |
| `scripts_check/test_notification_toasts.cjs` | Existing browser-free toast behavior regressions |
| `hub/tests.py` | Existing Django asset-contract regression, already fixed remotely |
| `docs/ci-workflow-testing.md`, `docs/cd-workflow-testing.md` | Current behavior, measurements, troubleshooting, rollback instructions |

## Implementation tasks

### Task 1: Establish trustworthy baseline and diagnose missing-job runs

**Files:** Create `scripts/analyze_actions_runs.py` and `scripts/test_analyze_actions_runs.py`; update the measurement instructions in `docs/ci-workflow-testing.md` and `docs/cd-workflow-testing.md`.

**Interfaces:** `summarize_runs(runs: list[dict], jobs_by_run: dict[str, list[dict]]) -> dict`. Input is metadata exported with `gh`; output groups by workflow, branch/event, SHA, conclusion, and migration/non-migration category when annotated. Report total elapsed time, time to first job, individual job durations, and summed job execution separately. The summary tool does not fetch logs, access secrets, or mutate GitHub.

- [ ] Write offline tests named `test_parallel_jobs_are_not_added_to_elapsed_time`, `test_duplicate_push_and_pr_are_grouped_by_sha`, `test_missing_jobs_remain_unknown`, and `test_failed_and_cancelled_runs_are_not_success_samples`. Assert that a no-job 885-second run retains total elapsed time but has unknown execution/wait attribution.
- [ ] Run `python -m unittest discover -s scripts -p test_analyze_actions_runs.py`; confirm the new tests fail before implementation.
- [ ] Implement the analyzer using only the Python standard library. Keep raw metadata in the session artifacts directory, not the source tree; exclude sensitive logs.
- [ ] Run the same unit-test command; require all tests to pass.
- [ ] Export 20 completed runs each for CI and release; retrieve jobs for the selected runs. Separate standalone dev pushes, PRs, master CI, migration releases, and non-migration releases. Report sample counts alongside medians/p95; do not infer a meaningful p95 from two releases.
- [ ] For run 41909403539, inspect its PR Checks/timeline, workflow validation annotations, Actions billing/runner availability, and any workflow approval banner through accessible GitHub UI/API. If still unavailable, record **unresolved missing-job failure** and watch the next occurrence; do not claim a root cause.
- [ ] Save the reproducible metadata commands and baseline in the two testing guides; review and commit only this task's changes through the normal Git process.

**Deliverable:** A baseline that separates runtime from blocked/no-job runs and failed-feedback cycles.

### Task 2: Preserve the repaired regression and add cheap pre-deploy checks

**Files:** Existing `hub/tests.py`, `scripts_check/test_notification_toasts.cjs`, `scripts/test-deploy-production.sh`; modify `.github/workflows/container-security.yml` and the testing guides.

**Interfaces:** Keep `NotificationToastTests.test_non_dashboard_page_initializes_global_live_toast_polling` as the Django integration contract. Use the existing Node and Bash test executables, without npm dependencies. These checks remain inside an existing CI job rather than introducing a separately required check name.

- [ ] Reconcile remote commit `409567a2` with the working branch; verify the updated assertion still requires the toast asset and a version parameter rather than accepting missing script initialization.
- [ ] Run `python manage.py test hub.tests.NotificationToastTests --settings=request_hub.settings_ci --noinput` against disposable PostgreSQL only. Do not connect local validation to production.
- [ ] Run `node --test scripts_check\test_notification_toasts.cjs` on Windows and `bash scripts/test-deploy-production.sh` in Git Bash/Linux. If Bash is unavailable locally, execute the mocked test on a non-production Actions run; never substitute real deployment.
- [ ] Add those two inexpensive commands before the full application-test step in the existing Django job. Use an available supported Node runtime; only add `actions/setup-node` if runner verification shows it is needed.
- [ ] Confirm all existing deployment mock scenarios pass, including stale SHA, tag mismatch, unknown ancestry, backup failure, migration failure, cleanup failure, verification failure, and unhealthy rollback.
- [ ] Validate the workflow on dev; check that genuine assertion failures remain red and the three CI check names are unchanged. Do not add retries to test failures.
- [ ] Document and commit the regression gate changes independently.

**Deliverable:** Faster, clearer detection of front-end contract and deployment-script regressions, without weakening the full suite.

### Task 3: Suppress duplicate full dev CI while retaining PR merge validation

**Files:** Modify `.github/workflows/container-security.yml`; create `scripts/test-ci-routing.sh`; update `docs/ci-workflow-testing.md`.

**Interfaces:** Add an unprivileged `classify-event` job with output `run_full_ci` equal to the literal string `true` or `false`. Existing heavy jobs depend on that output. Default is `true`; only a dev push with a confirmed open PR from the same repository/branch to master returns `false`. PR merge-ref validation always runs; master pushes and manual runs always run full CI.

- [ ] Write mocked routing tests for master push, standalone dev push, dev push with open/draft master PR, fork PR, reopened PR, changed PR base, manual execution, empty lookup, malformed response, and API failure. Require the last two to return `true` with a diagnostic notice.
- [ ] Run `bash scripts/test-ci-routing.sh`; confirm failures before implementing the classifier.
- [ ] Implement lookup with `gh api` and minimally scoped `pull-requests: read` permission on the classifier only. Match the exact head owner/repository, head branch, open state, and master base. Preserve `pull_request` rather than using privileged `pull_request_target`.
- [ ] Make Django, dependency audit, and image scan depend on `classify-event` and run when its output is `true`. Keep CI's current per-ref cancellation so branch runs cannot cancel required PR merge runs. Handle an unexpected classifier failure by allowing full validation rather than creating a successful empty gate.
- [ ] Run the mocked routing tests, then exercise standalone dev push, open-PR dev update, manual execution, and PR merge-ref runs through reviewed non-production changes.
- [ ] Verify the intended result: one full run for an update with an open PR; branch-only validation remains available when no PR exists; master never skips validation; a skipped dev run cannot trigger production because CD still requires master push provenance.
- [ ] Document that a lightweight dev routing run can succeed while PR checks are still pending. Check branch-protection consumers before rollout; roll back this task if they incorrectly use that routing-only result as the validation signal.
- [ ] Commit the routing change separately.

**Deliverable:** Approximately half the heavy Container Security job executions per open-PR update. This primarily saves runner work and reduces contention; it does not halve a single run's wall-clock time.

### Task 4: Remove redundant release orchestration, not final-image verification

**Files:** Modify `.github/workflows/production-release.yml`; create `scripts/test-release-preflight.sh`; update `docs/cd-workflow-testing.md`.

**Interfaces:** Combine eligibility explanation and configuration validation in one `release-preflight` job on the `production` environment. Keep output `ready` as `true`/`false`. Run configuration validation only for an eligible trusted successful master push, and let `release-image` depend on this one job. Preserve the existing secret-free eligibility summary and configuration-name-only errors. The offline test harness extracts the literal `run: |` blocks for the existing eligibility/configuration step names, removes their common YAML indentation, and executes them with synthetic values and temporary `GITHUB_OUTPUT`/`GITHUB_STEP_SUMMARY` files. Reject GitHub expressions inside extracted scripts rather than evaluating them. It does not execute publishing, Azure login, or deployment steps.

- [ ] Add offline fixture cases for disabled CD, failed CI, PR/manual upstream, foreign repository, missing configuration, malformed HTTPS origin, debug enabled, missing database TLS, and valid configuration. Assert `ready=false` for ineligible cases, nonzero exit for eligible invalid configuration, and `ready=true` only after valid configuration. Assert synthetic secret values never appear in captured output or summaries.
- [ ] Run `bash scripts/test-release-preflight.sh` against the current workflow to establish the existing behavior. Add a structural assertion requiring a single preflight job to demonstrate the proposed graph change fails before implementation.
- [ ] Merge the two small preflight jobs without changing validation logic, secret scopes, numbered tags, registry coordination, SHA freshness, scan evidence, or publishing semantics. Attaching the environment earlier may alter approval behavior if reviewers are introduced later; document this explicitly.
- [ ] Run `bash scripts/test-release-preflight.sh` and inspect a dev/manual upstream completion. Verify that it cannot publish. Validate eligible configuration behavior only through an approved master release, not a synthetic production test.
- [ ] Compare scheduling overhead and total preflight duration before/after; expect only a few seconds saved on currently warm runs, not a minute-scale improvement.
- [ ] Keep full release checkout history: the changelog generator needs it. Keep release build and Trivy scan because numbered metadata and the generated changelog differ from CI.
- [ ] Document the changed graph and commit separately.

**Deliverable:** One fewer serial job allocation while keeping every release prerequisite.

### Task 5: Consolidate redundant read-only Azure queries

**Files:** Modify `scripts/deploy-production.sh`, `scripts/migrate-production.sh`, `scripts/test-deploy-production.sh`; update `docs/cd-workflow-testing.md`.

**Interfaces:** Fetch the main sitecontainer once and extract `properties.isMain`, `properties.targetPort`, and `properties.image` from that response. Fetch PostgreSQL server metadata once and extract `state`, `backup.backupRetentionDays`, and `backup.earliestRestoreDate`. Parse selected fields locally with Python's standard library; never print whole Azure responses or app settings.

- [ ] Extend mocked tests with missing main-container fields, wrong target port, missing prior image, non-Ready server, retention below seven days, and missing restore metadata. Require failure before image update. Retain all existing rollback/fallback tests.
- [ ] Run `bash scripts/test-deploy-production.sh`; demonstrate the new expectations fail before modifying reads.
- [ ] Replace three repeated sitecontainer reads with one snapshot for initial checks, and three PostgreSQL `show` requests with one snapshot. Keep separate linuxFxVersion and RUN_MIGRATIONS validation, final image-state verification, fresh SHA checks, and registry digest re-verification.
- [ ] Update mocks to produce selected JSON fixtures and assert one initial sitecontainer fetch and one database metadata fetch per full migration window. Preserve the no-migrations case with no PostgreSQL management calls.
- [ ] Run `bash -n scripts/deploy-production.sh scripts/migrate-production.sh scripts/test-deploy-production.sh`, then `bash scripts/test-deploy-production.sh`; require all scenarios to pass.
- [ ] Compare `[timing]` output during approved releases. Do not overlap firewall creation with migration execution, leave a permanent firewall rule, or bypass its 65-second provisioning wait.
- [ ] Document and commit this change separately.

**Deliverable:** Lower Azure CLI/process/request overhead while preserving the same fail-closed safety checks. The external firewall operation and application startup remain explicit costs.

### Task 6: Evaluate startup latency and accept or roll back each optimization

**Files:** Update `docs/ci-workflow-testing.md` and `docs/cd-workflow-testing.md`; use the Task 1 analyzer and existing deployment `[timing]` markers. No production behavior change is required for this task.

**Interfaces:** Compare the same event/release categories using `summarize_runs`. Produce before/after tables with sample counts, medians, p95 when the sample is sufficient, failure classification, and heavy-job counts per PR update.

- [ ] Collect at least ten ordinary non-migration releases over normal release activity; annotate migration releases separately. Do not create production deployments solely to fill the sample.
- [ ] Correlate the existing 83–85s production verification with App Service container startup/image pull/application logs and first successful `/release/`, `/readyz/`, and `/accounts/login/` responses. Use read-only diagnostics; preserve secret redaction.
- [ ] Identify whether image fetching, application startup, static initialization, readiness/database connection, or polling granularity dominates. A polling loop duration is not proof that the polling sleep causes the delay.
- [ ] Leave polling timeouts, restart behavior, gunicorn settings, app capacity, and endpoint checks unchanged unless a separate measured proposal is approved. Faster success must still prove expected revision, database readiness, and login availability.
- [ ] Verify CI still passes real PostgreSQL tests and rejects a known failing regression in an isolated test branch. Verify existing deployment mock negative cases remain red, with rollback where appropriate.
- [ ] Accept each task only when its relevant metric improves or stays within the budget below, with no new correctness/authorization gaps. Revert the specific task if not; avoid stacking unmeasured changes.
- [ ] Publish measurements and remaining bottlenecks in the two guides. Hand off any infrastructure/startup redesign as a separately approved follow-up.

**Deliverable:** Measured improvements with no unsubstantiated claim that all delays are eliminated.

## Acceptance budgets and expectations

| Metric | Baseline/target | Interpretation |
| --- | --- | --- |
| Standalone warm dev/master CI | Observed 73–91s on recent examples; aim median <=90s and no >10% matched-baseline regression | Preserve speed; classifier adds some overhead |
| Heavy CI work per open-PR branch update | Two full runs today; target one full PR run | Roughly 50% less duplicate heavy work, not 50% lower CI latency |
| Non-migration end-to-end release | Observed about 4.7 minutes; aspirational <=4.5-minute median | Validate with samples; preflight/query savings may be modest |
| Migration end-to-end release | Observed about 6.3 minutes | Report separately; 65s firewall provisioning is not safely removed by this plan |
| Missing-job failures | Every new occurrence classified with evidence or explicitly unresolved | Never substitute empty successful results for missing checks |
| Correctness | All current application/security/release safeguards retained | Non-negotiable |

Current evidence does **not** justify promising a two-minute production deployment. The largest observed costs are production startup and firewall provisioning; duplicate CI reduction mostly improves runner consumption and feedback reliability.

## Execution order and rollback

1. Implement baseline and reliability tasks first.
2. Measure and ship duplicate suppression independently.
3. Measure and ship preflight consolidation independently.
4. Measure and ship consolidated Azure reads independently.
5. Review accumulated release evidence before proposing startup or infrastructure changes.

Before executing, use an isolated working branch/worktree, reconcile remote changes, and confirm the worktree is clean except for intended work. Each task gets its own reviewed commit; include the standard Copilot co-author trailer when commits are created. Do not commit secrets or raw production logs.

Rollback means reverting the individual workflow/helper change through normal Git review. Restore the original dev-push full-CI behavior if routing is unreliable. Never resolve a failed deployment by disabling scan thresholds, schema checks, health verification, or firewall cleanup.

## References

- [GitHub workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [GitHub workflow_run event and trust boundary](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)
- [Trivy Action caching and scan configuration](https://github.com/aquasecurity/trivy-action#cache)
- [Current CI testing guide](../../ci-workflow-testing.md)
- [Current release testing guide](../../cd-workflow-testing.md)

## Status

<<<<<<< HEAD
**Investigation complete to the extent supported by accessible evidence; implementation not started.** Balanced scope selected by the user. No workflows, application code, repository settings, or production resources were changed while creating this plan. The 14m45s no-job failure remains unresolved, and all performance targets are prospective rather than verified outcomes.
=======
Implementation is on the isolated `optimize-github-actions` branch, based on remote dev commit `409567a2`. Tasks 1–5 have local implementation and regression coverage. A fresh branch review found three introduced defects (Azure string targetPort handling, no-op release latency mixing, and incomplete job coverage); all three were reproduced with failing tests and fixed. Final offline validation passes: 19 Python unit/gate tests, 5 Node toast tests, 22 mocked deployment scenarios, 17 routing cases, and 16 preflight cases; workflow actionlint and Bash syntax checks pass. The analyzer's 16 tests also pass on Python 3.11, and the four new Python files parse with Python 3.12 grammar. No push, production deployment, or repository/environment settings change has been performed.

| Task | Implementation evidence | Still required after reviewed rollout |
| --- | --- | --- |
| 1 | Offline analyzer, 40 exported run/job metadata captures, explicit missing-job evidence | Root cause of the 885-second no-job failure remains unresolved |
| 2 | Fast analyzer/Node/mocked deployment checks wired before the complete Django suite; remote toast fix preserved | Python 3.12 + disposable PostgreSQL 16 integration on the Actions runner |
| 3 | Fail-safe classifier and branch/PR/API-error fixtures; stable CI gate names | Actual standalone-dev, open-PR, merge-ref, and manual-run verification |
| 4 | Combined eligibility/configuration job; invalid-config and secret-redaction fixtures | Trusted master release and environment-gate behavior verification |
| 5 | One initial main-container snapshot and one backup-state snapshot; negative-case and rollback tests | Live Azure timing comparison during an approved release |
| 6 | Full versus routing-only timing categories, started-heavy-job counts, baseline and rollout instructions | At least ten ordinary non-migration releases, startup correlation, and before/after acceptance measurements |

Local Python is 3.14.6; CI remains pinned to 3.12. Git Bash tests use a test-only mode-bit shim for their synthetic env file; production/Linux permission checks are unchanged. A local Django integration attempt failed because disposable PostgreSQL was unavailable (`127.0.0.1:65534`, connection refused); no production database was contacted. Do not interpret offline passing checks as verified live deployment speed. The production startup and temporary firewall provisioning delays remain intentional investigation targets, not removed safeguards.
>>>>>>> 0ea5b812f924912d9862d1a8f3140643464c4d19
