#!/bin/bash
set -euo pipefail

for required in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZURE_POSTGRES_SERVER PRODUCTION_ENV_FILE MIGRATION_IMAGE GITHUB_RUN_ID GITHUB_RUN_ATTEMPT; do
    test -n "${!required}" || { echo "Missing $required" >&2; exit 1; }
done
test -r "$PRODUCTION_ENV_FILE"
test -z "$(find "$PRODUCTION_ENV_FILE" -perm /077 -print)"
[[ "$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+-[0-9]+$ ]] || exit 1
rule="github-cd-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
server_args=(--subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$AZURE_RESOURCE_GROUP" --server-name "$AZURE_POSTGRES_SERVER")
owned=false
cleanup() {
    local status=$?
    trap - EXIT
    if "$owned"; then
        if ! az postgres flexible-server firewall-rule delete "${server_args[@]}" --name "$rule" --yes --output none; then
            echo "::error::Temporary firewall cleanup failed: $rule. Remove this exact rule manually."
            status=1
        else
            rm -f "$PRODUCTION_ENV_FILE.firewall"
        fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

state=$(az postgres flexible-server show --subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_POSTGRES_SERVER" --query state -o tsv)
retention=$(az postgres flexible-server show --subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_POSTGRES_SERVER" --query backup.backupRetentionDays -o tsv)
restore_date=$(az postgres flexible-server show --subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_POSTGRES_SERVER" --query backup.earliestRestoreDate -o tsv)
test "$state" = Ready
[[ "$retention" =~ ^[0-9]+$ ]] && (( retention >= 7 ))
test -n "$restore_date" || { echo 'No point-in-time restore metadata available' >&2; exit 1; }

existing=$(az postgres flexible-server firewall-rule list "${server_args[@]}" --query "[?name=='$rule'].name | [0]" -o tsv)
test -z "$existing" || { echo "Rule $rule already exists; reconcile it before rerunning" >&2; exit 1; }
runner_ip=$(curl --fail --silent --show-error --max-time 15 https://api.ipify.org)
[[ "$runner_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || exit 1
IFS=. read -r first second third fourth <<< "$runner_ip"
for octet in "$first" "$second" "$third" "$fourth"; do
    (( 10#$octet <= 255 )) || exit 1
done
(( 10#$first != 0 && 10#$first != 10 && 10#$first != 127 && 10#$first < 224 )) || exit 1
if (( (10#$first == 169 && 10#$second == 254) || (10#$first == 172 && 10#$second >= 16 && 10#$second <= 31) || (10#$first == 192 && 10#$second == 168) || (10#$first == 100 && 10#$second >= 64 && 10#$second <= 127) )); then
    echo 'Runner address must be a public IPv4 address' >&2
    exit 1
fi
owned=true
touch "$PRODUCTION_ENV_FILE.firewall"
az postgres flexible-server firewall-rule create "${server_args[@]}" --name "$rule" --start-ip-address "$runner_ip" --end-ip-address "$runner_ip" --output none
connected=false
for attempt in {1..12}; do
    if docker run --rm --env-file "$PRODUCTION_ENV_FILE" -e DJANGO_SETTINGS_MODULE=request_hub.settings --entrypoint python "$MIGRATION_IMAGE" -c '
import sys
try:
    import django
    django.setup()
    from django.db import connection
    connection.settings_dict.setdefault("OPTIONS", {})["connect_timeout"] = 5
    connection.ensure_connection()
except Exception as error:
    message = str(error).lower()
    category = "database configuration or connection failure"
    if "password authentication failed" in message:
        category = "database username/password rejected"
    elif "does not exist" in message and "database" in message:
        category = "configured database does not exist"
    elif "translate host name" in message or "name or service not known" in message:
        category = "database hostname cannot be resolved"
    elif "timeout" in message or "timed out" in message or "no route" in message:
        category = "network timeout; check hostname, port, and runner firewall access"
    elif "ssl" in message or "certificate" in message:
        category = "database TLS negotiation or certificate failure"
    print("Database probe: " + category, file=sys.stderr)
    sys.exit(1)
'; then
        connected=true
        break
    fi
    sleep 5
done
"$connected" || { echo 'TLS database connectivity did not become available' >&2; exit 1; }
docker run --rm --env-file "$PRODUCTION_ENV_FILE" --entrypoint python "$MIGRATION_IMAGE" manage.py check --deploy --fail-level WARNING
docker run --rm --env-file "$PRODUCTION_ENV_FILE" --entrypoint python "$MIGRATION_IMAGE" manage.py migrate --noinput