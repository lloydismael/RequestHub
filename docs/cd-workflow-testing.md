# Enabling automatic production releases

## Current behavior

`Production Release` is defined in `.github/workflows/production-release.yml`. It listens for successful `Container Security` runs caused by pushes to `master` in this repository. It excludes development pushes, pull requests, and manually dispatched CI runs. The workflow must be on the default branch to receive `workflow_run` events.

It is disabled unless repository variable `CD_ENABLED` is exactly `true`. This is the only activation switch. Do not enable it until every prerequisite below has been reviewed. Production releases have no staging or reviewer gate, as requested; a restart or failed deployment can directly affect users.

The **Check release eligibility and configuration** job combines the two preflight stages in one hosted runner allocation. Its summary distinguishes **DISABLED** (switch unset/false) from **INELIGIBLE** (upstream CI is not a successful trusted master push). Only eligible runs execute the configuration step. Missing values fail that job with configuration names only, before image building or publishing. Its `ready` output becomes `true` only after configuration succeeds; disabled/ineligible runs return `false`. Configuration checks require production debug settings and database TLS but do not prove permissions or recovery readiness.

The combined job uses the `production` environment earlier than before. If environment reviewers or branch restrictions are added, they apply before eligibility is explained, including disabled/ineligible upstream completions. Review that behavior before rollout; no repository or environment settings are changed by this optimization. Image publishing still requires successful trusted master-push CI, and the deploy job retains its own production environment.

The workflow serializes releases without cancelling an active one and rejects superseded commits before publishing/deploying. CI keeps its own cancellation policy. Use branch protection to restrict changes to `master` and require all three CI jobs plus review. Restrict the GitHub `production` environment to the default `master` branch. The environment ref is the downstream workflow's default-branch ref, not the upstream merge ref.

## Offline regression checks

CI runs `bash scripts/test-deploy-production.sh`, `bash scripts/test-ci-routing.sh`, and `bash scripts/test-release-preflight.sh` before the full Django suite. It uses synthetic settings and mocks GitHub, Azure, Docker, and HTTP calls; it never deploys or connects to production. Run it in Linux or Git Bash. Git Bash uses a narrowly scoped test-only permission shim for its synthetic env file because Windows cannot enforce Unix mode 0600; production scripts and Linux CI retain the actual mode check. Node toast behavior tests also run in the existing Django CI job, without npm packages.

## Measuring release latency

