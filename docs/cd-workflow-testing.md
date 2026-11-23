# Enabling automatic production releases

## Current behavior

`Production Release` is defined in `.github/workflows/production-release.yml`. It listens for successful `Container Security` runs caused by pushes to `master` in this repository. It excludes development pushes, pull requests, and manually dispatched CI runs. The workflow must be on the default branch to receive `workflow_run` events.

It is disabled unless repository variable `CD_ENABLED` is exactly `true`. This is the only activation switch. Do not enable it until every prerequisite below has been reviewed. Production releases have no staging or reviewer gate, as requested; a restart or failed deployment can directly affect users.

The **Check release eligibility and configuration** job now rejects disabled or ineligible upstream completions at job level, before allocating a hosted runner or entering the production environment. Eligible runs still execute the existing explanatory and configuration checks. Missing values fail with configuration names only, before image building or publishing. Its `ready` output becomes `true` only after configuration succeeds. Configuration checks require production debug settings and database TLS but do not prove permissions or recovery readiness.

Both eligible preflight and deployment use the `production` environment. Restrict it to `master`; a human deployment approval gate is not added. The approved merge policy separately requires one PR approval and the three existing CI checks. Repository/environment policies are live GitHub settings, not enforced by editing workflow YAML alone.

The workflow serializes releases without cancelling an active one and rejects superseded commits before publishing/deploying. CI keeps its own cancellation policy. Use branch protection to restrict changes to `master` and require all three CI jobs plus review. Restrict the GitHub `production` environment to the default `master` branch. The environment ref is the downstream workflow's default-branch ref, not the upstream merge ref.

## Offline regression checks

CI runs `bash scripts/test-deploy-production.sh`, `bash scripts/test-ci-routing.sh`, and `bash scripts/test-release-preflight.sh` before the full Django suite. It uses synthetic settings and mocks GitHub, Azure, Docker, and HTTP calls; it never deploys or connects to production. Run it in Linux or Git Bash. Git Bash uses a narrowly scoped test-only permission shim for its synthetic env file because Windows cannot enforce Unix mode 0600; production scripts and Linux CI retain the actual mode check. Node toast behavior tests also run in the existing Django CI job, without npm packages.

### Container runtime compatibility fix

The container stays on digest-pinned Python 3.12, matching Django validation and the build-tool cleanup paths. Before this fix, the Python 3.15 image upgrade forced locked `psycopg2-binary==2.9.10` to build from source and stopped Container Security with `Error: pg_config executable not found.` Restoring `python:3.12.13-alpine3.24` restores binary-wheel installation without adding a compiler or PostgreSQL development packages.

Dependabot still proposes Python patch/digest updates, but minor/major runtime upgrades require a coordinated dependency, CI, and cleanup review. `python -m unittest discover -s scripts -p test_workflow_steps.py` covers the runtime pin and update policy alongside the existing workflow checks. This fix is included in the generated release changelog; its exact-parent repository-change captures supply before/after evidence for publication in Notifications → Changelogs.

Django validation now tests classic tracker pagination with classic mode enabled, builds the original 43-column SQR import fixture, and resolves performance scripts through Django's static storage. The release changelog records this test maintenance; verify its exact-parent repository-change evidence and Notifications → Changelogs visibility after release.

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
2. Allocate an unused numbered tag; generate the required release changelog manifest (failure stops the release); import validated screenshot evidence from the exact upstream CI run/attempt; build, smoke-test against disposable PostgreSQL, generate SBOM, and scan the exact image. Failed security checks prevent publishing/deployment, while generated evidence is retained for 30 days.
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

`/readyz/` checks a database connection with `SELECT 1`. `/healthz/` remains database-independent. Static files are collected during the Docker build; the image defaults `RUN_COLLECTSTATIC=false`. The new container smoke check exercises packaged static files without enabling runtime collection. Production retains controlled migrations with `RUN_MIGRATIONS=false`.

## Release changelog

Each release creates its own entry under **Management → Changelogs**. New generated releases require screenshot evidence for every logged change, including backend, documentation, tests and maintenance. Incomplete evidence holds publication, not deployment.

- **Manifest.** In the `release-image` job, the step "Generate release changelog manifest" runs `python -m hub.release_changelog` after the tag is allocated and before the image is built. It writes the git-ignored `hub/release_changelog_manifest.json`, which `COPY . .` bakes into the image (`.dockerignore` has no rule for it). Generation is required: an error stops the image build rather than silently deploying changes without a changelog. The checkout uses `fetch-depth: 0` so the full history is available.
- **Range.** The generator lists the full `git log --no-merges BASELINE..revision`, without silently truncating history. `BASELINE` is the v55.1 release (`a0f37126304c245797c346abf2df664e25fa5310`) and the range is cumulative on purpose: commits already logged are dropped when the entry is created. If the baseline is missing or not an ancestor of the release commit, the generator prints `error: ...`, exits 1, and writes nothing. Merge commits are ignored.
- **Classification.** A subject such as `Prefix - text`, `prefix: text`, or `prefix(scope): text` is classified by its prefix, which is then stripped. `Feature`/`Features`/`Feat` → New; `Fix`/`Bugfix`/`Hotfix` → Fixed; `Docs`/`Doc`, `Test`/`Tests`, `Chore`, `CI`, `Build`, `Refactor`, `Style` → Maintenance; `Enhancement`/`Improvement`/`Perf` → Improved. Without a prefix, a subject starting "Fix ", "Fixes " or "Fixed " is Fixed, one starting "Bump " is Maintenance, and anything else is Improved and kept as written. Titles are capped at 200 characters.
- **Skipping.** A commit whose subject or body contains `[skip changelog]` (any case) is left out, as is the subject "Initial plan" and a commit with an empty subject. Put the tag in the commit message of changes that users should not see.
- **Sync at start.** When the container starts, `entrypoint.sh` runs `python manage.py sync_release_changelog` when a release manifest or the packaged changelog-assets directory exists. Packaged evidence enriches matching existing releases even when there is no current manifest. The sync is limited to 60 seconds, and a failure or timeout prints `Release changelog sync failed; continuing startup` and the app still starts. It is independent of `RUN_MIGRATIONS`: production keeps `RUN_MIGRATIONS=false` and still syncs. Set `RUN_CHANGELOG_SYNC=false` to turn it off (`true` is the default; any other value stops startup). The entry therefore appears when the new container starts, not when the image is built.
- **One entry per release.** The command creates one entry per release revision and skips commits already stored on any automatic entry (published or not), so repeated starts print `Nothing new to log`. Evidence retries update the original entry instead of relogging its changes. New-policy changes without complete screenshot pairs are stored as drafts. A corrupt manifest or missing/invalid declared image exits non-zero with `Invalid release manifest: ...`; changelog/media writes roll back and startup still continues. The entry date is the manifest's generation time in Asia/Manila.
- **Required evidence.** Generated manifests declare `evidence_policy: "all_changes"` and `requires_comparison: true` for every commit. Each commit requires at least one labeled pair containing `title`, `before_text`, `after_text`, `before_image`, `after_image`, `before_revision` and `after_revision`; up to 20 pairs can document different screens and viewports. The before revision must be the commit's exact first parent (`parent_sha`) and the after revision its exact SHA. Images are PNG, JPEG or WebP, at most 5 MB each. Sync imports image bytes into database-backed media with content-based names, so restarts do not duplicate files. Legacy policy-less manifests and release-wide comparisons remain supported.
- **Editing.** Admins can edit an automatic entry's title, summary and comparisons, upload missing screenshots with their exact revisions, and add another screenshot pair. Large comparison editors show two changes per page to respect Django's field/file upload limits; save each page before switching. Unshown changes are retained, and publishing is rejected until required evidence is complete across all pages. New system drafts opt into automatic publication when all evidence arrives; clear the automatic-publication checkbox to hold a draft. Unpublishing disables automatic republication. Legacy drafts retain their existing publication choices unless explicitly opted in. Packaged evidence preserves admin comparison content and holds. A release that is rolled back after its container started keeps its entry; edit or unpublish it under **Management → Changelogs**.
- **First release.** The first release after this feature is merged lists everything since v55.1. It also ships migration 0056 (adds `release_sha` and `changes` to changelog entries), so it runs the full migration window.

