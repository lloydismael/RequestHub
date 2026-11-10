#!/usr/bin/env bash
set -euo pipefail
image=${1:?Pass the exact built image}
revision=${2:?Pass the expected revision}
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full commit revision' >&2; exit 1; }
prefix="request-hub-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$-$RANDOM"
network="$prefix-network"
database="$prefix-db"
application="$prefix-app"
network_owned=false
database_owned=false
application_owned=false
cleanup() {
    local status=$?
    trap - EXIT
    if "$application_owned"; then docker rm -f "$application" >/dev/null || status=1; fi
    if "$database_owned"; then docker rm -fv "$database" >/dev/null || status=1; fi
    if "$network_owned"; then docker network rm "$network" >/dev/null || status=1; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker network create "$network" >/dev/null
network_owned=true
docker create --name "$database" --network "$network" \
    -e POSTGRES_DB=container_smoke -e POSTGRES_USER=smoke -e POSTGRES_PASSWORD=disposable-smoke-password \
    postgres:16 >/dev/null
database_owned=true
docker start "$database" >/dev/null
ready=false
for _attempt in {1..30}; do
    if docker exec "$database" pg_isready -h 127.0.0.1 -U smoke -d container_smoke >/dev/null 2>&1; then ready=true; break; fi
    sleep 1
done
"$ready" || { echo 'Disposable PostgreSQL did not become ready' >&2; exit 1; }
settings=(--network "$network" -e DJANGO_DEBUG=False \
    -e DJANGO_SECRET_KEY=ci-only-container-smoke-key-not-for-production-0123456789abcdef \
    -e 'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1' -e DJANGO_PUBLIC_BASE_URL=https://container-smoke.invalid \
    -e DB_HOST="$database" -e DB_NAME=container_smoke -e DB_USER=smoke \
    -e DB_PASSWORD=disposable-smoke-password -e DB_PORT=5432 -e DB_SSLMODE=disable \
    -e RUN_MIGRATIONS=false -e AZURE_TELEMETRY_ENABLED=false)
docker run --rm "${settings[@]}" --entrypoint python "$image" manage.py check --deploy --fail-level WARNING
docker run --rm "${settings[@]}" --entrypoint python "$image" manage.py migrate --noinput >/dev/null
docker create --name "$application" "${settings[@]}" -p 127.0.0.1::8000 "$image" >/dev/null
application_owned=true
docker start "$application" >/dev/null
port=$(docker port "$application" 8000/tcp)
[[ "$port" =~ ^127\.0\.0\.1:[0-9]+$ ]] || { echo 'Unexpected smoke-test port binding' >&2; exit 1; }
"${SMOKE_PYTHON:-python}" scripts/check_container_smoke.py --url "http://$port" --revision "$revision"
