#!/usr/bin/env bash

prepare_orion_schema() {
    local repo_root="$1" compose_command="$2"
    local generated="$repo_root/bazel-bin/lib/codegen/telemetry/orion_db_init.sql"
    local selected="${ORION_DB_INIT_SQL:-$generated}"
    case "$compose_command" in
        up|create|run|watch)
            if [[ "$selected" != "$generated" ]]; then
                [[ "$selected" == /* && -f "$selected" ]] || {
                    printf 'Explicit ORION_DB_INIT_SQL must name an existing absolute file: %s\n' "$selected" >&2
                    return 1
                }
            else
                command -v bazel >/dev/null 2>&1 || {
                    printf 'bazel is required to resolve the generated Orion database schema\n' >&2
                    return 1
                }
                ( cd "$repo_root" && bazel build --config=local //apps/telemetry:current_schema_sql ) || return 1
                [[ -f "$generated" ]] || {
                    printf 'Bazel did not produce Orion database schema: %s\n' "$generated" >&2
                    return 1
                }
            fi
            ;;
    esac
    ORION_DB_INIT_SQL="$selected"
    export ORION_DB_INIT_SQL
    local artemis_generated="$repo_root/bazel-bin/lib/codegen/telemetry/artemis_db_init.sql"
    local artemis_selected="${ARTEMIS_DB_INIT_SQL:-$artemis_generated}"
    case "$compose_command" in
        up|create|run|watch)
            if [[ "$artemis_selected" == "$artemis_generated" ]]; then
                ( cd "$repo_root" && bazel build --config=local //apps/telemetry:new_car_schema_sql ) || return 1
            fi
            [[ "$artemis_selected" == /* && -f "$artemis_selected" ]] || {
                printf 'ARTEMIS_DB_INIT_SQL must name an existing absolute file: %s\n' "$artemis_selected" >&2
                return 1
            }
            ;;
    esac
    ARTEMIS_DB_INIT_SQL="$artemis_selected"
    export ARTEMIS_DB_INIT_SQL
}
