# Enabling automatic production releases

## Current behavior

`Production Release` is defined in `.github/workflows/production-release.yml`. It listens for successful `Container Security` runs caused by pushes to `master` in this repository. It excludes development pushes, pull requests, and manually dispatched CI runs. The workflow must be on the default branch to receive `workflow_run` events.

It is disabled unless repository variable `CD_ENABLED` is exactly `true`. This is the only activation switch. Do not enable it until every prerequisite below has been reviewed. Production releases have no staging or reviewer gate, as requested; a restart or failed deployment can directly affect users.

The eligibility summary distinguishes **DISABLED** (switch unset/false) from **INELIGIBLE** (upstream CI is not a successful trusted master push). Eligible runs first execute a hosted **Check production configuration** job. Missing values fail that job with configuration names only, before image building or publishing. Configuration checks require production debug settings and database TLS but do not prove permissions or recovery readiness.

The workflow serializes releases without cancelling an active one and rejects superseded commits before publishing/deploying. CI keeps its own cancellation policy. Use branch protection to restrict changes to `master` and require all three CI jobs plus review. Restrict the GitHub `production` environment to the default `master` branch. The environment ref is the downstream workflow's default-branch ref, not the upstream merge ref.

## Numbered Docker tags

The image remains `lloydismael12/request-hub`. The workflow reads existing Docker Hub tags, chooses the highest matching numbered tag, and increments its single-digit patch: `v51.8` to `v51.9`, then `v52.0`. It refuses to allocate a release if no baseline is found. Registry tags are authoritative; local image listings are not.

The image is built for `linux/amd64`, scanned, and pushed without rebuilding. Production uses the resulting digest rather than a mutable tag. Neither `latest` nor existing numbered tags should be overwritten. All publishers, including manual release operators, must use the same release coordination: the workflow concurrency lock cannot prevent a separate manual publisher from racing it. Restrict token write access and pause manual publishing while CD runs.

README/Compose tags are not automatically committed after deployment. Use the run outputs and deployed image digest as release evidence, then update documentation through a reviewed PR when needed.

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

The helper requires the server state to be Ready, retention of at least seven days, and nonempty earliest-restore metadata. These checks establish available restoration metadata, not a recent successful backup or a rehearsed restore. A platform-owned restoration rehearsal and backward-compatible migration review remain required operational prerequisites. The old local backup executable is no longer used.

The helper obtains the runner's IPv4 address from `https://api.ipify.org`, validates its format, and creates only a start=end rule named `github-cd-RUN_ID-RUN_ATTEMPT`. It retries database connectivity for up to about one minute, requires TLS settings, runs checks/migrations, and removes that rule before updating the app. Existing firewall rules, including any allow-Azure-services rule, are not changed. Existing same-name rules cause failure rather than being taken over.

EXIT/signal traps perform cleanup, with a job-level cleanup step as fallback for owned rules. Cleanup failures fail the release. Force cancellation or runner loss can defeat cleanup; inspect rules with the `github-cd-` prefix, correlate the run and attempt, and remove only the orphaned rule after confirming no migration is active. Do not cancel a migration or delete another run's rule blindly.

Configure the Web App's `RUN_MIGRATIONS=false` only after hosted migration execution and recovery have been rehearsed. The deployment script verifies it before any migration or image update.

## Deployment sequence

1. A successful trusted `master` CI run triggers a serialized release. Check eligibility and production configuration before building or publishing.
2. Allocate an unused numbered tag; build, generate SBOM, and scan the exact image. Failed security checks prevent publishing/deployment, while generated evidence is retained for 30 days.
3. Publish the scanned image and pass its version, commit, and digest to the production job.
4. Verify current `master` and prepare the temporary protected migration configuration.
5. Authenticate to Azure using OIDC and to Docker Hub using the scoped token.
6. Confirm `sitecontainers` mode, the specified container's `isMain=true`, target port 8000, and disabled startup migrations. Classic Docker mode deliberately fails closed; verify the real production mode rather than converting it automatically.
7. Pull and pin the old image for rollback. Pull the candidate and verify its OCI revision matches the approved CI commit.
8. Check backup metadata, create owned single-IP firewall access, verify connectivity, run configuration checks/migrations using the exact image, and remove firewall access. No settings are printed or downloaded from App Service.
9. Update only the main sitecontainer's image using Azure Resource Manager, preserving its registry authentication, environment configuration, mounts, and other containers. Restart the Web App.
10. Poll `/release/`, `/readyz/`, and `/accounts/login/` with finite timeouts. Verify the expected commit and Azure image reference.

`/release/` exposes only version/revision metadata. `APP_REVISION` is baked from the build commit; remove any stale platform override before activation. `APP_VERSION` can also be overridden by existing App Settings, so the commit/digest is the authoritative verification signal; reconcile an existing version override deliberately before enabling numbered-version reporting.

`/readyz/` checks a database connection with `SELECT 1`. `/healthz/` remains database-independent. Static collection remains at runtime in this implementation, using the existing startup default; build-time static collection is not enabled. Do not set `RUN_COLLECTSTATIC=false` unless static assets have been prepared independently.

## Recovery

If image update or post-deployment verification fails, the script attempts to restore the previous image digest, restarts the app, and checks readiness. The release stays failed even if rollback succeeds. It does not reverse database migrations. Releases requiring incompatible/destructive migrations must not use this automatic path; disable CD and follow a reviewed maintenance/recovery process.

Failures during pre-update migrations stop the release without replacing the running image. Some migration operations may already have completed; inspect migration state and use the backup/migration playbook rather than blindly rerunning or reversing them. Do not cancel an in-progress migration.

Set `CD_ENABLED=false` to stop subsequent eligibility checks from admitting new releases. This does not cancel an already admitted build or deployment. There is no manual dispatch in the current release workflow; re-running old jobs is not a way to bypass successful CI or current-master guards.

## Validation before activation

- Validate the workflow with `actionlint` and the shell script with `bash -n`.
- Run focused `hub.test_release_health` tests and the full suite against disposable PostgreSQL.
- Build the image locally with a synthetic version/revision; prove startup defaults, disabled migrations, revision reporting, database readiness, and local Docker health under production HTTPS settings.
- Rehearse migration failure, image update failure, verification timeout, and rollback using mocked Azure/registry tools or a dedicated nonproduction target. Syntax checks alone do not prove rollback.
- Test successful/failed CI triggers, dev/PR exclusions, serialization, and stale-commit rejection before providing production credentials.
- Verify actual production mode, recovery readiness, migration secret, persistent uploads, and OIDC/Web App/PostgreSQL firewall permissions through approved validation.
- Keep `CD_ENABLED` unset/false until all prerequisites pass. A separately approved first activation causes a real production release; it is not a harmless dry run.

No registry push, Azure update, firewall mutation, identity creation, or activation is performed merely by adding these files.