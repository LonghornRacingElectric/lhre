#!/usr/bin/env bash
set -Eeuo pipefail

# telemetry contains shared drive-day/track metadata, not a retired car's samples.
for database in telemetry orion artemis; do
    if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$database'" | grep -q 1; then
        createdb --username "$POSTGRES_USER" "$database"
    fi
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname telemetry -f /app/common_schema.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname orion -f /app/orion_db_init.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname artemis -f /app/artemis_db_init.sql