### Recovery of the v57.1–v57.6 backlog

The original backlog used exact first-parent repository reports to recover six releases that had been kept in draft. The versioned manifests now use verified application screenshots wherever the corresponding screen can be recovered. The commit descriptions still record backend, tests, and other nonvisual work.

| Release | Evidence in Notifications |
| --- | --- |
| v57.1 | Mobile seasonal sign-in layout. |
| v57.2 | Changelog comparison-editor pagination and mobile festive sign-in. |
| v57.3 | Technical reports for validation hardening and the stored-image regression test. |
| v57.4 | Mobile avatar controls and seasonal sign-in on desktop and mobile. |
| v57.5 | Technical report for contributor conduct guidance. |
| v57.6 | Seasonal sign-in lighting on desktop and mobile; the captures do not demonstrate telemetry. |

Images are stored alongside each versioned manifest, not in the Git-ignored `hub/changelog_assets/automatic/` capture cache. The same packaging also supplies two unreleased SQR shell comparisons for commit `73ab83f5`; those assets do not publish a release on their own.

`sync_release_changelog` enriches matching existing entries from these packaged manifests without relogging commits. System drafts publish when evidence is complete. Legacy/admin-held drafts still require an explicit publication choice; the v57.1 legacy draft was explicitly opted into publication during this authorized recovery. Existing release titles, summaries, dates and previous-version links are preserved.

### Unreleased: Branded README overview

- **Before:** The project README combined the user introduction with long setup and technical references.
- **After:** The [README](../README.md) keeps the original Phil-Data / ESG banner and adds grouped navigation, a short workflow, and a synthetic Reports screenshot. [Portal usage](using-request-hub.md), [local development](local-development.md), and linked maintainer guides carry the details. No application behavior changed.
- **Technical evidence:** [Before](../hub/changelog_assets/pending-readme-overview/before.png) · [After](../hub/changelog_assets/pending-readme-overview/after.png) · [Capture details](../hub/changelog_assets/pending-readme-overview/capture.json). These reports compare the committed baseline and working tree without exposing source text. A disposable release check confirmed that **Notifications → Changelogs → Technical evidence** hides draft reports and serves both original images after test publication. Release CI captures the exact committed revisions; nothing was published live.

### Unreleased: Profile photo shortcut

- **Before:** On Settings, the Form Editor turned the camera badge into overflowing “Profile photo” text.
- **After:** Only the label inside the upload form receives the configured field name. The camera badge stays visible and still opens the file picker. The runtime URL was updated so browsers do not reuse the old script. A Chromium regression test checks the icon, field label, and file picker.
- **Visual evidence:** [Before](../hub/changelog_assets/pending-profile-avatar-shortcut/before.png) · [After](../hub/changelog_assets/pending-profile-avatar-shortcut/after.png) · [Capture details](../hub/changelog_assets/pending-profile-avatar-shortcut/capture.json). These are working-tree previews with a synthetic account. The `profile-avatar-shortcut` recipe captures the exact revisions for **Notifications → Changelogs → Visual changes** after release.

### Unreleased: Evergreen Canopy Christmas lights

- **Before:** The Christmas navbar used SVG lights with a fixed twinkle. The Themes panel offered on/off controls but no way to adjust the glow or move the cord.
- **After:** The wrapped layout, socket positions, and CSS Liquid Glass stay in place. WebGL2 ray-traces bulbs, filaments, and reflections over the glass, with ripples when you press an open area of the navbar. Drag an exposed edge of the cord or use **Nudge cord** in **Themes → Christmas, your way**. Both strands and their sockets move together while the corners stay fixed. There are still 36 bulbs at 1024 px and wider, 24 from 480 to 1023 px, and none below 480 px.
- **Account setting:** **Light flow** starts at **Steady** and adjusts the speed of the clockwise glow. Changes preview immediately; **Cancel preview** restores the saved selection, and **Apply Theme** saves it across devices. The migration defaults existing accounts to Steady. The server validates speeds and preserves the saved value when an older client or a disabled control omits it.
- **Compatibility:** SVG remains available if WebGL2 is unavailable or loses context. Reduced motion and **Reduce Visual Effects** keep the lights steady and disable cord interaction. The frozen Santa transition uses SVG too. Both renderers are hidden in print, and the ray canvas is cropped to avoid horizontal scrolling.
- **Verification:** Django tests cover validation, persistence, and legacy submissions. Node and Chromium checks cover shared placement and motion, keyboard controls, form submission, fallback, print, and Santa transitions. The capture tests verify that older light-only changes are not assigned the canopy recipe and that both snapshots render without live data.

| Visual evidence | Before | After |
| --- | --- | --- |
| Same synthetic account and dark Themes panel, 1440 × 1040 | [SVG lights and the original controls](../hub/changelog_assets/pending-christmas-canopy/desktop-dark-before.png) | [Ray-traced canopy, Light flow, and Nudge cord](../hub/changelog_assets/pending-christmas-canopy/desktop-dark-after.png) |

The [capture metadata](../hub/changelog_assets/pending-christmas-canopy/capture.json) records the baseline revision, working-tree fingerprints, and image checksums. A signed-in reader on disposable PostgreSQL could not see the draft or retrieve either image. After test publication, **Notifications → Changelogs → Visual changes** displayed both PNGs, which matched the original bytes and used `no-store`. The stills show the Steady setting, not animation or hardware performance. Nothing was published live; trusted release CI captures the exact first-parent and change revisions.

### Unreleased: Evergreen Canopy on seasonal sign-in

