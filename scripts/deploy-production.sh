#!/bin/bash
set -euo pipefail

for required in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZURE_WEBAPP_NAME AZURE_MAIN_CONTAINER AZURE_POSTGRES_SERVER PRODUCTION_ENV_FILE PRODUCTION_URL RELEASE_SHA RELEASE_VERSION RELEASE_DIGEST; do
    test -n "${!required}" || { echo "Missing $required" >&2; exit 1; }
done
[[ "$PRODUCTION_URL" == https://* ]] || exit 1
[[ "$RELEASE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 1
[[ "$RELEASE_VERSION" =~ ^v[0-9]+\.[0-9]$ ]] || exit 1
test "$(gh api "repos/$GITHUB_REPOSITORY/commits/master" --jq .sha)" = "$RELEASE_SHA" || { echo '::error::Release commit is no longer current master'; exit 1; }

phase_start=$SECONDS
timing() {
    printf '[timing] %s: %ss\n' "$1" "$((SECONDS - phase_start))"
    phase_start=$SECONDS
}

image="lloydismael12/request-hub@$RELEASE_DIGEST"
deployment_image="lloydismael12/request-hub:$RELEASE_VERSION"
# Start downloading the release image while the Azure checks below run.
docker pull --quiet "$image" >/dev/null &
image_pull=$!

resource="/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.Web/sites/$AZURE_WEBAPP_NAME"
mode=$(az resource show --ids "$resource/config/web" --api-version 2025-03-01 --query properties.linuxFxVersion -o tsv)
test "$mode" = sitecontainers || { echo 'Only confirmed sitecontainers mode is supported' >&2; exit 1; }
container="$resource/sitecontainers/$AZURE_MAIN_CONTAINER"
test "$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.isMain -o tsv)" = true || { echo '::error::Configured sitecontainer is not the main container'; exit 1; }
test "$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.targetPort -o tsv)" = 8000 || { echo '::error::Main container targetPort must be 8000'; exit 1; }
migration_setting=$(az webapp config appsettings list --name "$AZURE_WEBAPP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query "[?name=='RUN_MIGRATIONS'].value | [0]" -o tsv)
test "$migration_setting" = false || { echo "::error::Web App RUN_MIGRATIONS must be false; current value: ${migration_setting:-unset}. Configure controlled migration execution before changing this setting."; exit 1; }
previous=$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.image -o tsv)
test -n "$previous" || { echo '::error::No previous image configured for rollback'; exit 1; }
docker pull "$previous"
previous=$(docker image inspect "$previous" --format '{{index .RepoDigests 0}}')
test -n "$previous"
image_pull_status=0
wait "$image_pull" || image_pull_status=$?
test "$image_pull_status" = 0 || { echo "::error::Release image pull failed (exit $image_pull_status)" >&2; exit 1; }
timing 'Azure checks and image pulls'
test "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$RELEASE_SHA"
verify_release_tag() {
    docker pull "$deployment_image"
    docker image inspect "$deployment_image" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -Fx "$image" >/dev/null || {
        echo '::error::Release tag does not resolve to the scanned image digest; refusing deployment'
        return 1
    }
}
verify_release_tag

# The database only needs the migration window (firewall, backup gate, migrate) when
# schema-affecting files changed since the currently deployed revision. Every
# uncertainty (missing label, unknown ancestry, git failure) selects the full window.
database_changes_possible() {
    local revision=$1 changed
    if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Deployed revision is unknown ('${revision:-none}'); running the full migration window."
        return 0
    fi
    if ! git cat-file -e "$revision^{commit}" 2>/dev/null || ! git merge-base --is-ancestor "$revision" "$RELEASE_SHA" 2>/dev/null; then
        echo "Deployed revision $revision is not an ancestor of the release; running the full migration window."
        return 0
    fi
    if ! changed=$(git diff --no-renames --name-only "$revision" "$RELEASE_SHA" -- ':(glob)**/migrations/*.py' requirements.txt requirements.lock ':(glob)request_hub/settings*.py'); then
        echo 'Could not compare revisions; running the full migration window.'
        return 0
    fi
    if [ -n "$changed" ]; then
        printf 'Database-affecting files changed since %s:\n%s\n' "$revision" "$changed"
        return 0
    fi
    return 1
}

restore_required=false
restore_previous() {
    if "$restore_required"; then
        echo 'Deployment failed; restoring previous image. Database migrations are not reversed.' >&2
        az resource update --ids "$container" --api-version 2025-03-01 --set "properties.image=$previous" --output none
        az webapp restart --name "$AZURE_WEBAPP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --output none
        if ! curl --fail --silent --show-error --retry 20 --retry-delay 5 --retry-all-errors --max-time 10 "$PRODUCTION_URL/readyz/" >/dev/null; then
            echo 'Rollback readiness failed; operator recovery is required' >&2
        fi
    fi
}

# After migrations succeed, the temporary firewall rule is deleted in the background
# while the app restarts. The release cannot succeed until the deletion is confirmed.
firewall_rule="github-cd-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
firewall_cleanup_pid=
finish_firewall_cleanup() {
    local pid=$firewall_cleanup_pid wait_start=$SECONDS
    test -n "$pid" || return 0
    firewall_cleanup_pid=
    if wait "$pid"; then
        rm -f "$PRODUCTION_ENV_FILE.firewall"
        printf '[timing] Waited for firewall rule delete: %ss\n' "$((SECONDS - wait_start))"
    else
        echo "::error::Temporary firewall cleanup failed: $firewall_rule. Remove this exact rule manually."
        return 1
    fi
}
on_exit() {
    local status=$?
    trap - EXIT
    set +e
    restore_previous
    finish_firewall_cleanup || status=1
    exit "$status"
}
trap on_exit EXIT

migration_window=skipped
previous_revision=$(docker image inspect "$previous" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)
if database_changes_possible "$previous_revision"; then
    migration_window=executed
    export MIGRATION_IMAGE="$image" MIGRATION_DEFER_FIREWALL_CLEANUP=true
    bash "$(dirname "$0")/migrate-production.sh"
    timing 'Database migration window (firewall, probe, check, migrate)'
    if [ -f "$PRODUCTION_ENV_FILE.firewall" ]; then
        az postgres flexible-server firewall-rule delete --subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$AZURE_RESOURCE_GROUP" --server-name "$AZURE_POSTGRES_SERVER" --name "$firewall_rule" --yes --output none &
        firewall_cleanup_pid=$!
    fi
else
    echo "No database-affecting changes between $previous_revision and $RELEASE_SHA; skipping the migration window."
    timing 'Database migration window skipped'
fi
test "$(gh api "repos/$GITHUB_REPOSITORY/commits/master" --jq .sha)" = "$RELEASE_SHA"
verify_release_tag

restore_required=true
az resource update --ids "$container" --api-version 2025-03-01 --set "properties.image=$deployment_image" --output none
az webapp restart --name "$AZURE_WEBAPP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --output none
timing 'Image update and restart'

python3 - <<'PY'
import json
import os
import time
import urllib.request

base = os.environ['PRODUCTION_URL'].rstrip('/')
for attempt in range(120):
    try:
        with urllib.request.urlopen(base + '/release/', timeout=10) as response:
            release = json.load(response)
        with urllib.request.urlopen(base + '/readyz/', timeout=10) as response:
            ready = json.load(response)
        with urllib.request.urlopen(base + '/accounts/login/', timeout=10) as response:
            login_ok = response.status == 200
        if release['revision'] == os.environ['RELEASE_SHA'] and ready['ready'] and login_ok:
            print('Production revision, database readiness, and login verified')
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(5)
else:
    raise SystemExit('Production verification timed out')
PY
timing 'Production verification'
test "$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.image -o tsv)" = "$deployment_image"
restore_required=false
finish_firewall_cleanup
printf 'Released %s (%s)\n' "$RELEASE_VERSION" "$RELEASE_SHA"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '## Production release\n\n- Image: `%s`\n- Verified digest: `%s`\n- Commit: `%s`\n- Previous rollback image: `%s`\n- Database migration window: %s (deployed revision: `%s`)\n' "$deployment_image" "$RELEASE_DIGEST" "$RELEASE_SHA" "$previous" "$migration_window" "${previous_revision:-unknown}" >> "$GITHUB_STEP_SUMMARY"
fi