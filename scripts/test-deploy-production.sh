#!/bin/bash
set -euo pipefail

export AZURE_SUBSCRIPTION_ID=test-subscription AZURE_RESOURCE_GROUP=test-group
export AZURE_WEBAPP_NAME=test-app AZURE_MAIN_CONTAINER=main
export PRODUCTION_URL=https://production.invalid
export RELEASE_SHA=0123456789012345678901234567890123456789
export RELEASE_VERSION=v52.0 GITHUB_REPOSITORY=test/repository
export AZURE_POSTGRES_SERVER=test-postgres GITHUB_RUN_ID=123 GITHUB_RUN_ATTEMPT=1
export RELEASE_DIGEST="sha256:$(printf 'a%.0s' {1..64})"
export OLD_IMAGE="lloydismael12/request-hub@sha256:$(printf 'b%.0s' {1..64})"
export PREVIOUS_SHA=$(printf 'c%.0s' {1..40})
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
export MOCK_LOG="$workspace/commands" MOCK_IMAGE="$workspace/image"
export PRODUCTION_ENV_FILE="$workspace/production.env"
touch "$PRODUCTION_ENV_FILE"
chmod 600 "$PRODUCTION_ENV_FILE"

gh() {
    echo "gh $*" >> "$MOCK_LOG"
    if [[ "$SCENARIO" == stale ]]; then echo outdated; else echo "$RELEASE_SHA"; fi
}

az() {
    echo "az $*" >> "$MOCK_LOG"
    if [[ "$*" == *'--rule-name'* ]]; then
        echo 'Unsupported firewall argument --rule-name' >&2
        return 2
    fi
    case "$*" in
        *'firewall-rule list'*) ;;
        *'firewall-rule create'*)
            [[ "$*" == *'--start-ip-address 203.0.113.10 --end-ip-address 203.0.113.10'* ]] || return 1 ;;
        *'firewall-rule delete'*) [[ "$SCENARIO" != cleanup-failure ]] ;;
        *'--query state'*) echo Ready ;;
        *backup.backupRetentionDays*) echo 7 ;;
        *backup.earliestRestoreDate*)
            if [[ "$SCENARIO" != missing-backup ]]; then echo 2026-10-14T00:00:00Z; fi ;;
        *properties.linuxFxVersion*)
            if [[ "$SCENARIO" == classic ]]; then echo 'DOCKER|old'; else echo sitecontainers; fi ;;
        *properties.isMain*) echo true ;;
        *properties.targetPort*) echo 8000 ;;
        *appsettings*) echo false ;;
        'resource update'*)
            local argument
            for argument in "$@"; do
                if [[ "$argument" == properties.image=* ]]; then
                    printf '%s' "${argument#properties.image=}" > "$MOCK_IMAGE"
                fi
            done ;;
        *properties.image*) cat "$MOCK_IMAGE" ;;
    esac
}

docker() {
    echo "docker $*" >> "$MOCK_LOG"
    case "$*" in
        *"$OLD_IMAGE"*org.opencontainers.image.revision*)
            if [[ "$SCENARIO" != unknown-revision ]]; then echo "$PREVIOUS_SHA"; fi ;;
        *org.opencontainers.image.revision*) echo "$RELEASE_SHA" ;;
        *'range .RepoDigests'*)
            if [[ "$SCENARIO" == tag-mismatch ]]; then echo "$OLD_IMAGE"; else echo "lloydismael12/request-hub@$RELEASE_DIGEST"; fi ;;
        *RepoDigests*) echo "$OLD_IMAGE" ;;
        *'manage.py migrate'*) [[ "$SCENARIO" != migration-failure ]] ;;
    esac
}

python3() {
    cat >/dev/null
    echo 'mock verification' >> "$MOCK_LOG"
    [[ "$SCENARIO" != verification-failure ]]
}

curl() {
    echo "curl $*" >> "$MOCK_LOG"
    if [[ "$*" == *api.ipify.org* ]]; then echo 203.0.113.10; return 0; fi
    [[ "$SCENARIO" != rollback-unhealthy ]]
}