- **Before:** Sign-in used a separate SVG strand with fixed twinkle, snowfall, and a warm bloom from the winter photo.
- **After:** The sign-in card shares the navbar’s wrapped strand, ray-traced bulbs and glass lighting, and cord physics. In dark mode the snow falls and the bulbs cast matching colors onto the background. **Christmas effects** offers **Light flow**, **Nudge cord**, and **Pause motion**. The flow starts at **Steady**; the pause choice stays in the browser. Snow remains on phones below 480 px, where the cord and its lights are hidden.
- **Compatibility:** WebGL2 falls back to SVG when unavailable or lost. Pause and reduced motion keep a still scene. The seasonal site switch, saved-theme expiry, and sign-in form and authentication are unchanged.
- **Interaction:** The cord is draggable where it crosses inside the top and bottom edges, without covering the sign-in controls. Background reflections hold their last frame while the page is hidden and realign when it returns.
- **Verification:** Django checks the seasonal gate, asset order, and sign-in. Node and Chromium cover snowfall, controls, inward cord grabs, hidden reflection frames, responsive layout, and renderer fallback. The offline capture keeps page scripts blocked and verifies that snow, bulbs, and reflections contribute pixels to the dark-mode screenshots.

| Visual evidence | Before | After |
| --- | --- | --- |
| Dark appearance, desktop, 1200 × 900 | [Original sign-in](../hub/changelog_assets/pending-evergreen-login/desktop-dark-before.png) | [Wrapped canopy and colored reflections](../hub/changelog_assets/pending-evergreen-login/desktop-dark-after.png) |
| Dark appearance, phone, 375 × 844 | [Original sign-in](../hub/changelog_assets/pending-evergreen-login/phone-dark-before.png) | [Snow without the strand](../hub/changelog_assets/pending-evergreen-login/phone-dark-after.png) |

The [capture metadata](../hub/changelog_assets/pending-evergreen-login/capture.json) identifies both working-tree snapshots and the image checksums. In disposable PostgreSQL, a signed-in reader could not see the draft or fetch its four images. After test publication, **Notifications → Changelogs → Visual changes** showed the desktop pair and loaded the phone pair when opened. All four responses matched the original PNG bytes and used `no-store`. These are synthetic stills, not exact-release evidence; release CI captures the exact Git revisions. Nothing was published live.

### Unreleased: Christmas lights at full speed

- **Before:** New accounts started at **Steady**, and existing accounts used their saved **Light flow**. Seasonal sign-in had a separate **Christmas effects** disclosure and could remember a pause in the browser.
- **After:** New accounts start at **100%**. The update resets existing accounts to **100%** once, including accounts that had saved **Steady** or a slower speed. Users can still preview and save any speed afterward. Other preferences, including reduced motion and disabled effects, are unchanged. Seasonal sign-in runs the lights at full speed without the disclosure; snow, reflections, and cord interaction remain.
- **Database:** [Migration 0033](../accounts/migrations/0033_christmas_light_flow_default.py) sets the default; [0034](../accounts/migrations/0034_reset_christmas_light_flow.py) changes only saved speeds that are not already **100%**. Rolling back the migration does not restore the old speeds.
- **Verification:** A PostgreSQL upgrade checked existing **Steady**, **35%**, and **100%** values and confirmed that a choice saved after the upgrade survives `migrate` reruns. Django covers untouched effect and accessibility preferences. Node and Chromium cover preview cancellation, reduced motion, scrolling, hidden pages, WebGL fallback, and mobile sign-in. Release CI captures the new-account and sign-in changes and uses a technical report for a standalone backfill; the offline fixture does not run data migrations.

| Visual evidence | Before | After |
| --- | --- | --- |
| Existing account, dark Themes panel | [35%](../hub/changelog_assets/pending-christmas-light-flow-reset/existing-profile-before.webp) | [100%](../hub/changelog_assets/pending-christmas-light-flow-reset/existing-profile-after.webp) |
| New account, dark Themes panel | [Steady](../hub/changelog_assets/pending-christmas-light-flow-default/profile-themes-before.webp) | [100%](../hub/changelog_assets/pending-christmas-light-flow-default/profile-themes-after.webp) |
| Seasonal sign-in, desktop | [With the disclosure](../hub/changelog_assets/pending-christmas-light-flow-default/sign-in-desktop-before.webp) | [Without the disclosure](../hub/changelog_assets/pending-christmas-light-flow-default/sign-in-desktop-after.webp) |
| Seasonal sign-in, phone | [With the disclosure](../hub/changelog_assets/pending-christmas-light-flow-default/sign-in-phone-before.webp) | [Without the disclosure](../hub/changelog_assets/pending-christmas-light-flow-default/sign-in-phone-after.webp) |

[Upgrade capture details](../hub/changelog_assets/pending-christmas-light-flow-reset/capture.json) and [earlier new-account and sign-in capture details](../hub/changelog_assets/pending-christmas-light-flow-default/capture.json) record the synthetic fixtures and image checksums. These working-tree stills show the layout and selected speed, not animation or exact-release revisions. In a disposable **Notifications → Changelogs** test, the draft and all eight images were hidden from a signed-in reader. After test publication, all four pairs were visible and the image responses matched the originals with `no-store`.

### Unreleased: Django startup recovery (PR #163)

- **Before:** A merge committed unresolved conflict markers. Django stopped importing its models with `SyntaxError: invalid decimal literal`, so both VS Code F5 launch configurations, Django Validation, and the Docker image's static collection step failed.
- **After:** Resolved all 79 conflict blocks across 26 files. Kept the reconciled request views, including the row lock and rollback for status updates, as well as the SQR error handling and notification cache fixes. Preserved the pending PPT defaults, tracker controls, and Request Progress dark-mode changes and their regression coverage. Kept the relocated one-off database scripts out of the runtime image without running them. The repair adds no migration or database configuration change.
- **Technical evidence:** The [before report](../hub/changelog_assets/pr163-startup-recovery/01-before.png), [after report](../hub/changelog_assets/pr163-startup-recovery/01-after.png), and [manifest](../hub/changelog_assets/pr163-startup-recovery/manifest.json) use the repair's exact first parent and result. In disposable PostgreSQL, a reader could not access the draft or its images. After test publication, **Notifications → Changelogs → Technical evidence** opened the report and served both original PNGs with `no-store`. The transaction was rolled back; no live release was published.

### Unreleased: Browser auto-refresh in the localhost F5 preview

