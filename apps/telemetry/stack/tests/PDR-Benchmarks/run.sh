#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
export PDR_DIR="$repo/apps/telemetry/stack/tests/PDR-Benchmarks"
export PDR_BASE_COMPOSE="$repo/apps/telemetry/stack/tests/query-gateway.compose.yml"
export PDR_COMPOSE="$PDR_DIR/compose.yml"
cd "$repo"
mode="${1:-reads}"
if [[ $# -gt 0 ]]; then shift; fi
case "$mode" in reads|ingest|recent) ;; *) echo 'Usage: run.sh reads|ingest|recent [benchmark arguments]' >&2; exit 2 ;; esac
flags=()
if [[ "${QUERY_TEST_LOCAL:-0}" == 1 ]]; then flags+=(--config=local); fi
bazel build "${flags[@]}" //apps/telemetry:new_car_schema_sql
for target in //apps/telemetry:postgres_load //apps/telemetry:kafka_load //apps/telemetry/stack/processors/db_writer:db_writer_load //apps/telemetry/stack/kafka:query_gateway_load; do
    bazel run "${flags[@]}" "$target"
done
if [[ "$mode" == ingest ]]; then
    bazel run "${flags[@]}" //apps/telemetry/stack/kafka:kafka_bridge_load
    bazel run "${flags[@]}" //apps/telemetry/stack/tests:pdr_probe_load
fi
export PDR_STATE_DIR="${PDR_STATE_DIR:-$(mktemp -d /tmp/lhre-pdr-state.XXXXXX)}"
export ARTEMIS_DB_INIT_SQL="$repo/bazel-bin/lib/codegen/telemetry/artemis_db_init.sql"
compose=(docker compose -p pdr-benchmarks -f "$PDR_BASE_COMPOSE" -f "$PDR_COMPOSE")
# Never attach this runner to the ordinary telemetry project.
if [[ -n "$("${compose[@]}" ps -aq)" ]]; then
    echo 'pdr-benchmarks already exists; inspect it before running a fresh benchmark.' >&2
    exit 1
fi
export PDR_LOG_FILE="${PDR_LOG_FILE:-/tmp/lhre-pdr-${mode}-$(date -u +%Y%m%dT%H%M%SZ)-$$.log}"
cleanup() {
    "${compose[@]}" --profile current --profile legacy logs --no-color > "$PDR_LOG_FILE" 2>&1 || true
    echo "Container log: $PDR_LOG_FILE"
    "${compose[@]}" --profile current --profile legacy down -v --remove-orphans
}
trap cleanup EXIT
"${compose[@]}" up -d --wait --wait-timeout 180 db postgres kafka db_writer query_gateway mosquitto
bazel run "${flags[@]}" "//apps/telemetry/stack/tests:pdr_${mode}" -- "$@"
