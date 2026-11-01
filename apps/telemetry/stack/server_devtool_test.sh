#!/usr/bin/env bash

set -euo pipefail

source "$(dirname "$0")/server_devtool.sh"
integration_runner="$(dirname "$0")/tests/run_integration_test.sh"

OS="Darwin"
HOME="/Users/test"

df() {
    printf '%s\n' \
        "Filesystem 1024-blocks Used Available Capacity Mounted on" \
        "/dev/disk 20971520 8388608 12582912 40% /System/Volumes/Data"
}

[[ "$(docker_root)" == "$HOME" ]]
[[ "$(free_gb)" == "12" ]]

fixture="$(mktemp -d "${TEST_TMPDIR:-${TMPDIR:-/tmp}}/schema path.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
REPO_ROOT="$fixture/repo"
ingest_dir="$REPO_ROOT/ingest"
generated="$REPO_ROOT/bazel-bin/lib/codegen/telemetry/orion_db_init.sql"
build_log="$fixture/builds"
mkdir -p "$ingest_dir" "$(dirname "$generated")" "$fixture/bin"
touch "$ingest_dir/docker-compose.yml" "$build_log"
printf 'stale\n' > "$generated"
printf 'first edit\n' > "$REPO_ROOT/spec"

bazel() {
    [[ "$*" == "build --config=local //apps/telemetry:current_schema_sql" ]] || return 1
    printf 'build\n' >> "$build_log"
    [[ "${fail_build:-0}" == 0 ]] || return 9
    cat "$REPO_ROOT/spec" > "$generated"
}

cat > "$fixture/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
    "compose up"|"compose create"|"compose run") [[ -f "$ORION_DB_INIT_SQL" ]] ;;
    "compose down"|"compose logs"|"compose ps"|"compose config"|"compose restart"|"compose start") ;;
    *) exit 1 ;;
esac
[[ "$ORION_DB_INIT_SQL" == /* ]]
printf '%s\n' "$ORION_DB_INIT_SQL"
DOCKER
chmod +x "$fixture/bin/docker"
export PATH="$fixture/bin:$PATH"

sudo() {
    command env -i "PATH=$PATH" "$@"
}

unset ORION_DB_INIT_SQL
prepare_orion_schema "$REPO_ROOT" up
[[ "$(cat "$generated")" == "first edit" ]]
printf 'second edit\n' > "$REPO_ROOT/spec"
prepare_orion_schema "$REPO_ROOT" up
[[ "$(cat "$generated")" == "second edit" ]]
[[ "$(wc -l < "$build_log" | tr -d ' ')" == 2 ]]

for SUDO in "" sudo; do
    [[ "$(compose_in "$ingest_dir" up)" == "$generated" ]]
done

fail_build=1
if compose_in "$ingest_dir" up; then
    echo 'A failed schema build must prevent Compose from running' >&2
    exit 1
fi
fail_build=0

build_count="$(wc -l < "$build_log")"
missing_repo="$fixture/no build output"
mkdir -p "$missing_repo"
for SUDO in "" sudo; do
    for compose_command in down logs ps config restart start; do
        (
            REPO_ROOT="$missing_repo"
            unset ORION_DB_INIT_SQL
            fail_build=1
            [[ "$(compose_in "$ingest_dir" "$compose_command")" == "$REPO_ROOT/bazel-bin/lib/codegen/telemetry/orion_db_init.sql" ]]
        )
    done
done
[[ "$(wc -l < "$build_log")" == "$build_count" ]]

(
    source "$integration_runner"
    REPO_ROOT="$missing_repo"
    unset ORION_DB_INIT_SQL
    fail_build=1
    docker-compose() {
        [[ -n "$ORION_DB_INIT_SQL" ]]
        [[ "$ORION_DB_INIT_SQL" == "$REPO_ROOT/bazel-bin/lib/codegen/telemetry/orion_db_init.sql" ]]
        printf '%s\n' "$2" >> "$fixture/cleanup"
    }
    cleanup
    [[ "$(cat "$fixture/cleanup")" == "$(printf '%s\n' "$GPS_COMPOSE" "$LAP_TIMER_COMPOSE" "$KAFKA_BASE_COMPOSE" "$INGEST_COMPOSE" "$KAFKA_COMPOSE")" ]]
)
[[ "$(wc -l < "$build_log")" == "$build_count" ]]

override="$fixture/manual schema.sql"
printf 'admin override\n' > "$override"
printf 'bazel schema\n' > "$fixture/bazel schema.sql"
for selected_schema in "" "$override"; do
    (
        export TEST_SRCDIR="$fixture"
        ORION_DB_INIT_SQL="$selected_schema"
        source "$integration_runner" "bazel schema.sql"
        fail_build=1
        prepare_orion_schema "$REPO_ROOT" up
        [[ "$ORION_DB_INIT_SQL" == "${selected_schema:-$fixture/bazel schema.sql}" ]]
    )
done
build_count="$(wc -l < "$build_log")"
for SUDO in "" sudo; do
    unset ORION_DB_INIT_SQL
    ORION_DB_INIT_SQL="$override"
    [[ "$(compose_in "$ingest_dir" up)" == "$override" ]]
done
[[ "$(wc -l < "$build_log")" == "$build_count" ]]

ORION_DB_INIT_SQL="$fixture/missing.sql"
if compose_in "$ingest_dir" up; then
    echo 'A missing override must fail without falling back' >&2
    exit 1
fi

OS="Linux"
docker_root() {
    printf '/var/lib/docker\n'
}
[[ "$(free_gb)" == "12" ]]

require_docker() { SUDO="${test_sudo:-}"; }
bazel() { printf '%s\n' "$@"; }
for test_sudo in "" sudo; do
    expected="$(printf '%s\n' run --config=local //apps/telemetry/schema_registry:deployed_registry_binary --)"
    if [[ -n "$test_sudo" ]]; then expected="$expected"$'\n''--docker-sudo'; fi
    expected="$expected"$'\n'"$(printf '%s\n' --config "$override")"
    [[ "$(run_command schema-serve --config "$override")" == "$expected" ]]
done