- **Before:** Django restarted after Python saves, but developers had to refresh the browser manually to see changes. The pinned release did not reflect working-tree edits.
- **After:** The editable **Django: Request Hub (auto-reload)** preview refreshes the most recently opened localhost tab after Python, template, or static-asset saves when the optional browser reload package is installed. The event stream bypasses database-backed sessions, and pages returned directly by form submissions are not automatically reloaded. The pinned release, Azure migration checks, and production settings are unchanged. See the [local development guide](local-development.md#run-with-live-azure-postgresql-no-docker).
- **Verification:** A disposable Chromium preview refreshed after actual template, static-file, and Python saves. Normal Django settings and `DEBUG=False` did not expose the event stream. No Azure migration or release publication was performed.
- **Technical evidence:** [Before](../hub/changelog_assets/pending-local-browser-reload/report-before.png) · [After](../hub/changelog_assets/pending-local-browser-reload/report-after.png) · [Checksums](../hub/changelog_assets/pending-local-browser-reload/capture.json). The reports show working-tree fingerprints, not exact-release captures. A disposable PostgreSQL check confirmed that **Notifications → Changelogs → Technical evidence** hid the draft and its reports from a signed-in reader, then served both original images with `no-store` after fixture publication. Release CI captures the exact revisions.

### Unreleased: Docker-free VS Code debugging with live Azure PostgreSQL

- **Before:** The local F5 setup depended on loopback PostgreSQL and could not show live records. Restoring the Azure connection exposed a second blocker: the working tree required `accounts.0032_user_christmas_light_flow`, which was not applied in Azure.
- **After:** The existing **Django: Request Hub (auto-reload)** profile starts Django on Windows at `127.0.0.1:8000` without Docker or a new profile. When the checkout is ahead of Azure, it can run a verified Git export of the deployed revision without changing the working tree. The launcher reads only database settings from the ignored `.env`, verifies TLS, and checks migration history and model columns in a read-only transaction on startup and reload. The snapshot accepts only its recorded extra migration entries; other unexpected entries and missing migrations or columns still block startup. **Django: Request Hub** remains available for isolated local development. See the [local development guide](local-development.md#run-with-live-azure-postgresql-no-docker).
- **Verification:** VS Code F5 ran release `9d95c54` against Azure. `/` and `/readyz/` returned HTTP 200 before and after a reload that replaced the Django process. A read-only probe confirmed live account and request records exist. No migration ran, no live data was changed, and working-tree edits are not loaded in snapshot mode. Normal sign-in and other app actions can write to the live database.
- **Technical evidence:** [Before](../hub/changelog_assets/pending-live-azure-f5/report-before.png), [after](../hub/changelog_assets/pending-live-azure-f5/report-after.png), and [snapshot checksums](../hub/changelog_assets/pending-live-azure-f5/capture.json). These reports compare the guarded launcher before and after the snapshot fallback; they show no live data. The Notifications check uses a disposable fixture, not a live publication. Release CI captures the exact committed revisions.

### Unreleased: Request progress in dark mode

**Before:** Fixed navy rectangles behind the step names and statuses stood out against themed cards, including the Christmas theme's dark green card.

**After:** Step names and statuses use the card background and theme-aware text. On wider screens, the connectors run between labels instead of behind them. The phone layout keeps its vertical connectors. Light mode and lifecycle behavior are unchanged.

| Viewport | Before | After |
| --- | --- | --- |
| Desktop | [Before](../hub/changelog_assets/pending-request-progress-dark-mode/desktop-before.png) | [After](../hub/changelog_assets/pending-request-progress-dark-mode/desktop-after.png) |
| Phone | [Before](../hub/changelog_assets/pending-request-progress-dark-mode/phone-before.png) | [After](../hub/changelog_assets/pending-request-progress-dark-mode/phone-after.png) |

The [capture metadata](../hub/changelog_assets/pending-request-progress-dark-mode/capture.json) records the baseline, working-tree fingerprints, and image checksums. Both sides use the same synthetic request. The `request-progress-dark-mode` recipe captures the exact release revisions before publication. A Chromium regression check verifies that all five step labels and statuses sit on the card surface and have at least 4.5:1 contrast in the Default and Christmas dark themes at both widths. Light-mode captures were pixel-identical at both widths.

In disposable PostgreSQL, **Notifications → Changelogs** hid an unpublished test release and all four images from a signed-in reader. After test publication, the desktop pair appeared in the feed, the phone pair opened on demand, and all images were served byte-for-byte with `no-store`. No live release was published.

### Unreleased: Accounts, dashboard and SQR performance

- **Management → Accounts:** The old editor sent every account and hid rows in the browser. The new editor sends up to 50 rows at a time, searches the entire directory by name, and fetches only rows and pagination controls for in-page navigation. The search box follows the accepted results if paging cancels a pending search. Edits retain their original fields and signed schema across pages, even if a new schema is published. Save Changes validates the whole draft before writing; a failed save retains submitted values and links errors to the affected inputs.
- **Save limits:** Pages shrink for schemas with many custom fields or multiselect options. Native search, paging, and single-page saves work without scripts when the fields fit Django’s request limit. If saved retired choices make a page too large, it explains why native save is unavailable. The script counts submitted selections individually and keeps oversized drafts available for correction or row-by-row reversion. It never silently splits a batch or raises the global limit.
- **Form Editor:** The old Accounts formset did not load the published configuration. It now loads the definition and only the visible accounts' answers in two queries. Custom fields follow chained visibility rules in the browser and on the server. SQR conditions refresh after department changes rebuild the Account Manager choices. Account forms still save individually to preserve custom-answer validation and persistence.
- **Engineer dashboard and SQR:** Assigned and backup requests fetch owner relations with the row query instead of reading users per row. SQR history shares one quotation-edit lookup per read without relaxing restore or undo checks. New-submission notifications insert the same deduplicated recipients in one statement. No schema migration or background job changes are needed.
- **Regression coverage:** Added checks for directory-wide search, bounded queries, native field limits, draft retention after schema reset, hidden descendants, search cancellation, atomic saves and retries, and the dashboard, SQR history and notification query counts.

Measured locally with disposable PostgreSQL and 128 synthetic accounts against `ff89314`. The HTML sizes are uncompressed; these are not production timings.

| Measurement | Before | After |
| --- | ---: | ---: |
| Existing account rows in the initial HTML | 128 | 50 |
| Accounts HTML | 290,047 bytes | 190,978 bytes |
| Accounts GET SQL queries | 6 | 8 |
| Accounts Form Editor queries with a published schema | 0 (fields not rendered) | 2 |
| Assigned dashboard standalone User reads, 1 → 12 requests | 2 → 13 | No per-row growth |
| Backup dashboard standalone User reads, 1 → 12 requests | 3 → 25 | No per-row growth |
| SQR history table reads in the restore-policy regression | 3 | 2 |
| SQR submission notification inserts in the fixture | 5 | 1 |

| Viewport | Before | After |
| --- | --- | --- |
| Desktop | [Before](../hub/changelog_assets/pending-accounts-pagination/desktop-before.png) | [After](../hub/changelog_assets/pending-accounts-pagination/desktop-after.png) |
| Phone | [Before](../hub/changelog_assets/pending-accounts-pagination/phone-before.png) | [After](../hub/changelog_assets/pending-accounts-pagination/phone-after.png) |

The `management-accounts` recipe uses the same synthetic directory and search URL at both revisions. It captures the desktop and phone fallback without scripts and includes a technical fingerprint report for related changes. The [capture metadata](../hub/changelog_assets/pending-accounts-pagination/capture.json) records the exact baseline, working-tree fingerprints, and image checksums. Trusted CI captures the exact Git revision pair before release. In disposable PostgreSQL, a signed-in non-admin could not see the draft or its four images. After test publication, **Notifications → Changelogs** displayed the desktop comparison and linked the phone comparison; all four images matched the captures and were served with `no-store`. The test transaction was rolled back; nothing was published live.

### Unreleased: Phase 2 — view and service organization

- Split the view monolith into eight [domain modules](../hub/views/) and kept the existing [view import facade](../hub/views/__init__.py) and [URL registrations](../hub/urls.py). Centralized equivalent role checks in [mixins.py](../hub/mixins.py) without merging distinct assignee or reviewer rules.
- Moved SLA calculations, report and export builders, request-change notifications and email composition, and SQR workflow and workbook import logic into [services](../hub/services/). Removed a duplicate backup-assignee assignment. This phase adds no migrations or template changes.
- Added tests for the dashboard route, Manila business hours, reversed report months, and SQR approval metadata on hold. Compatibility tests also cover retrying an SQR filing error after rollback, using the legacy locked lookup for inline updates, and redirecting to Outlook sign-in before recording acknowledgement. The [local development guide](local-development.md#project-structure) documents the new layout.

| Technical evidence | Before | After |
| --- | --- | --- |
| View organization, services, and regression coverage | [Pre-split report](../hub/changelog_assets/pending-view-service-split/report-before.png) | [Working-tree report](../hub/changelog_assets/pending-view-service-split/report-after.png) |

The [capture metadata](../hub/changelog_assets/pending-view-service-split/capture.json) identifies the pre-split local checkout (including earlier uncommitted work) and the Phase 2 working tree. These are offline technical reports, not UI screenshots or exact release commits. Trusted CI generates exact first-parent and change reports for backend-only changes when the release revisions exist.

### Unreleased: Phase 3 — query optimization

| Change | Impact | Implementation |
| --- | --- | --- |
| Batch historical SQR assignee and linked-request labels instead of looking each ID up per row | High | [SQR workflow](../hub/services/sqr_workflow.py), [history view](../hub/views/sqr.py) |
| Calculate assignment capacity in one grouped query, join request participants, and reuse loaded lifecycle events and activity totals | High | [Request changes](../hub/services/request_changes.py), [request views](../hub/views/request_views.py), [lifecycle](../hub/services/request_lifecycle.py) |
| Reuse form choices, load account suggestions only when displayed, and reuse Form Editor metadata without losing the offline baseline render | High | [Forms](../hub/forms.py), [Form Editor](../hub/form_editor.py), [Form Editor runtime](../hub/form_editor_runtime.py), [people directory](../hub/form_editor_views.py), [SQR forms](../hub/views/sqr.py) |
| Aggregate SLA health, turnaround, and activity hours in SQL; reuse grouped counts and retrieve only the fields the attention list displays | Medium | [Reports](../hub/services/reports.py) |
| Insert ordinary edit notifications in one batch, preserving the status-log entry and deduplicated recipients | Medium | [Notifications service](../hub/services/notifications.py) |

The [regression tests](../hub/test_query_performance.py) pin ten rendered query budgets and cover scaling, recipient deduplication, and opt-in SQR history pagination. History still returns its newest 200 entries by default, with the same payload. Supply `?page=1` to opt into 50-entry pages and pagination metadata. Existing list controls, filters, URLs, and permission scopes are unchanged. The SQR Prototype still loads its full authorized pool by default for client-side filtering; its existing paged JSON is opt-in. Exports, request-detail timelines, and the full populations used for dashboard and SQR report totals are not capped. Dashboard and SQR report rollups that reuse those populations remain in Python. Event-keyed notification writes and audit-producing saves were not batched. This phase adds no migrations.

| Authenticated GET (120-row fixture) | SQL statements before | SQL statements after |
| --- | ---: | ---: |
| SQR history with relationship edits | 244 | 6 |
| Admin request management | 79 | 18 |
| Engineer collaborative detail | 34 | 19 |
| Engineer Classic SQR | 33 | 13 |
| Admin dashboard search | 25 | 14 |
| Requestor edit | 21 | 9 |
| SQR edit | 21 | 9 |
| Operational report / SLA | 20 | 12 |
| Form Editor people directory | 19 | 4 |
| Engineer activity logs | 18 | 12 |

The disposable PostgreSQL probes included template rendering at 6 and 120 synthetic rows: 36 of 122 paired measurements used fewer queries, and 86 were unchanged. Relationship-history reads stayed at 6 as the fixture grew. Adding 20 assignable engineers previously took request management from 79 to 119 queries; it now stays at 18. Matching HTTP statuses and recorded row counts do not imply byte-for-byte HTML parity or production latency. Both runs excluded the same two existing SQR approval `mailto:` failures (HTTP 400).

| Technical evidence | Before | After |
| --- | --- | --- |
| Rendered query counts and source fingerprints | [Pre-optimization report](../hub/changelog_assets/pending-query-optimization/report-before.png) | [Optimized report](../hub/changelog_assets/pending-query-optimization/report-after.png) |

The [capture metadata](../hub/changelog_assets/pending-query-optimization/capture.json) records the local source snapshots, query counts, image checksums, and disposable Notifications check. These are technical reports, not UI screenshots or exact release revisions. No live publication was performed.

### Unreleased: Phase 4 — database indexes

The [model indexes](../hub/models.py) and [migrations 0065–0071](../hub/migrations/) change how PostgreSQL finds rows, not the SQL emitted by the views, the rows returned, or notification and request permissions. No indexes were removed and no rows were rewritten. Each index targets a measured filter and ordering:

| Index | Query served | Shared block accesses, before → after | Impact |
| --- | --- | ---: | --- |
| `req_active_status_new_idx` | [Admin dashboard](../hub/services/dashboard_queries.py): non-deleted requests for one status, newest 50 by creation date (`DESC NULLS LAST`) and descending ID | Open: 2,971 → 332; completed: 2,971 → 351 | High |
| `req_ongoing_sla_idx` | [SLA report](../hub/services/reports.py): first 10 non-deleted, ongoing requests due by the cutoff, ordered by due date, priority, then reference | 2,951 → 50 | High |
| `notif_rec_read_new_idx` | [Notifications](../hub/notification_views.py): one recipient and read state, newest first. The same prefix supports marking that recipient's unread rows read | Unread: 9,860 → 58; read: 976 → 59 | High |
| `sqr_new_idx` | [Classic SQR](../hub/views/sqr.py): admin's unscoped page ordered by descending creation date and ID | 1,906 → 200 | High |
| `sqr_engineer_new_idx` | [Classic SQR](../hub/views/sqr.py): filing engineer's page with the same stable ordering | 320 → 80 | Medium |
| `sqr_reviewer_new_idx` | [Classic SQR](../hub/services/sqr_queries.py): assigned PM's page in the same order | 331 → 154 | Medium |
| `activity_eng_date_new_idx` | [Activity Logs](../hub/views/reports.py): one engineer, most recent request dates first, then descending creation time | 451 → 110 | Medium |

These are `EXPLAIN (ANALYZE, BUFFERS)` shared hits plus reads for individual SQL statements captured from authenticated requests on disposable PostgreSQL 16 with 304,004 synthetic rows. All 12 paired SQL statements and results matched (for tied keys with no SQL tie-breaker, row membership and ordered keys matched). Nine plans reduced block accesses; three were unchanged. This is not a production latency estimate or a reduction in the number of SQL statements. New indexes occupied about 15 MiB on the fixture and add maintenance cost to writes, including read-state updates. Separate primary-assignee, backup-assignee, and requestor composites were deferred: the existing foreign-key indexes served those queries, and the tested alternatives saved at most 9% of blocks without eliminating their sorts. No standalone low-selectivity status indexes or indexes for computed search/aggregate expressions were added.

Each non-atomic migration builds or drops one index concurrently. This permits ordinary reads and writes but can still wait for long-running transactions and use I/O during the build. Keep the production migration run serialized. If a build is interrupted, first check that no migration is still active, then compare `showmigrations hub` with `pg_index.indisvalid` and `pg_get_indexdef` for the affected index. If an index exists for an unapplied migration, confirm its ownership and definition against `sqlmigrate hub <number>`; in an approved maintenance window, drop only that orphaned index concurrently outside a transaction, then retry `migrate`. Do not fake migrations or remove unrelated indexes. To roll back just Phase 4 when it is the current migration tip, target `python manage.py migrate hub 0064_backfill_sqr_schedule_performance`. Forward migration, rollback, and reapplication were exercised on the same populated fixture; all four tables retained their row counts and content fingerprints.

| Technical evidence | Before | After |
| --- | --- | --- |
| PostgreSQL query plans and source fingerprints | [Pre-index report](../hub/changelog_assets/pending-database-indexes/report-before.png) | [Indexed report](../hub/changelog_assets/pending-database-indexes/report-after.png) |

The [capture metadata](../hub/changelog_assets/pending-database-indexes/capture.json) records the source fingerprints, matched plans, image checksums, and disposable Notifications check. The draft and its images were hidden from a non-admin. After local admin publication, **Notifications → Changelogs → Technical evidence** opened the report and served the exact images. The fixture was rolled back; no deployment or live publication was performed.

### Unreleased: Phase 5 — lazy badges and shared lookups

[Notification badges and the initial toast cursor](../hub/context_processors.py) now query only when consumed and share one summary per request. An optional [Redis cache](../hub/services/lookup_cache.py) stores the summary for 30 seconds per database, user, and role. [Activity and toast polling](../hub/notification_views.py) still read live data. The [department directory](../hub/assistant_directory.py) caches only active-user counts and department labels; member searches and permissions stay live. Roles, request types, and theme choices are constants or loaded with the user, so they do not need a shared cache.

[Save/delete signals](../hub/signals.py), [batched writes](../hub/services/notifications.py), mark-all-read, and [backup recovery](../accounts/recovery.py) invalidate the affected lookups after commit. Tests cover committed writes and rollbacks, recipient changes, late cache fills, live polling, and directory membership changes. [Settings](../request_hub/settings.py) enable health checks on reused PostgreSQL connections without changing the default 60-second connection lifetime. See the [shared lookup cache settings](azure-app-service-deployment.md#optional-shared-lookup-cache). The Redis client is pinned in the runtime and capture locks. Redis is not provisioned or enabled by default. If it is unavailable, reads fall back to SQL; an invalidation missed during an outage can leave a recovered entry stale for the remainder of its TTL. No URLs, templates, or migrations changed.

| Authenticated operation on the same synthetic PostgreSQL fixture | Before | After, no Redis | After, warm Redis |
| --- | ---: | ---: | ---: |
| Build the badge context without reading a badge | 1 | 0 | 0 |
| Read the badge and initial cursor | 1 | 1 | 0 |
| Read the grouped department counts | 1 | 1 | 0 |
| Render Request Edit | 9 | 8 | 8 |
| Render the admin dashboard | 13 | 13 | 12 |
| Render the Changelogs tab | 5 | 5 | 4 |
| List departments (JSON) | 3 | 3 | 2 |
| Render Activity / poll toasts / list members | 6 / 4 / 6 | 6 / 4 / 6 | 6 / 4 / 6 |

Counts are SQL statements, not production timings. Cold badge and department reads still use one statement each. The 12 probes matched on their recorded statuses and response values. Separate processes verified warm Redis reads, refresh after committed changes, and SQL fallback during a Redis outage.

| Technical evidence | Before | After |
| --- | --- | --- |
| Lookup query counts and source fingerprints | [Pre-cache report](../hub/changelog_assets/pending-shared-lookup-cache/report-before.png) | [Lazy lookups report](../hub/changelog_assets/pending-shared-lookup-cache/report-after.png) |

The [capture metadata](../hub/changelog_assets/pending-shared-lookup-cache/capture.json) records the local snapshots, query comparisons, image checksums, and disposable **Notifications → Changelogs → Technical evidence** verification. These are technical reports, not UI screenshots or exact release revisions. Nothing was published live.

### Unreleased: Phase 7 — repository cleanup

> **Archived Phase 7 results.** This section describes the reviewed Phase 7 snapshot. The PR #163 recovery retains the incident-script move and container exclusions, but does not restore the archived dependency layering or the reverted and ambiguous cleanup hunks.

Moved the incident 906 [inspection](../scripts/forensics_906.py) and [repair](../scripts/repair_906.py) scripts into `scripts/`, updated their run instructions, and kept both out of container builds. The four retired root inspection scripts were already absent, and the standalone JavaScript tests were already in `tests/js/`. No incident script was executed.

[Supplemental dependency inputs](ci-workflow-testing.md#reproducible-dependencies-and-added-validation) now constrain against the runtime manifest and, where applicable, the exact runtime lock. The tooling lock aligns `requests` to runtime (`2.34.2` → `2.33.0`); the evidence lock adds a constraint provenance comment without changing its pin. The runtime, CI, Windows, and capture locks did not change.

| Metric | Before | After |
| --- | ---: | ---: |
| Maintained Python Ruff findings | 531 | 0 |
| Whole-tree Ruff findings | 948 | 417 |
| Python lines in affected files | 41,084 | 41,646 |
| PostgreSQL probes with matching counts and recorded responses | 12 | 12 |

The remaining 417 diagnostics are unchanged: 206 in historical migrations and 211 in bundled agent tools. Runtime changes are limited to lint cleanup, typing, and documentation; no schema or query-budget changes were introduced. The SQL probes used the same synthetic fixtures with Redis disabled and compared selected response values, not raw HTML or timings.

| Technical evidence | Before | After |
| --- | --- | --- |
| Ruff findings, dependency pins, and source fingerprints | [Pre-cleanup report](../hub/changelog_assets/pending-repository-cleanup/report-before.png) | [Cleaned working-tree report](../hub/changelog_assets/pending-repository-cleanup/report-after.png) |

The [capture metadata](../hub/changelog_assets/pending-repository-cleanup/capture.json) records the local source fingerprints, probe results, image hashes, and verification through **Notifications → Changelogs → Technical evidence** on disposable PostgreSQL. The reports are not UI screenshots or exact release revisions. Nothing was published live.

### Unreleased: Phase 8 — chart loading and reduced motion

Reports, Activity Logs, both Dashboard report variants, and SQR Reports defer Chart.js and its dependent scripts in order. The page shell, Reports table details, and Dashboard account autocomplete no longer wait for the chart CDN while the page is parsing. Chart controls still initialize in order, and exact values remain available if chart rendering fails. Request Edit and Request Management also defer the shared account autocomplete script. The admin reassignment test checks that the management form loads that script once and still saves the new account; Chromium covers keyboard selection.

[Login parallax](../static/js/liquid-glass.js) cancels pending frames and resets its lighting when the system enables reduced motion. Turning that preference off resumes motion only on new pointer input. Existing app opt-outs, hidden-tab guards, and holiday motion behavior are unchanged. WhiteNoise already serves compressed manifest assets; no static files were removed and no settings or migrations changed.

| Matched local observation | Before | After |
| --- | ---: | ---: |
| Parser-blocking chart scripts on each of the five chart pages | 4 | 0 |
| Reports reached `interactive` while Chart.js was held | 0/5 | 5/5 |
| Login layout reads per pointer input (normal / reduced / resumed) | 1 / 1 / 1 | 1 / 0 / 1 |
| Authenticated SQL counts (Reports / Activity Logs / Requestor Dashboard / Engineer Dashboard report / SQR Reports / Request Edit) | 12 / 12 / 7 / 11 / 8 / 8 | 12 / 12 / 7 / 11 / 8 / 8 |

In the held-CDN Chromium probe, the candidate's median `domInteractive` was 197 ms from local navigation start. The baseline stayed in `loading` until the probe released Chart.js. This is not a production timing or a `DOMContentLoaded` comparison. Both snapshots served gzip with `Vary: Accept-Encoding`, `max-age=315360000, public, immutable` on the fingerprinted URL, and `max-age=60, public` on the unversioned URL.

| Technical evidence | Before | After |
| --- | --- | --- |
| Browser measurements, queries, cache headers, and source fingerprints | [Pre-change report](../hub/changelog_assets/pending-frontend-performance/report-before.png) | [Deferred scripts and live motion report](../hub/changelog_assets/pending-frontend-performance/report-after.png) |

The [capture metadata](../hub/changelog_assets/pending-frontend-performance/capture.json) records the local snapshots and image checksums. A reader could not fetch the draft or its images. After local admin test publication, **Notifications → Changelogs → Technical evidence** linked to both byte-verified images with `no-store`; the transaction was rolled back. These reports are not screenshots of a visual redesign or exact Git first-parent evidence. Before release, attach evidence for the actual revision pair. Nothing was published live.

### Unreleased: performance patch review corrections

[Notification deletes](../hub/signals.py) use Django's set-based delete path when shared lookups are disabled. With shared lookups enabled, commit-time badge invalidation remains in place for direct deletes and request cascades. The [view facade](../hub/views/__init__.py) again exposes the Requests library under the legacy `hub.views.requests` binding; request views and URLs still resolve through their existing exports. This earlier review snapshot kept **My Assigned** for admins when Classic Tracker was hidden. The later [PPT navigation update](unreleased-ppt-navigation-changelog.md#admin-default-filters) makes both administrator account types start unfiltered and keeps the PM-ESG default. No permissions, migrations, or stored data changed.

| Matched local observation | Before review fix | After review fix |
| --- | --- | --- |
| SQL against notifications, per 250-row queryset or request cascade | 1 SELECT + 3 DELETE | 1 DELETE |
| Legacy Requests binding | Request views module | Requests HTTP library |
| Admin Performance with Classic hidden | All Projects | My Assigned |

[Before report](../hub/changelog_assets/pending-performance-review-fixes/report-before.png) · [After report](../hub/changelog_assets/pending-performance-review-fixes/report-after.png) · [Measurements and checksums](../hub/changelog_assets/pending-performance-review-fixes/capture.json). Counts are from identical synthetic fixtures, not production timings. The reports compare local patch snapshots; trusted release CI must capture exact first-parent evidence once revisions exist.

### Preparing screenshot evidence

The non-blocking **Prepare release screenshot evidence** job runs only on trusted `master` pushes, alongside CI validation. It uses Python 3.12, installs the complete hash-verified `requirements-changelog-capture.txt` in an isolated virtual environment, and caches pip downloads, Chromium, verified vendor assets, and fingerprinted historical captures. It prepares SHA-256-checked Bootstrap assets and runs `scripts/capture_release_changelog.py`, bounded to eight minutes. Historical Chromium regression tests run in this environment too. Capture setup/failure does not turn successful required CI red; missing evidence retains every change as a draft with an actionable reason.

CI exports only the manifest, referenced images, checksums, and run/revision metadata as `changelog-evidence-SHA-ATTEMPT`, retained for 30 days. Production Release downloads from `github.event.workflow_run.id`, never from the latest unrelated run. The importer verifies run ID/attempt, revision, the complete commit set, first-parent provenance, image checksums, size limits, and safe asset destinations before copying files. A minimal hash-pinned Pillow decoder validates actual image integrity and decoding before accepting a bundle; missing decoder or malformed evidence falls back to drafts rather than losing the changelog during startup. It preserves the numbered release identity and never imports executable content or overwrites different packaged images. Missing/rejected artifacts annotate incomplete changes; manifest generation remains required. Captured assets are included in the final scanned image, so no new production database importer or firewall window is needed.

`workflow_run` still waits for the entire CI workflow, including optional capture. This overlaps capture with validation and removes browser setup from the release-image job; it is not a fully asynchronous release and does not guarantee a three-minute end-to-end saving. Compare actual CI-to-production timings after rollout. Capture dependencies, scripts, tests, and vendor caches remain excluded from the runtime image.

Recipes in `hub/changelog_capture_recipes.json` use schema 1, a unique `id`, and either a full `commit` SHA or `paths` patterns. Exact-commit recipes take precedence. A UI recipe matches any covered file, even when other files change in the same commit. Multiple UI recipes can contribute distinct scenes within the 20-pair limit. Document and behavior-report recipes still match only when they cover the entire change.

When an uncovered change touches templates, stylesheets, or browser scripts, it needs an explicit recipe or manual screenshots. Other uncovered changes can use a `repository_change` report from the exact parent and change snapshots. Reports show public paths, presence, byte counts, and fingerprints, but not file contents. Existing packaged reports remain usable as technical evidence when the UI cannot be recovered. The Notifications feed keeps reports and documentation under **Technical evidence** and screenshots under **Visual changes**. Each scene declares a bounded `title`, `before_text`, `after_text`, and optional `viewport` (`width` 320-1920; `height` 240-1600):

- `kind: "ui"` selects an allowlisted component: sign-in, seasonal sign-in, profile avatar, Admin profile tools, changelog display, editor, history preview or Notifications feed, Management Accounts or Monitoring, Request progress, or an SQR tracker/performance shell. Historical Django templates use synthetic, unsaved fixtures and a dummy database, not the deployed app. The `management-accounts` recipe compares the same Accounts search request without scripts at both revisions. The `request-progress-dark-mode` recipe captures the Christmas theme's dark-mode progress card on desktop and phone. The `admin-profile-tools` recipe selects Personalization and captures the desktop and phone defaults with scripts disabled; the Create backup control shows its inline fallback. The `changelog-loading` recipe opens native disclosures with scripts disabled to capture the server-rendered fallback in Management and the mobile Notifications feed. These screenshots document the fallback, not load times. Monitoring captures show the shell before readings load.
- The `christmas-canopy` UI recipe matches the new canopy renderer rather than the legacy light paths. It renders the same synthetic account's Themes panel and steady navbar in dark mode. Only the snapshot's theme, cord, and navbar renderers run, with normal page scripts and network blocked. The Chromium regression uses the prepared vendor directory and the capture runner's SwiftShader settings. The stills show placement, reflections, and the new controls, not motion or hardware performance. See the [Evergreen Canopy release notes](#unreleased-evergreen-canopy-christmas-lights).
- `kind: "document"` selects a repository-relative text `path` and Markdown `heading`. Code blocks and private configuration/credential paths are rejected.
- `kind: "report"` selects the allowlisted `changelog_contract` behavior report or `repository_change` file fingerprint report. Fingerprints cover safe paths changed from the first parent and can accompany a UI scene to document related backend, configuration, documentation, and test changes. Neither report calls a live provider.

All configured scenes must render successfully. A UI recipe must produce at least one visually different UI pair; pixel-identical UI pairs are omitted. A changed technical report cannot satisfy that check. Source revisions, recipes, fixture/runner implementation and pinned dependencies determine the automatic-cache fingerprint. Verified screen evidence can replace generic repository reports during CI import and database sync; older reports cannot displace screen evidence or admin uploads.

For local capture/retry (Windows, from the repository root):

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-changelog-capture.txt -r requirements-windows.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts\capture_release_changelog.py --prepare-assets
.\.venv\Scripts\python.exe -m hub.release_changelog --version vNEXT --revision HEAD
.\.venv\Scripts\python.exe scripts\capture_release_changelog.py
```

Use the actual release version in place of `vNEXT`, a full-history checkout, and the matching source revision. Capture never reads production settings or the application database. Page scripts, service workers and external traffic are disabled; only snapshot static resources and verified vendor assets are served. The seasonal sign-in fixture explicitly runs only the snapshot's local decoration script for a seeded, reduced-motion frame. The profile photo shortcut fixture runs only the historical Form Editor runtime so the comparison shows the actual label update. Do not invoke the startup sync locally against a shared database for testing. Import a completed manifest only into the intended app database after reviewing its evidence. Publication intent is stored in the existing changelog JSON; this improvement needs no additional schema migration.

Capture the actual previous and updated UI using the same synthetic fixture and viewport. Do not relabel an after-only screenshot as "before", use live personal/request data, or claim that a static screenshot proves provider calls, email delivery or animations. Include meaningful before/after descriptions for non-visual changes as well; do not invent UI screenshots for backend-only fixes.

After the UI commit exists, add a follow-up evidence commit before release. Put its assets and a curated `manifest.json` in `hub/changelog_assets/<capture-set>/`. The file uses the normal manifest structure (`version`, full `revision`, timezone-aware `generated_at`, and `commits`). For pre-release evidence, `version` may be `next` and `revision` the captured after revision; the generator matches **commit SHA**, not the capture-set version. To repair a known shipped release, use its exact version and release revision. Packaged repairs include curated `v56.4` through `v56.8` manifests; the packaged-evidence pass enriches matching existing releases rather than recreating every historical entry. See the [v56.6–v56.8 retrospective](2026-11-06-v56.6-v56.8-changelog.md#importing-the-retrospective-entries) for explicit imports, including the empty v56.7 maintenance checkpoint. Importing evidence completes an existing draft without publishing it: review the restored comparisons and explicitly publish the release in **Management → Changelogs**.

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
  "evidence_kind": "ui",
  "capture_note": "Historical UI capture with synthetic data; no external messages were sent."
}
```

Paths are relative to `hub/changelog_assets/`; absolute paths, traversal and unsupported formats are rejected. Capture revisions and notes are shown alongside the comparisons instead of implying that every pre-change snapshot came from the preceding numbered release. Replacing screenshot files in the editor clears their old capture provenance. Run `python manage.py sync_release_changelog` to import packaged evidence locally. Screenshots inherit the release's visibility: signed-in readers can view only published, non-future releases, admins can preview drafts, and responses use `no-store`.

### Unreleased: page load performance

- **Management:** Previously, Users, Accounts, Sign-In Logs and Forms rendered together. Each now renders only its own data and pane, and only Forms initializes the people directory. Users search returns just the rows, count and pager, ignores superseded responses, and keeps the previous results with a retry message on failure. Invalid user creation and account saves retain entered values and errors. Accounts keeps its bulk editor and warns before leaving with unsaved edits.
- **Changelogs:** Management release history loads full previews only when opened. Notifications loads the first visual pair and any release-wide comparison with the page, and defers other visual and technical pairs. Direct links work without scripts and return to the originating page. If a release was edited, an outdated link offers to refresh the feed at the release rather than showing a different comparison. Admin previews remain restricted, readers cannot fetch drafts or future releases, and preview responses are private and not cached.
- **Shared pages:** The navigation badge and Notifications reuse counts within each request without changing who can see Activity or toast events. Activity does not reload after an unchanged poll; it snapshots the feed before pagination so concurrent arrivals still trigger a refresh. Polling on Changelogs updates only the unread badge. The welcome animation script loads only when its post-login overlay is shown.
- **Regression coverage and evidence:** Added Django and Chromium checks for pane isolation, search recovery, Accounts edits, paginated return links, stale comparison recovery, preview visibility and caching, and login asset scoping. Notification tests cover scoped queries and refreshes. The `changelog-loading` recipe captures before and after Management history and mobile Notifications disclosures with scripts disabled. Live Chromium also loaded both sides of comparisons, checked that polling preserves open evidence, and verified that a test release became visible to a non-admin in Notifications only after an admin published it in the disposable database.

Local comparison against `b61e6ab` used the same synthetic PostgreSQL fixture and two alternating rounds of seven warm requests per route. The ranges are server-side medians, not browser navigation or production timings. Payloads are uncompressed HTML.

| Route | Median before → after | HTML before → after | SQL queries before → after |
| --- | --- | --- | --- |
| Management → Users | 924–937 ms → 208–222 ms | 1.11 MB → 337 KB | 18 → 10 |
| Management → Users search | 896–947 ms → 138–180 ms | 1.11 MB → 264 KB | 18 → 6 |
| Management → Changelogs | 125–146 ms → 41–53 ms | 381 KB → 59 KB | 8 → 7 |
| Notifications → Changelogs | 179–184 ms → 69–102 ms | 367 KB → 216 KB | 9 → 5 |

Management Sign-In Logs and Forms also served less HTML (about 176 KB and 59 KB, respectively, down from 1.11 MB each). Activity dropped from 14 queries to 6 and no longer makes an immediate duplicate page request. Dashboard, Reports, PPT, and Profile each used one fewer query; these runs did not establish a consistent speedup on those pages. At that benchmark revision, Accounts dropped to 694 KB but still rendered the full 350-row bulk formset, and its timing ranges overlapped. The [separate Accounts pagination comparison](#unreleased-accounts-dashboard-and-sqr-performance) measures the editor update.

The after screenshots are working-tree captures, not exact Git revision evidence. Run trusted CI against the actual release revision and its first parent before packaging. Verify the numbered release and both sides of its comparisons in **Notifications → Changelogs** as a non-admin after authorized publication.

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