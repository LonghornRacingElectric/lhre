"""Atomic Artemis pool writes. Kafka offsets remain owned by db_writer."""

from collections import defaultdict

from sqlalchemy import and_, select, update, func
from sqlalchemy.dialects.postgresql import insert

from lib.codegen.telemetry.artemis_models import POOLS, STORAGE_ID
from analysis.sql_utils.models import (ARTEMIS_MODELS, ArtemisGpsPrecise, ArtemisRecordingSession,
                                       ArtemisTransportState, ArtemisStorageMigration)


POOL_MODELS = {model.__tablename__: model for model in ARTEMIS_MODELS.values()
               if model.__tablename__ in {pool["table"] for pool in POOLS}}
GAP_MS = 300_000


def require_schema(session):
    if not session.scalar(select(ArtemisStorageMigration.storage_id).where(ArtemisStorageMigration.storage_id == STORAGE_ID)):
        raise RuntimeError("Artemis storage schema is not applied; run //apps/telemetry:schema_apply")


def assign_sessions(session, times):
    """Match historical windows too; late data can bridge two recordings."""
    session.execute(select(func.pg_advisory_xact_lock(176021, 1)))
    mapping = {}
    tables = [model.__table__ for model in POOL_MODELS.values()] + [ArtemisGpsPrecise.__table__]
    for stamp in sorted(set(times)):
        nearby = session.scalars(select(ArtemisRecordingSession).where(
            ArtemisRecordingSession.start_time <= stamp + GAP_MS,
            ArtemisRecordingSession.end_time >= stamp - GAP_MS,
        ).order_by(ArtemisRecordingSession.session_start_ms).with_for_update()).all()
        if not nearby:
            key = stamp
            session.add(ArtemisRecordingSession(session_start_ms=stamp, start_time=stamp, end_time=stamp))
            session.flush()
        else:
            key = nearby[0].session_start_ms
            for other in nearby[1:]:
                for table in tables:
                    session.execute(update(table).where(table.c.session_start_ms == other.session_start_ms).values(session_start_ms=key))
                session.delete(other)
                mapping = {t: key if old == other.session_start_ms else old for t, old in mapping.items()}
            nearby[0].start_time = min(stamp, *(r.start_time for r in nearby))
            nearby[0].end_time = max(stamp, *(r.end_time for r in nearby))
            session.flush()
        mapping[stamp] = key
    return mapping


def insert_rows(session, table, rows):
    """Identical replay succeeds; differing values at one sample time fail closed."""
    unique = {}
    for row in rows:
        key = (row["session_start_ms"], row["time"])
        if key in unique and unique[key] != row:
            raise ValueError(f"Conflicting {table.name} samples at {key}")
        unique[key] = row
    if not unique:
        return
    # Equal column sets allow one executemany-shaped statement for each pool.
    names = sorted({name for row in unique.values() for name in row})
    values = [{name: row.get(name) for name in names} for row in unique.values()]
    statement = insert(table).values(values)
    equal = and_(*(table.c[name].is_not_distinct_from(statement.excluded[name]) for name in names))
    admitted = session.execute(statement.on_conflict_do_update(
        index_elements=[table.c.session_start_ms, table.c.time],
        set_={"time": statement.excluded.time}, where=equal,
    ).returning(table.c.time)).all()
    if len(admitted) != len(values):
        raise ValueError(f"Conflicting stored {table.name} sample; batch rolled back")


def write_snapshots(session, messages):
    require_schema(session)
    sessions = assign_sessions(session, [int(message["time"]) for message in messages])
    jobs = defaultdict(list)
    for message in messages:
        stamp = int(message["time"])
        for pool in POOLS:
            source = message.get(pool["group"])
            if source is None:
                continue
            row = {field["name"]: source.get(field["name"]) for field in pool["fields"]}
            row.update(time=stamp, session_start_ms=sessions[stamp])
            jobs[pool["table"]].append(row)
    for name, rows in jobs.items():
        insert_rows(session, POOL_MODELS[name].__table__, rows)
    if messages:
        session.execute(update(ArtemisTransportState).where(ArtemisTransportState.singleton).values(
            last_packet_id=func.greatest(ArtemisTransportState.last_packet_id, max(int(m.get("packet_id", 0)) for m in messages))))


def write_gps(session, rows):
    require_schema(session)
    sessions = assign_sessions(session, [int(row["time"]) for row in rows])
    columns = ArtemisGpsPrecise.__table__.columns
    values = [{**{name: row.get(name) for name in columns.keys() if name != "session_start_ms"},
               "session_start_ms": sessions[int(row["time"])], "time": int(row["time"])} for row in rows]
    insert_rows(session, ArtemisGpsPrecise.__table__, values)
