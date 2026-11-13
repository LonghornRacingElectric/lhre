#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$repo"
bazel_flags=()
if [[ "${QUERY_TEST_LOCAL:-0}" == 1 ]]; then bazel_flags+=(--config=local); fi
bazel build "${bazel_flags[@]}" //apps/telemetry:new_car_schema_sql
for target in //apps/telemetry:postgres_load //apps/telemetry:kafka_load //apps/telemetry:mqtt_load //apps/telemetry/stack/ingest:ingest_load //apps/telemetry/stack/kafka:kafka_bridge_load //apps/telemetry/stack/processors/db_writer:db_writer_load //apps/telemetry/stack/kafka:query_gateway_load //apps/telemetry:grafana_load; do
    bazel run "${bazel_flags[@]}" "$target"
done
export ARTEMIS_DB_INIT_SQL="$repo/bazel-bin/lib/codegen/telemetry/artemis_db_init.sql"
export QUERY_TEST_COMPOSE="$repo/apps/telemetry/stack/tests/query-gateway.compose.yml"
export DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}"
compose=(docker compose -p query-gateway-test -f "$QUERY_TEST_COMPOSE")
benchmark=0
demo=0
if [[ "${1:-}" == --demo ]]; then demo=1; shift; fi
if [[ "${1:-}" == --benchmark || "${1:-}" == --compare ]]; then
    benchmark=1
    if [[ "$1" == --compare ]]; then benchmark=2; fi
    shift
    compose+=(-f "$repo/apps/telemetry/stack/tests/query-gateway.benchmark.yml")
fi
cleanup() { "${compose[@]}" logs --tail=60 > /tmp/lhre-query-gateway-test.log; "${compose[@]}" down -v --remove-orphans; }
trap cleanup EXIT
"${compose[@]}" up -d --wait --wait-timeout 150
if [[ "$demo" == 1 ]]; then
    echo "Grafana demo: http://localhost:33000/d/artemis-demo (admin / gateway-test-password); publishing for 60 seconds."
    QUERY_GATEWAY_URL=http://127.0.0.1:38090 QUERY_API_TOKEN=gateway-test-token \
        bazel run "${bazel_flags[@]}" //apps/telemetry/analysis:validate_live_viewer -- --mqtt-port 31883 --duration 60
elif [[ "$benchmark" == 2 ]]; then
    bazel run "${bazel_flags[@]}" //apps/telemetry/stack/tests:query_gateway_comparison -- "$@"
elif [[ "$benchmark" == 1 ]]; then
    bazel run "${bazel_flags[@]}" //apps/telemetry/stack/tests:query_gateway_benchmark -- "$@"
else
    bazel test "${bazel_flags[@]}" //apps/telemetry/stack/tests:query_gateway_integration_test \
        --nocache_test_results --test_env=QUERY_TEST_COMPOSE --test_env=ARTEMIS_DB_INIT_SQL --test_env=DOCKER_CONFIG --test_output=errors "$@"
fi
