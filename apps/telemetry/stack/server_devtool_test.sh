#!/usr/bin/env bash

set -euo pipefail

source "$(dirname "$0")/server_devtool.sh"

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
[[ "$*" == "compose config" ]]
[[ -f "$ORION_DB_INIT_SQL" ]]
printf '%s\n' "$ORION_DB_INIT_SQL"
DOCKER
chmod +x "$fixture/bin/docker"
export PATH="$fixture/bin:$PATH"

sudo() {
    command env -i "PATH=$PATH" "$@"
}

unset ORION_DB_INIT_SQL
ensure_orion_schema_path
[[ "$(cat "$generated")" == "first edit" ]]
printf 'second edit\n' > "$REPO_ROOT/spec"
ensure_orion_schema_path
[[ "$(cat "$generated")" == "second edit" ]]
[[ "$(wc -l < "$build_log" | tr -d ' ')" == 2 ]]

for SUDO in "" sudo; do
    [[ "$(compose_in "$ingest_dir" config)" == "$generated" ]]
done

fail_build=1
if compose_in "$ingest_dir" config; then
    echo 'A failed schema build must prevent Compose from running' >&2
    exit 1
fi
fail_build=0

override="$fixture/manual schema.sql"
printf 'admin override\n' > "$override"
build_count="$(wc -l < "$build_log")"
for SUDO in "" sudo; do
    unset ORION_DB_INIT_SQL
    ORION_DB_INIT_SQL="$override"
    [[ "$(compose_in "$ingest_dir" config)" == "$override" ]]
done
[[ "$(wc -l < "$build_log")" == "$build_count" ]]

ORION_DB_INIT_SQL="$fixture/missing.sql"
if compose_in "$ingest_dir" config; then
    echo 'A missing override must fail without falling back' >&2
    exit 1
fi

OS="Linux"
docker_root() {
    printf '/var/lib/docker\n'
}
[[ "$(free_gb)" == "12" ]]