Use the offline analyzer and export commands in [the CI testing guide](ci-workflow-testing.md#measuring-latency-and-duplicate-work). Annotate individual release REST objects with `migration_window: "executed"` or `"skipped"` only when the controlled-deployment timing log proves it; otherwise the analyzer reports `unknown`. Never infer skipped migrations merely from a fast release.

A November 5, 2026 capture of 20 completed release workflows contained **12 successful deployments**, with a downstream median of **245.5s**, and **5 successful preflight-only runs**, with a median of **9s**. The analyzer separates `release_stage` so disabled/ineligible no-op workflows cannot artificially lower deployment latency. Deployed samples still mix migration and non-migration runs until logs supply explicit annotations; neither group has enough complete samples for p95. The examined non-migration release spent 83s in production verification; the migration release spent 65s creating its temporary firewall rule and 85s in verification. Actual check/migrate execution was 9s. End-to-end CI-to-release time was about 4.7 and 6.3 minutes respectively.

Keep `[timing]` markers for Azure checks/image pulls, database state/backup checks, firewall create/delete, connectivity, check/migrate, image restart, and production verification. Collect at least ten ordinary non-migration releases during normal activity before assessing startup changes; do not deploy solely to create samples. Preserve revision, database readiness, and login checks while correlating timings with redacted App Service startup logs. Offline validation cannot prove a live speed improvement.

## Numbered Docker tags

The image remains `lloydismael12/request-hub`. The workflow reads existing Docker Hub tags, chooses the highest matching numbered tag, and increments its single-digit patch: `v51.8` to `v51.9`, then `v52.0`. It refuses to allocate a release if no baseline is found. Registry tags are authoritative; local image listings are not.

The image is built for `linux/amd64`, scanned, and pushed without rebuilding. Deployment Center uses the readable numbered reference, such as `lloydismael12/request-hub:v52.3`. The script verifies that the tag resolves to the scanned digest before migrations and again before image updating; migrations still execute the digest-pinned image. Tags can be changed externally, so this is less strict than digest-pinned Azure deployment. Neither `latest` nor existing numbered tags should be overwritten. All publishers, including manual release operators, must use the same release coordination: the workflow concurrency lock cannot prevent a separate manual publisher from racing it. Restrict token write access and pause manual publishing while CD runs.

README/Compose tags are not automatically committed after deployment. Use the run outputs and deployed image digest as release evidence, then update documentation through a reviewed PR when needed.

Successful deployment summaries record the numbered image reference, verified digest, commit, and previous rollback digest. For manual overrides, set repository `CD_ENABLED=false`, confirm no release is still running, then edit Deployment Center's `main` container to a known numbered image tag. Check database compatibility before selecting an older version. A later automated release can replace the manual selection after CD is re-enabled. Automatic failure recovery deliberately restores the previous digest, so Deployment Center may show a hash again after rollback. Existing digest-based deployments remain unchanged until a new release deploys this script.

## Required repository configuration

| Setting | Purpose |
| --- | --- |
| Secret `DOCKERHUB_USERNAME` | Registry account used to publish/pull |
| Secret `DOCKERHUB_TOKEN` | Repository-scoped publishing credential; never use an account password |
| Variable `CD_ENABLED` | Explicit release switch; leave unset/false during preparation |

Configure these variables on the `production` environment:

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Phildata deployment identity using GitHub OIDC |
| `AZURE_RESOURCE_GROUP`, `AZURE_WEBAPP_NAME` | Existing production target |
| `AZURE_MAIN_CONTAINER` | Actual main sitecontainer resource name |
| `AZURE_POSTGRES_SERVER` | `requesthub-postgre`, used for temporary firewall access and backup metadata checks |
| `PRODUCTION_URL` | Canonical HTTPS origin, without a trailing slash |

`CD_ENABLED` must be a repository variable; an environment-only value cannot enable the eligibility job. Docker Hub secrets must be repository-accessible because the publishing job does not use the production environment. Do not create different environment overrides for those secrets.

Create a federated credential for this repository's `production` environment identity. Scope Azure permissions to the Web App/configuration needed for image updates and restarts. The app's runtime identity and registry pull authentication remain separate. Never send tokens, database passwords, or env-file contents through chat.

## GitHub-hosted migration execution

Deployment uses `ubuntu-24.04`; no self-hosted runner, dedicated VM, or `/opt` files are required. OIDC authentication remains unchanged.

Add a **production environment secret** named `MIGRATION_ENV_FILE`. Its contents must use unquoted `KEY=value` lines, including `DJANGO_DEBUG=False`, a valid `DJANGO_SECRET_KEY`, canonical domain settings, explicit `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`, and `DB_SSLMODE=require` (or `verify-ca`/`verify-full`). Use the production server hostname and migration-capable database user. Do not include unrelated email credentials. Never send contents through chat or commit them. The workflow writes the secret under `$RUNNER_TEMP` with mode 0600 and removes it in an always-run step. Custom certificate mounts are not implemented; container-inaccessible `DB_SSLROOTCERT` paths are rejected.

In addition to the Web App assignment, grant the deployment identity a custom Azure role scoped to the PostgreSQL server with these management actions: `Microsoft.DBforPostgreSQL/flexibleServers/read`, `Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read`, `Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/write`, and `Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/delete`. These are not database SQL permissions; SQL access comes from the migration credentials. Do not grant subscription-wide Contributor merely to manage one server's firewall.

The helper fetches state, backup retention, and earliest-restore metadata in one read-only PostgreSQL server response, then validates selected JSON fields locally. The deploy script similarly fetches main-container identity, port, and rollback image in one initial response rather than three calls. Missing/malformed fields fail before image updating; full Azure responses and app settings are not logged. The sitecontainers API defines `targetPort` as a string, so the gate accepts the documented `"8000"` representation as well as integer `8000`, while rejecting other ports. Separate RUN_MIGRATIONS checks, final image-state verification, SHA freshness checks, and tag/digest re-verification remain unchanged. These snapshots are not cached between releases and do not remove firewall provisioning or application startup latency.

The helper requires the server state to be Ready, retention of at least seven days, and nonempty earliest-restore metadata. These checks establish available restoration metadata, not a recent successful backup or a rehearsed restore. A platform-owned restoration rehearsal and backward-compatible migration review remain required operational prerequisites. The old local backup executable is no longer used.

The helper obtains the runner's IPv4 address from `https://api.ipify.org`, validates its format, and creates only a start=end rule named `github-cd-RUN_ID-RUN_ATTEMPT`. It retries database connectivity for up to about one minute, requires TLS settings, runs checks/migrations, and then deletes that rule. When called from the deployment script, deletion starts in the background after migrations succeed and is confirmed before the release can succeed; on any failure, the helper deletes it synchronously before exiting. Existing firewall rules, including any allow-Azure-services rule, are not changed. Existing same-name rules cause failure rather than being taken over.

EXIT/signal traps perform cleanup, with a job-level cleanup step as fallback for owned rules. Cleanup failures fail the release. Force cancellation or runner loss can defeat cleanup; inspect rules with the `github-cd-` prefix, correlate the run and attempt, and remove only the orphaned rule after confirming no migration is active. Do not cancel a migration or delete another run's rule blindly.

Configure the Web App's `RUN_MIGRATIONS=false` only after hosted migration execution and recovery have been rehearsed. The deployment script verifies it before any migration or image update.

## Deployment sequence

1. A successful trusted `master` CI run triggers a serialized release. Check eligibility and production configuration before building or publishing.
2. Allocate an unused numbered tag; generate the release changelog manifest (a failure there only warns); build, generate SBOM, and scan the exact image. Failed security checks prevent publishing/deployment, while generated evidence is retained for 30 days.
3. Publish the scanned image and pass its version, commit, and digest to the production job.
4. Verify current `master` and prepare the temporary protected migration configuration.
5. Authenticate to Azure using OIDC and to Docker Hub using the scoped token.
6. Confirm `sitecontainers` mode, the specified container's `isMain=true`, target port 8000, and disabled startup migrations. Classic Docker mode deliberately fails closed; verify the real production mode rather than converting it automatically.
7. Pull and pin the old image for rollback. Pull the candidate and verify its OCI revision matches the approved CI commit.
8. Compare the commit label of the currently deployed image with the release commit. If the deployed commit is a verified ancestor of the release and no `**/migrations/*.py`, `requirements.txt`, `requirements.lock`, or `request_hub/settings*.py` file changed between them, skip the migration window entirely (steps 8a-8c). Any uncertainty (missing or malformed label, unknown or diverged history, git failure) runs the full window.
   - 8a. Check backup metadata, create owned single-IP firewall access, and verify connectivity.
   - 8b. Run configuration checks/migrations using the exact image. No settings are printed or downloaded from App Service.
   - 8c. Start deleting the firewall rule in the background and continue; the rule is not left to the workflow's final cleanup step unless deletion fails.
9. Update only the main sitecontainer's image to the verified numbered tag using Azure Resource Manager, preserving its registry authentication, environment configuration, mounts, and other containers. Restart the Web App.
10. Poll `/release/`, `/readyz/`, and `/accounts/login/` with finite timeouts. Verify the expected commit and Azure image reference. Then confirm the firewall rule deletion finished; the release is marked failed if it did not.

`/release/` exposes only version/revision metadata. `APP_REVISION` is baked from the build commit; remove any stale platform override before activation. `APP_VERSION` can also be overridden by existing App Settings, so the commit/digest is the authoritative verification signal; reconcile an existing version override deliberately before enabling numbered-version reporting.

`/readyz/` checks a database connection with `SELECT 1`. `/healthz/` remains database-independent. Static collection remains at runtime in this implementation, using the existing startup default; build-time static collection is not enabled. Do not set `RUN_COLLECTSTATIC=false` unless static assets have been prepared independently.

## Release changelog

Each release creates its own entry under **Management → Changelogs**. Non-visual releases publish automatically; a release with incomplete UI/UX screenshots stays in draft until an admin completes and publishes its evidence.

- **Manifest.** In the `release-image` job, the step "Generate release changelog manifest" runs `python -m hub.release_changelog` after the tag is allocated and before the image is built. It writes the git-ignored `hub/release_changelog_manifest.json`, which `COPY . .` bakes into the image (`.dockerignore` has no rule for it). The step is `continue-on-error`: a failure only warns, and the image is built without a manifest. The checkout uses `fetch-depth: 0` so the full history is available.
- **Range.** The generator lists `git log --no-merges BASELINE..revision`, capped at 500 commits. `BASELINE` is the v55.1 release (`a0f37126304c245797c346abf2df664e25fa5310`) and the range is cumulative on purpose: commits already logged are dropped when the entry is created. If the baseline is missing or not an ancestor of the release commit, the generator prints `error: ...`, exits 1, and writes nothing. Merge commits are ignored.
- **Classification.** A subject such as `Prefix - text`, `prefix: text`, or `prefix(scope): text` is classified by its prefix, which is then stripped. `Feature`/`Features`/`Feat` → New; `Fix`/`Bugfix`/`Hotfix` → Fixed; `Docs`/`Doc`, `Test`/`Tests`, `Chore`, `CI`, `Build`, `Refactor`, `Style` → Maintenance; `Enhancement`/`Improvement`/`Perf` → Improved. Without a prefix, a subject starting "Fix ", "Fixes " or "Fixed " is Fixed, one starting "Bump " is Maintenance, and anything else is Improved and kept as written. Titles are capped at 200 characters.
- **Skipping.** A commit whose subject or body contains `[skip changelog]` (any case) is left out, as is the subject "Initial plan" and a commit with an empty subject. Put the tag in the commit message of changes that users should not see.
- **Sync at start.** When the container starts, `entrypoint.sh` runs `python manage.py sync_release_changelog` when a release manifest or the packaged changelog-assets directory exists. Packaged evidence enriches matching existing releases even when there is no current manifest. The sync is limited to 60 seconds, and a failure or timeout prints `Release changelog sync failed; continuing startup` and the app still starts. It is independent of `RUN_MIGRATIONS`: production keeps `RUN_MIGRATIONS=false` and still syncs. Set `RUN_CHANGELOG_SYNC=false` to turn it off (`true` is the default; any other value stops startup). The entry therefore appears when the new container starts, not when the image is built.
- **One entry per release.** The command creates one entry per release revision and skips commits already stored on any automatic entry (published or not), so repeated starts print `Nothing new to log`. New UI/UX changes without complete screenshot pairs are stored as drafts. A corrupt manifest or missing/invalid declared image exits non-zero with `Invalid release manifest: ...`; changelog/media writes roll back and startup still continues. The entry date is the manifest's generation time in Asia/Manila.
- **Visual evidence.** Changes to `templates/` or `static/` set `requires_comparison: true`. Each affected commit requires at least one labeled pair containing `title`, `before_text`, `after_text`, `before_image` and `after_image`; up to 20 pairs per commit can document different screens and viewports. Images are PNG, JPEG or WebP, at most 5 MB each. Sync imports packaged image bytes into database-backed media with content-based names, so restarts do not duplicate files. New comparisons coexist with legacy release-wide before/after fields.
- **Editing.** Admins can edit an automatic entry's title, summary and comparisons, upload missing screenshots, and add another screenshot pair. Publishing is rejected while a required pair is incomplete. To remove an entry, unpublish it rather than deleting it, so its commits are not relogged. Packaged evidence only fills missing comparison content, preserving existing comparison descriptions and publication choices; a later release can enrich an earlier draft but does not publish that draft automatically. A release that is rolled back after its container started keeps its entry; edit or unpublish it under **Management → Changelogs**.
- **First release.** The first release after this feature is merged lists everything since v55.1. It also ships migration 0056 (adds `release_sha` and `changes` to changelog entries), so it runs the full migration window.

### Preparing screenshot evidence

Capture the actual previous and updated UI using the same synthetic fixture and viewport. Do not relabel an after-only screenshot as "before", use live personal/request data, or claim that a static screenshot proves provider calls, email delivery or animations. Include meaningful before/after descriptions for non-visual changes as well; do not invent UI screenshots for backend-only fixes.

After the UI commit exists, add a follow-up evidence commit before release. Put its assets and a curated `manifest.json` in `hub/changelog_assets/<capture-set>/`. The file uses the normal manifest structure (`version`, full `revision`, timezone-aware `generated_at`, and `commits`). For pre-release evidence, `version` may be `next` and `revision` the captured after revision; the generator matches **commit SHA**, not the capture-set version. To repair a known shipped release, use its exact version and release revision. Packaged repairs include curated `v56.4` and `v56.5` manifests; older release entries are not recreated. Importing evidence completes an existing draft without publishing it: review the restored comparisons and explicitly publish the release in **Management → Changelogs**.

Each annotated commit retains its `sha`, `type` and `title`, and can add `before_text`, `after_text`, `requires_comparison` and `comparisons`. A screenshot pair looks like this (replace the example revision values with the actual full SHAs):

```json
{
  "title": "Request update composer",
  "before_text": "Updates used a plain text field.",
  "after_text": "The composer suggests eligible stakeholders to mention.",
  "before_image": "next/composer-before.webp",
  "after_image": "next/composer-after.webp",
  "before_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "after_revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "capture_note": "Historical UI capture with synthetic data; no external messages were sent."
}
```

Paths are relative to `hub/changelog_assets/`; absolute paths, traversal and unsupported formats are rejected. Capture revisions and notes are shown alongside the comparisons instead of implying that every pre-change snapshot came from the preceding numbered release. Replacing screenshot files in the editor clears their old capture provenance. Run `python manage.py sync_release_changelog` to import packaged evidence locally. Screenshots inherit the release's visibility: signed-in readers can view only published, non-future releases, admins can preview drafts, and responses use `no-store`.

## Release speed and timing

The October 22, 2026 release took about 6 minutes: roughly 73 seconds to build, scan, and publish and 286 seconds in the production job. Time in the production job was about 19 seconds of Azure checks and image pulls, 74 seconds to open firewall access and probe the database, 70 seconds deleting the firewall rule, and about 105 seconds from image update through verification. Speed measures:

- **Image build cache.** The release build reads (never writes) the `request-hub-image` Actions cache populated by the CI run for the same commit, so dependency layers are not rebuilt. OS packages are still refreshed on every build.
- **Overlapped pulls and shorter polling.** The release image downloads while the Azure checks run, and readiness is polled every 5 seconds within the same 10-minute budget.
- **Skipped migration window.** Releases without schema-affecting changes skip the firewall, probe, `check --deploy`, and migrate steps (about 144 seconds). The backup-retention gate applies only to releases that run the window, and `check --deploy` against the production configuration runs only inside it. Applying the release still verifies the application boots and reports ready on the production configuration, and a failed verification rolls back.
- **Background firewall cleanup.** On releases that do run migrations, the firewall rule is deleted while the app restarts. A failed cleanup no longer stops the image update; it fails the release, names the rule, and leaves the workflow's final cleanup step to retry. Treat that failure as urgent: the new image is already live.

Deployment and migration scripts print `[timing] <phase>: <seconds>s` lines in the job log for each phase. Use them to confirm these savings on the first real release and to find the next hot spot. The step summary records whether the migration window was executed or skipped and the deployed revision used for the decision.

To force the full window for a release, include a change to a file matching the patterns above or temporarily remove the revision label from the deployed image; there is deliberately no skip-bypass input.

## Recovery

If image update or post-deployment verification fails, the script attempts to restore the previous image digest, restarts the app, and checks readiness. The release stays failed even if rollback succeeds. It does not reverse database migrations. Releases requiring incompatible/destructive migrations must not use this automatic path; disable CD and follow a reviewed maintenance/recovery process.

Failures during pre-update migrations stop the release without replacing the running image. Some migration operations may already have completed; inspect migration state and use the backup/migration playbook rather than blindly rerunning or reversing them. Do not cancel an in-progress migration.

Set `CD_ENABLED=false` to stop subsequent eligibility checks from admitting new releases. This does not cancel an already admitted build or deployment. There is no manual dispatch in the current release workflow; re-running old jobs is not a way to bypass successful CI or current-master guards.

## Validation before activation

- Validate the workflow with `actionlint` and the shell scripts with `bash -n`. Run `bash scripts/test-deploy-production.sh` on Linux or in a Linux container; it exercises the deployment and migration scripts against mocked `az`, `docker`, `gh`, `git`, and `curl` for success, skipped-migration, stale-commit, migration-failure, cleanup-failure, and rollback scenarios.
- Run focused `hub.test_release_health` tests and the full suite against disposable PostgreSQL.
- Build the image locally with a synthetic version/revision; prove startup defaults, disabled migrations, revision reporting, database readiness, and local Docker health under production HTTPS settings.
- Rehearse migration failure, image update failure, verification timeout, and rollback using mocked Azure/registry tools or a dedicated nonproduction target. Syntax checks alone do not prove rollback.
- Test successful/failed CI triggers, dev/PR exclusions, serialization, and stale-commit rejection before providing production credentials.
- Verify actual production mode, recovery readiness, migration secret, persistent uploads, and OIDC/Web App/PostgreSQL firewall permissions through approved validation.
- Keep `CD_ENABLED` unset/false until all prerequisites pass. A separately approved first activation causes a real production release; it is not a harmless dry run.

No registry push, Azure update, firewall mutation, identity creation, or activation is performed merely by adding these files.