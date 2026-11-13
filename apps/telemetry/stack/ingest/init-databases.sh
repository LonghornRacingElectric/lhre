#!/usr/bin/env bash
set -Eeuo pipefail

# telemetry contains shared drive-day/track metadata, not a retired car's samples.
for database in telemetry artemis; do
    if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$database'" | grep -q 1; then
        createdb --username "$POSTGRES_USER" "$database"
    fi
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname telemetry -f /app/common_schema.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname artemis -f /app/artemis_db_init.sql

# The gateway has its own read role; it cannot write even if a query is faulty.
# Identifiers are fixed and the password is a psql literal parameter.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set=analysis_password="$ANALYSIS_PWD" <<'SQL'
SELECT format('CREATE ROLE analysis LOGIN PASSWORD %L', :'analysis_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analysis') \gexec
SQL
for database in telemetry artemis; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" <<'SQL'
GRANT USAGE ON SCHEMA public TO analysis;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analysis;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analysis;
SQL
done
