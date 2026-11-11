"""Plan/apply generated additive Artemis DDL without replacing its data volume."""

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path

from runfiles import Runfiles
from sqlalchemy import inspect, select, func, create_engine
from sqlalchemy.orm import Session

from analysis.sql_utils.db_session import get_db
from analysis.sql_utils.models import ArtemisStorageMigration


def changes(connection, expected, allow_historical=False):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    if "packet" in tables:
        raise ValueError("Found the old packet-table layout. Historical migration is not supported by this command; use a fresh Artemis database.")
    previous = None
    if "storage_migration" in tables:
        previous = connection.execute(select(ArtemisStorageMigration.contract).order_by(
            ArtemisStorageMigration.applied_at.desc()).limit(1)).scalar()
    old_paths = {tuple(field["path"]): (pool["table"], field["name"], field["sql_type"])
                 for pool in (previous or {}).get("pools", []) for field in pool["fields"]}
    expected_paths = {tuple(field["path"]) for pool in expected["pools"] for field in pool["fields"]}
    if allow_historical and {pool["table"] for pool in (previous or {}).get("pools", [])} - {pool["table"] for pool in expected["pools"]}:
        raise ValueError("Rollback across added pools requires an explicit migration; older writers cannot maintain their session bounds")
    if old_paths.keys() - expected_paths and not allow_historical:
        raise ValueError("Retiring fields requires an explicit migration preserving historical read interfaces")
    planned = []
    for name in ("recording_session", "storage_migration", "gps_precise"):
        if name not in tables:
            planned.append(f"Create {name}")
    views = set(inspector.get_view_names(schema="public"))
    if not {pool["group"] for pool in expected["pools"]}.issubset(views):
        planned.append("Refresh generated bucket read interfaces")
    for pool in expected["pools"]:
        name = pool["table"]
        columns = {c["name"]: c for c in inspector.get_columns(name, schema="public")} if name in tables else {}
        if name not in tables:
            planned.append(f"Create {name}")
        else:
            key = inspector.get_pk_constraint(name, schema="public")["constrained_columns"]
            if key != ["session_start_ms", "time", "sample_time_ns"]:
                raise ValueError(f"{name}: expected native primary key (session_start_ms, time, sample_time_ns); use a fresh Artemis database")
        for field in pool["fields"]:
            identity = (name, field["name"], field["sql_type"])
            if tuple(field["path"]) in old_paths and old_paths[tuple(field["path"])] != identity:
                raise ValueError(f"{field['path']}: rename, type or frequency-pool change requires an explicit migration")
            column = columns.get(field["name"])
            if column is None:
                if columns:
                    planned.append(f"Add nullable {name}.{field['name']}")
            elif str(column["type"].compile(dialect=connection.dialect)).lower() != field["sql_type"]:
                raise ValueError(f"{name}.{field['name']}: database type differs from the generated schema")
    return planned


def apply(session, expected, sql):
    session.execute(select(func.set_config("lock_timeout", "5s", True)))
    session.execute(select(func.pg_advisory_xact_lock(176021, 1)))
    planned = changes(session.connection(), expected)
    applied = session.scalar(select(ArtemisStorageMigration.storage_id).where(
        ArtemisStorageMigration.storage_id == expected["storage_id"])) if inspect(session.connection()).has_table("storage_migration", schema="public") else None
    if not applied or planned:
        session.connection().exec_driver_sql(sql)
    return planned


@contextmanager
def database_session():
    url = os.getenv("ARTEMIS_DATABASE_URL")
    if url:
        engine = create_engine(url)
        try:
            with Session(engine) as session:
                yield session
        finally:
            engine.dispose()
    else:
        with get_db("Artemis") as session:
            yield session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract")
    parser.add_argument("sql")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    resolver = Runfiles.Create()
    def artifact(value):
        return Path(value) if Path(value).is_file() else Path(resolver.Rlocation(value))
    expected = json.loads(artifact(args.contract).read_text())
    with database_session() as session, session.begin():
        session.execute(select(func.pg_advisory_xact_lock(176021, 1)))
        planned = changes(session.connection(), expected)
        for change in planned:
            print(change)
        if args.apply:
            applied = session.scalar(select(ArtemisStorageMigration.storage_id).where(
                ArtemisStorageMigration.storage_id == expected["storage_id"])) if inspect(session.connection()).has_table("storage_migration", schema="public") else None
            if applied and not planned:
                print(f"Storage schema {expected['storage_id']} is already applied.")
                return
            # Only Bazel-generated DDL is executed, after validating existing types.
            session.connection().exec_driver_sql(artifact(args.sql).read_text())
            print(f"Applied storage schema {expected['storage_id']}")
        else:
            print("Plan only; no schema changes applied.")


if __name__ == "__main__":
    main()
