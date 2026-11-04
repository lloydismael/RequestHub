#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
python scripts/workflow_test_utils.py production-release.yml 'Explain release eligibility' > "$workspace/eligibility.sh"
python scripts/workflow_test_utils.py production-release.yml 'Validate required configuration without exposing values' > "$workspace/configuration.sh"
export GITHUB_OUTPUT="$workspace/output" GITHUB_STEP_SUMMARY="$workspace/summary"
export GITHUB_REPOSITORY=example/request-hub
python3() { command python "$@"; }
export -f python3
migration_settings=$'DJANGO_DEBUG=False\nDJANGO_SECRET_KEY=SYNTHETIC_SECRET_123\nDB_HOST=database.invalid\nDB_NAME=ci\nDB_USER=ci\nDB_PASSWORD=SYNTHETIC_PASSWORD_456\nDB_PORT=5432\nDB_SSLMODE=require'
for scenario in disabled failed-ci cancelled-ci pr manual foreign-repo wrong-branch missing-config bad-origin debug no-tls bad-env missing-db rootcert default-password valid; do
    export CD_ENABLED=true CI_CONCLUSION=success CI_EVENT=push CI_BRANCH=master CI_REPOSITORY="$GITHUB_REPOSITORY"
    export AZURE_CLIENT_ID=test AZURE_TENANT_ID=test AZURE_SUBSCRIPTION_ID=test
    export AZURE_RESOURCE_GROUP=test AZURE_WEBAPP_NAME=test AZURE_MAIN_CONTAINER=main AZURE_POSTGRES_SERVER=test
    export DOCKERHUB_USERNAME=test DOCKERHUB_TOKEN=SYNTHETIC_TOKEN_789
    export PRODUCTION_URL=https://production.invalid MIGRATION_ENV_FILE="$migration_settings"
    expected=valid
    case "$scenario" in
        disabled) CD_ENABLED=false; expected=ineligible ;;
        failed-ci) CI_CONCLUSION=failure; expected=ineligible ;;
        cancelled-ci) CI_CONCLUSION=cancelled; expected=ineligible ;;
        pr) CI_EVENT=pull_request; expected=ineligible ;;
        manual) CI_EVENT=workflow_dispatch; expected=ineligible ;;
        foreign-repo) CI_REPOSITORY=foreign/request-hub; expected=ineligible ;;
        wrong-branch) CI_BRANCH=dev; expected=ineligible ;;
        missing-config) AZURE_MAIN_CONTAINER=''; expected=invalid ;;
        bad-origin) PRODUCTION_URL=http://production.invalid/; expected=invalid ;;
        debug) MIGRATION_ENV_FILE="${migration_settings/False/True}"; expected=invalid ;;
        no-tls) MIGRATION_ENV_FILE="${migration_settings/require/disable}"; expected=invalid ;;
        bad-env) MIGRATION_ENV_FILE=not-an-env-line; expected=invalid ;;
        missing-db) MIGRATION_ENV_FILE=$'DJANGO_DEBUG=False\nDB_SSLMODE=require'; expected=invalid ;;
        rootcert) MIGRATION_ENV_FILE+=$'\nDB_SSLROOTCERT=/private/cert'; expected=invalid ;;
        default-password) MIGRATION_ENV_FILE+=$'\nDJANGO_DEFAULT_USER_PASSWORD=SYNTHETIC_PASSWORD_456'; expected=invalid ;;
    esac
    : > "$GITHUB_OUTPUT"; : > "$GITHUB_STEP_SUMMARY"
    bash -e -o pipefail "$workspace/eligibility.sh" > "$workspace/log" 2>&1
    if [[ "$expected" == ineligible ]]; then
        grep -Fx 'eligible=false' "$GITHUB_OUTPUT" >/dev/null
        ! grep -q 'ready=true' "$GITHUB_OUTPUT"
    else
        grep -Fx 'eligible=true' "$GITHUB_OUTPUT" >/dev/null
        ! grep -q 'ready=true' "$GITHUB_OUTPUT"
        status=0
        bash -e -o pipefail "$workspace/configuration.sh" >> "$workspace/log" 2>&1 || status=$?
        if [[ "$expected" == valid ]]; then
            test "$status" = 0
            grep -Fx 'ready=true' "$GITHUB_OUTPUT" >/dev/null
        else
            test "$status" != 0
            ! grep -q 'ready=true' "$GITHUB_OUTPUT"
        fi
    fi
    ! grep -E 'SYNTHETIC_(SECRET_123|PASSWORD_456|TOKEN_789)' "$workspace/log" "$GITHUB_STEP_SUMMARY"
    printf 'PASS: preflight %s (%s)\n' "$scenario" "$expected"
done