git() {
    echo "git $*" >> "$MOCK_LOG"
    case "$*" in
        cat-file*) ;;
        merge-base*) [[ "$SCENARIO" != diverged ]] ;;
        diff*) if [[ "$SCENARIO" != no-migrations ]]; then echo hub/migrations/0099_example.py; fi ;;
    esac
}

export -f gh az docker python3 curl git
script_dir=$(cd "$(dirname "$0")" && pwd)
for scenario in success no-migrations unknown-revision diverged stale classic tag-mismatch missing-backup migration-failure cleanup-failure verification-failure rollback-unhealthy; do
    export SCENARIO="$scenario"
    : > "$MOCK_LOG"
    rm -f "$PRODUCTION_ENV_FILE.firewall"
    printf '%s' "$OLD_IMAGE" > "$MOCK_IMAGE"
    if [[ "$scenario" == rollback-unhealthy ]]; then
        python3() { cat >/dev/null; return 1; }
        export -f python3
    fi
    exit_status=0
    bash "$script_dir/deploy-production.sh" > "$workspace/output" 2>&1 || exit_status=$?
    case "$scenario" in
        success|unknown-revision|diverged)
            test "$exit_status" = 0
            test "$(cat "$MOCK_IMAGE")" = "lloydismael12/request-hub:$RELEASE_VERSION"
            grep -q 'manage.py migrate --noinput' "$MOCK_LOG"
            grep -q -- "--entrypoint python lloydismael12/request-hub@$RELEASE_DIGEST manage.py migrate" "$MOCK_LOG"
            test "$(grep -c 'az resource update' "$MOCK_LOG")" = 1
            # The firewall rule is removed only after migrations have finished.
            test "$(grep -n 'manage.py migrate' "$MOCK_LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n 'firewall-rule delete' "$MOCK_LOG" | head -1 | cut -d: -f1)" ;;
        no-migrations)
            test "$exit_status" = 0
            test "$(cat "$MOCK_IMAGE")" = "lloydismael12/request-hub:$RELEASE_VERSION"
            test "$(grep -c 'az resource update' "$MOCK_LOG")" = 1
            grep -q "git diff .*$PREVIOUS_SHA $RELEASE_SHA" "$MOCK_LOG"
            grep -q 'skipping the migration window' "$workspace/output"
            ! grep -q 'manage.py' "$MOCK_LOG"
            ! grep -q 'firewall-rule' "$MOCK_LOG"
            ! grep -q 'flexible-server' "$MOCK_LOG" ;;
        cleanup-failure)
            # A failed background cleanup no longer blocks the image update, but the
            # release must still fail and name the rule to remove.
            test "$exit_status" != 0
            test "$(cat "$MOCK_IMAGE")" = "lloydismael12/request-hub:$RELEASE_VERSION"
            test "$(grep -c 'az resource update' "$MOCK_LOG")" = 1
            grep -q 'Temporary firewall cleanup failed: github-cd-123-1' "$workspace/output"
            test -f "$PRODUCTION_ENV_FILE.firewall" ;;
        stale|classic|tag-mismatch|missing-backup|migration-failure)
            test "$exit_status" != 0
            test "$(cat "$MOCK_IMAGE")" = "$OLD_IMAGE"
            ! grep -q 'az resource update' "$MOCK_LOG" ;;
        verification-failure|rollback-unhealthy)
            test "$exit_status" != 0
            test "$(cat "$MOCK_IMAGE")" = "$OLD_IMAGE"
            test "$(grep -c 'az resource update' "$MOCK_LOG")" = 2
            grep -q 'curl .*readyz/' "$MOCK_LOG"
            if [[ "$scenario" == rollback-unhealthy ]]; then
                grep -q 'operator recovery is required' "$workspace/output"
            fi ;;
    esac
    if [[ "$scenario" != stale && "$scenario" != classic && "$scenario" != tag-mismatch && "$scenario" != missing-backup && "$scenario" != no-migrations ]]; then
        grep -q 'firewall-rule create .*--name github-cd-123-1' "$MOCK_LOG"
        grep -q 'firewall-rule delete .*--name github-cd-123-1' "$MOCK_LOG"
    fi
    if [[ "$scenario" != cleanup-failure ]]; then
        test ! -e "$PRODUCTION_ENV_FILE.firewall"
    fi
    printf 'PASS: %s\n' "$scenario"
done