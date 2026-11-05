#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
python scripts/workflow_test_utils.py container-security.yml 'Classify CI event' > "$workspace/classify.sh"
export RUNNER_TEMP="$workspace" GITHUB_OUTPUT="$workspace/output" GITHUB_STEP_SUMMARY="$workspace/summary"
export GITHUB_REPOSITORY=example/request-hub GITHUB_SHA=abc123 GH_TOKEN=synthetic-only
export API_LOG="$workspace/api.log"
fixture='[{"state":"open","draft":false,"head":{"ref":"dev","sha":"abc123","repo":{"full_name":"example/request-hub"}},"base":{"ref":"master","repo":{"full_name":"example/request-hub"}}}]'
gh() {
    echo "$*" >> "$API_LOG"
    [[ "$*" == *'--method GET'* && "$*" == *'state=open'* && "$*" == *'base=master'* && "$*" == *'head=example:dev'* ]] || return 70
    if [[ "$SCENARIO" == api-failure ]]; then return 1; fi
    cat "$RUNNER_TEMP/pulls.json"
}
python3() { command python "$@"; }
export -f gh python3
for scenario in master standalone open draft fork-head fork-pr reopened base-change manual empty malformed wrong-shape invalid-head closed stale-sha foreign-base api-failure; do
    export SCENARIO="$scenario" GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/dev
    printf '%s' "$fixture" > "$workspace/pulls.json"
    expected=true
    case "$scenario" in
        master) export GITHUB_REF=refs/heads/master ;;
        standalone|empty) echo '[]' > "$workspace/pulls.json" ;;
        open) expected=false ;;
        draft) expected=false; echo "${fixture/\"draft\":false/\"draft\":true}" > "$workspace/pulls.json" ;;
        fork-head) echo "${fixture/\"full_name\":\"example\/request-hub\"/\"full_name\":\"fork\/request-hub\"}" > "$workspace/pulls.json" ;;
        fork-pr|reopened|base-change) export GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/1/merge ;;
        manual) export GITHUB_EVENT_NAME=workflow_dispatch ;;
        malformed) echo invalid-json > "$workspace/pulls.json" ;;
        wrong-shape) echo '{}' > "$workspace/pulls.json" ;;
        invalid-head) echo '[{"head":null}]' > "$workspace/pulls.json" ;;
        closed) echo "${fixture/\"state\":\"open\"/\"state\":\"closed\"}" > "$workspace/pulls.json" ;;
        stale-sha) echo "${fixture/abc123/older}" > "$workspace/pulls.json" ;;
        foreign-base) echo "${fixture/\"ref\":\"master\"/\"ref\":\"other\"}" > "$workspace/pulls.json" ;;
    esac
    : > "$GITHUB_OUTPUT"; : > "$GITHUB_STEP_SUMMARY"; : > "$API_LOG"
    bash -e -o pipefail "$workspace/classify.sh" > "$workspace/log" 2>&1
    grep -Fx "run_full_ci=$expected" "$GITHUB_OUTPUT" >/dev/null
    case "$scenario" in
        master|fork-pr|reopened|base-change|manual) test ! -s "$API_LOG" ;;
        malformed|wrong-shape|invalid-head|api-failure) grep -q '::notice::' "$workspace/log" ;;
    esac
    printf 'PASS: routing %s (%s)\n' "$scenario" "$expected"
done
