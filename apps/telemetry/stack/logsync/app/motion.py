"""Vehicle-motion detection from fresh Artemis speed observations.

Only recent, non-NULL speed observations participate. GPS speed uses m/s after
conversion from knots; wheel speed keeps its independent rad/s threshold.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import asyncpg

from sqlalchemy import BigInteger, Float, bindparam, column, literal, select, table, union_all
from sqlalchemy.dialects.postgresql.asyncpg import dialect

from .config import config


def _speed_query():
    sources = []
    for name, fields in (("dynamics_333hz", ("bl_wheel_speed", "br_wheel_speed", "fl_wheel_speed", "fr_wheel_speed")),
                         ("dynamics_10hz", ("wheel_speed",)), ("dynamics_external", ("gps_speed",))):
        source = table(name, column("time", BigInteger), column("sample_time_ns", BigInteger), *(column(f, Float) for f in fields))
        for field in fields:
            latest = select(source.c.time.label("time_ms"), source.c[field].label("speed"),
                            literal(field == "gps_speed").label("gps")).where(
                source.c.time >= bindparam("since", type_=BigInteger), source.c[field].is_not(None)
            ).order_by(source.c.time.desc(), source.c.sample_time_ns.desc()).limit(1).subquery()
            sources.append(select(latest))
    return union_all(*sources).compile(dialect=dialect())


_QUERY = _speed_query()


@dataclass
class Motion:
    moving: bool
    speed_mps: float | None     # best-known speed, None if no data
    sample_age_ms: int | None   # how old the freshest sample is
    recent: bool                # sample is fresher than the staleness window
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "moving": self.moving,
            "speed_mps": self.speed_mps,
            "sample_age_ms": self.sample_age_ms,
            "recent": self.recent,
            "error": self.error,
        }


class MotionMonitor:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> asyncpg.Pool:
        # Lock so concurrent callers (UI /motion poll + worker loop) don't each
        # build a pool across the create_pool await and leak one.
        async with self._lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    dsn=config.resolved_pg_dsn, min_size=1, max_size=2, command_timeout=5
                )
            return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def read(self) -> Motion:
        try:
            pool = await self._get_pool()
            now_ms = int(time.time() * 1000)
            values = dict(_QUERY.params, since=now_ms - config.motion_staleness_ms)
            rows = await pool.fetch(str(_QUERY), *(values[name] for name in _QUERY.positiontup))
        except Exception as e:  # DB down, schema mismatch, etc.
            # Fail open: we can't prove the car is moving, so don't block forever.
            return Motion(moving=False, speed_mps=None, sample_age_ms=None,
                          recent=False, error=str(e))

        return motion_from_rows(rows, now_ms)


def motion_from_rows(rows, now_ms):
    recent = [row for row in rows if 0 <= now_ms - int(row["time_ms"]) <= config.motion_staleness_ms]
    gps = [abs(row["speed"]) * 0.514444 for row in recent if row["gps"]]
    moving = any(abs(row["speed"]) * (0.514444 if row["gps"] else 1) >
                 (config.speed_threshold_mps if row["gps"] else config.wheel_threshold_rad_s) for row in recent)
    age = min((now_ms - int(row["time_ms"]) for row in recent), default=None)
    return Motion(moving=moving, speed_mps=max(gps,default=None), sample_age_ms=age, recent=bool(recent))
