#!/bin/bash
set -euo pipefail

for required in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZURE_WEBAPP_NAME AZURE_MAIN_CONTAINER AZURE_POSTGRES_SERVER PRODUCTION_ENV_FILE PRODUCTION_URL RELEASE_SHA RELEASE_VERSION RELEASE_DIGEST; do
    test -n "${!required}" || { echo "Missing $required" >&2; exit 1; }
done
[[ "$PRODUCTION_URL" == https://* ]] || exit 1
[[ "$RELEASE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 1
[[ "$RELEASE_VERSION" =~ ^v[0-9]+\.[0-9]$ ]] || exit 1
test "$(gh api "repos/$GITHUB_REPOSITORY/commits/master" --jq .sha)" = "$RELEASE_SHA" || { echo '::error::Release commit is no longer current master'; exit 1; }

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
image="lloydismael12/request-hub@$RELEASE_DIGEST"
docker pull "$image"
test "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$RELEASE_SHA"

export MIGRATION_IMAGE="$image"
bash "$(dirname "$0")/migrate-production.sh"
test "$(gh api "repos/$GITHUB_REPOSITORY/commits/master" --jq .sha)" = "$RELEASE_SHA"

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
trap restore_previous EXIT
restore_required=true
az resource update --ids "$container" --api-version 2025-03-01 --set "properties.image=$image" --output none
az webapp restart --name "$AZURE_WEBAPP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --output none

python3 - <<'PY'
import json
import os
import time
import urllib.request

base = os.environ['PRODUCTION_URL'].rstrip('/')
for attempt in range(60):
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
    time.sleep(10)
else:
    raise SystemExit('Production verification timed out')
PY
test "$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.image -o tsv)" = "$image"
restore_required=false
printf 'Released %s (%s)\n' "$RELEASE_VERSION" "$RELEASE_SHA"