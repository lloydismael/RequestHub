#!/bin/bash
set -euo pipefail

for required in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZURE_WEBAPP_NAME AZURE_MAIN_CONTAINER PRODUCTION_URL RELEASE_SHA RELEASE_VERSION RELEASE_DIGEST; do
    test -n "${!required}" || { echo "Missing $required" >&2; exit 1; }
done
[[ "$PRODUCTION_URL" == https://* ]] || exit 1
[[ "$RELEASE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 1
[[ "$RELEASE_VERSION" =~ ^v[0-9]+\.[0-9]$ ]] || exit 1
test "$(gh api "repos/$GITHUB_REPOSITORY/commits/master" --jq .sha)" = "$RELEASE_SHA"

resource="/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$AZURE_RESOURCE_GROUP/providers/Microsoft.Web/sites/$AZURE_WEBAPP_NAME"
mode=$(az resource show --ids "$resource/config/web" --api-version 2025-03-01 --query properties.linuxFxVersion -o tsv)
test "$mode" = sitecontainers || { echo 'Only confirmed sitecontainers mode is supported' >&2; exit 1; }
container="$resource/sitecontainers/$AZURE_MAIN_CONTAINER"
test "$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.isMain -o tsv)" = true
test "$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.targetPort -o tsv)" = 8000
test "$(az webapp config appsettings list --name "$AZURE_WEBAPP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query "[?name=='RUN_MIGRATIONS'].value | [0]" -o tsv)" = false
previous=$(az resource show --ids "$container" --api-version 2025-03-01 --query properties.image -o tsv)
test -n "$previous"
docker pull "$previous"
previous=$(docker image inspect "$previous" --format '{{index .RepoDigests 0}}')
test -n "$previous"
image="lloydismael12/request-hub@$RELEASE_DIGEST"
docker pull "$image"
test "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$RELEASE_SHA"

docker run --rm --env-file /opt/request-hub/production.env --entrypoint python "$image" manage.py check --deploy --fail-level WARNING
docker run --rm --env-file /opt/request-hub/production.env --entrypoint python "$image" manage.py migrate --noinput
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