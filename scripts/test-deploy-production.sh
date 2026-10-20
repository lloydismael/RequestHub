#!/bin/bash
set -euo pipefail

export AZURE_SUBSCRIPTION_ID=test-subscription AZURE_RESOURCE_GROUP=test-group
export AZURE_WEBAPP_NAME=test-app AZURE_MAIN_CONTAINER=main
export PRODUCTION_URL=https://production.invalid
export RELEASE_SHA=0123456789012345678901234567890123456789
export RELEASE_VERSION=v52.0 GITHUB_REPOSITORY=test/repository
export RELEASE_DIGEST="sha256:$(printf 'a%.0s' {1..64})"
export OLD_IMAGE="lloydismael12/request-hub@sha256:$(printf 'b%.0s' {1..64})"
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
export MOCK_LOG="$workspace/commands" MOCK_IMAGE="$workspace/image"

gh() {
    echo "gh $*" >> "$MOCK_LOG"
    if [[ "$SCENARIO" == stale ]]; then echo outdated; else echo "$RELEASE_SHA"; fi
}

az() {
    echo "az $*" >> "$MOCK_LOG"
    case "$*" in
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
        *org.opencontainers.image.revision*) echo "$RELEASE_SHA" ;;
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
    [[ "$SCENARIO" != rollback-unhealthy ]]
}

export -f gh az docker python3 curl
script_dir=$(cd "$(dirname "$0")" && pwd)
for scenario in success stale classic migration-failure verification-failure rollback-unhealthy; do
    export SCENARIO="$scenario"
    : > "$MOCK_LOG"
    printf '%s' "$OLD_IMAGE" > "$MOCK_IMAGE"
    if [[ "$scenario" == rollback-unhealthy ]]; then
        python3() { cat >/dev/null; return 1; }
        export -f python3
    fi
    exit_status=0
    bash "$script_dir/deploy-production.sh" > "$workspace/output" 2>&1 || exit_status=$?
    case "$scenario" in
        success)
            test "$exit_status" = 0
            test "$(cat "$MOCK_IMAGE")" = "lloydismael12/request-hub@$RELEASE_DIGEST"
            grep -q 'manage.py migrate --noinput' "$MOCK_LOG"
            test "$(grep -c 'az resource update' "$MOCK_LOG")" = 1 ;;
        stale|classic|migration-failure)
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
    printf 'PASS: %s\n' "$scenario"
done