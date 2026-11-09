"""Frequency pools and time-bucketed read interfaces derived from the CAN spec."""

import hashlib
import json
import re

from lib.spec.ir import validator, wire


SQL_TYPES = {
    "float": "double precision", "double": "double precision",
    "int32": "integer", "uint32": "bigint", "int64": "bigint",
    "uint64": "numeric", "bool": "boolean", "string": "text", "bytes": "bytea",
}


def snake_case(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def contract(spec):
    """Each logical field has one home, at its fastest declared producer rate."""
    rates = {}
    for _, message, _, _, binding in validator.telemetry_bindings(spec):
        key = (binding.group, binding.field)
        rates[key] = max(rates.get(key, 0), message.frequency_hz)
    ids = wire.field_ids(spec)
    groups = {group.name: group.snapshot_id for _, group in spec.groups()}
    pools = {}
    for (group, name), (field_type, _) in sorted(wire.field_types(spec).items()):
        hz = rates.get((group, name), 0)
        suffix = (f"{hz:g}".replace(".", "p") + "hz") if hz else "external"
        table = snake_case(group) + "_" + suffix
        pool = pools.setdefault(table, {"table": table, "group": snake_case(group), "frequency_hz": hz, "fields": []})
        repeated = field_type.startswith("repeated ")
        scalar = field_type.removeprefix("repeated ")
        pool["fields"].append({
            "name": name, "path": [groups[group], ids[group, name]],
            "sql_type": SQL_TYPES.get(scalar, "integer") + ("[]" if repeated else ""),
            "aggregate": "avg" if scalar in ("float", "double") and not repeated else "last",
        })
    value = {"format_version": 1, "car": "Artemis", "read_bucket_ms": 100, "bucket_aggregates": ["avg", "min", "max", "last"], "pools": list(pools.values())}
    value["storage_id"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def common_schema(sql):
    # Historical cars still consume the unmodified common schema.
    sql = re.sub(r"CREATE TABLE public\.partitions\(.*?\);", "", sql, flags=re.S)
    sql = re.sub(r"CREATE TABLE public\.gps_precise \(.*?\);", "", sql, flags=re.S)
    sql = re.sub(r"CREATE INDEX IF NOT EXISTS idx_gps_precise_time.*?;", "", sql)
    sql = re.sub(r"\breal\b", "double precision", sql)
    return sql


SESSION_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS public.recording_session (
    session_start_ms bigint PRIMARY KEY,
    start_time bigint NOT NULL,
    end_time bigint NOT NULL,
    CHECK (start_time > 0 AND end_time >= start_time)
);
CREATE OR REPLACE VIEW public.partitions AS
SELECT to_char(to_timestamp(start_time / 1000.0) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS') AS partition_name,
       start_time, end_time FROM public.recording_session;
CREATE TABLE IF NOT EXISTS public.transport_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    last_packet_id bigint NOT NULL DEFAULT 0
);
INSERT INTO public.transport_state(singleton) VALUES (true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS public.storage_migration (
    storage_id text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    contract jsonb NOT NULL
);
"""

GPS_FIELDS = {
    "lat_precise": "double precision", "lon_precise": "double precision",
    "gps_h_m": "double precision", "gps_sigma_h_m": "double precision",
    "gps_src": "smallint", "gps_fix_time": "double precision", "gps_speed_kn": "double precision",
}


def table_sql(table, fields):
    columns = ["session_start_ms bigint NOT NULL", '"time" bigint NOT NULL CHECK ("time" > 0)']
    columns += [f'{field["name"]} {field["sql_type"]}' for field in fields]
    columns.append('PRIMARY KEY (session_start_ms, "time")')
    lines = [f"CREATE TABLE IF NOT EXISTS public.{table} (\n    " + ",\n    ".join(columns) + "\n);"]
    # No defaults: existing recordings must read NULL for newly added sensors.
    lines += [f'ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {field["name"]} {field["sql_type"]};' for field in fields]
    lines.append(f"SELECT create_hypertable('public.{table}', by_range('time', 86400000::bigint), if_not_exists => TRUE);")
    return "\n".join(lines)


def read_sql(value):
    """Aggregate each pool before joining. Raw samples remain untouched."""
    lines = []
    groups = sorted({pool["group"] for pool in value["pools"]})
    for group in groups:
        lines += [f"DROP VIEW IF EXISTS public.{group};", f"DROP FUNCTION IF EXISTS public.{group}_buckets(bigint, bigint, bigint);", f"DROP FUNCTION IF EXISTS public.{group}_buckets(bigint, bigint, bigint, text);"]
        pools = [pool for pool in value["pools"] if pool["group"] == group]
        # Alphabetical view columns make unrelated generator ordering irrelevant.
        fields = sorted((field for pool in pools for field in pool["fields"]), key=lambda field: field["name"])
        returns = ["session_start_ms bigint", '"time" bigint'] + [f'{f["name"]} {f["sql_type"]}' for f in fields]
        ctes = []
        names = {}
        for i, pool in enumerate(pools):
            selected = []
            for f in pool["fields"]:
                names[f["name"]] = f"p{i}.{f['name']}"
                expression = f'last({f["name"]}, "time")'
                if f["aggregate"] == "avg":
                    name = f["name"]
                    expression = f"CASE bucket_aggregate WHEN 'min' THEN min({name}) WHEN 'max' THEN max({name}) WHEN 'last' THEN last({name}, \"time\") ELSE avg({name}) END"
                selected.append(f'{expression}::{f["sql_type"]} AS {f["name"]}')
            ctes.append(f'''p{i} AS (
    SELECT session_start_ms, time_bucket(bucket_ms, "time") AS "time", {", ".join(selected)}
    FROM public.{pool["table"]}
    WHERE "time" >= from_ms AND "time" < to_ms
    GROUP BY session_start_ms, time_bucket(bucket_ms, "time")
)''')
        joins = "p0" + "".join(f" FULL JOIN p{i} USING (session_start_ms, time)" for i in range(1, len(pools)))
        lines.append(f'''CREATE OR REPLACE FUNCTION public.{group}_buckets(bucket_ms bigint, from_ms bigint, to_ms bigint, bucket_aggregate text DEFAULT 'avg')
RETURNS TABLE ({", ".join(returns)}) LANGUAGE sql STABLE AS $buckets$
WITH {", ".join(ctes)}
SELECT session_start_ms, "time", {", ".join(names[f["name"]] for f in fields)} FROM {joins}
$buckets$;''')
        lines.append(f'''CREATE OR REPLACE VIEW public.{group} AS
SELECT * FROM public.{group}_buckets(100, 1, 9223372036854775807);''')
    union = " UNION ".join(f'SELECT session_start_ms, time_bucket(100::bigint, "time") AS "time" FROM public.{p["table"]}' for p in value["pools"])
    lines.append(f'''CREATE OR REPLACE VIEW public.sample_time AS {union};''')
    return "\n\n".join(lines) + "\n"


def update_sql(value):
    lines = [SESSION_SQL]
    for pool in value["pools"]:
        lines.append(table_sql(pool["table"], pool["fields"]))
    lines.append(table_sql("gps_precise", [{"name": name, "sql_type": kind} for name, kind in GPS_FIELDS.items()]))
    lines.append(read_sql(value))
    literal = json.dumps(value, sort_keys=True).replace("'", "''")
    lines.append(f"INSERT INTO public.storage_migration(storage_id, contract) VALUES ('{value['storage_id']}', '{literal}'::jsonb) ON CONFLICT DO NOTHING;")
    return "\n\n".join(lines)


def render_orm(value):
    types = {"double precision": "DOUBLE_PRECISION", "integer": "Integer", "bigint": "BigInteger", "numeric": "Numeric", "boolean": "Boolean", "text": "Text", "bytea": "BYTEA"}
    lines = [
        '"""Generated Artemis storage and bucketed read models."""',
        "from sqlalchemy import Column, Integer, BigInteger, Numeric, Boolean, Text",
        "from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, DOUBLE_PRECISION",
        "", "POOLS = " + repr(value["pools"]), "STORAGE_ID = " + repr(value["storage_id"]),
        "", "def declare_models(base):", "    models = {}",
    ]
    tables = [(pool["table"], "Artemis" + "".join(part.capitalize() for part in pool["table"].split("_")), pool["fields"]) for pool in value["pools"]]
    for group in sorted({pool["group"] for pool in value["pools"]}):
        fields = [field for pool in value["pools"] if pool["group"] == group for field in pool["fields"]]
        tables.append((group, "Artemis" + "".join(part.capitalize() for part in group.split("_")), fields))
    tables.append(("sample_time", "ArtemisSampleTime", []))
    for table, name, fields in tables:
        lines += [f"    class {name}(base):", f"        __tablename__ = {table!r}", "        __table_args__ = {'schema': 'public'}", "        session_start_ms = Column(BigInteger, primary_key=True)", "        time = Column(BigInteger, primary_key=True)"]
        for field in fields:
            kind = types[field["sql_type"].removesuffix("[]")]
            if field["sql_type"].endswith("[]"):
                kind = f"ARRAY({kind})"
            lines.append(f"        {field['name']} = Column({kind})")
        lines.append(f"    models[{name!r}] = {name}")
    lines += ["    return models", ""]
    return "\n".join(lines)
