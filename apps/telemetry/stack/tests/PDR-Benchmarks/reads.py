"""Matched Postgres, Timescale and gateway reads, including synchronized bursts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import heapq
import json
import math
import random
import shutil
from fractions import Fraction
from pathlib import Path
import platform
import threading
import time

from psycopg2 import sql
from sqlalchemy import text

import common as c
from stack.tests import benchmark_query_gateway as fixture

SIGNALS = ["controls.apps1_travel", "controls.commanded_torque", "pack.hv_pack_v", "thermal.ambient_temp", "dynamics.bl_wheel_speed"]
class ResourceLimit(RuntimeError):
    pass


VARIANTS = {"gateway_uncached": (0, 0), "gateway_ram": (256 << 20, 0), "gateway_disk": (0, 4 << 30), "gateway_both": (256 << 20, 4 << 30)}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def milliseconds(stamp):
    return round(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def width_for(catalog, body):
    if body["agg"] == "raw":
        return 0
    width = max(1, math.ceil((milliseconds(body["t1"]) - milliseconds(body["t0"])) / body["max_points"]))
    for pool in catalog["pools"]:
        if pool["frequency_hz"] and any(s["pool"] == pool["table"] and s["signal"] in body["signals"] for s in catalog["signals"]):
            width = max(width, math.ceil(1000 / pool["frequency_hz"]))
    return math.ceil(width / 1000) * 1000 if width >= 1000 else width


class Direct:
    def __init__(self, catalog, url, timescale):
        self.catalog, self.timescale = catalog, timescale
        self.engine = c.engine(url)

    def statements(self, body):
        width = width_for(self.catalog, body)
        for pool in self.catalog["pools"]:
            selected = sorted(s["signal"] for s in self.catalog["signals"] if s["pool"] == pool["table"] and s["signal"] in body["signals"])
            if not selected:
                continue
            fields = [sql.Identifier(s.split(".")[1]) for s in selected]
            bucket = sql.SQL('"time"') if width == 0 else (sql.SQL('time_bucket({}::bigint, "time")').format(sql.Literal(width)) if self.timescale else sql.SQL('("time" / {}::bigint) * {}::bigint').format(sql.Literal(width), sql.Literal(width)))
            aggregate = {"raw": "", "mean": "avg", "max": "max"}[body["agg"]]
            columns = fields if width == 0 else [sql.SQL(aggregate + "({})").format(field) for field in fields]
            ns = sql.SQL("sample_time_ns") if width == 0 else sql.SQL("{} * 1000000::bigint").format(bucket)
            query = sql.SQL('SELECT {}, session_start_ms, {}, {} FROM {} WHERE "time" >= %s AND "time" < %s').format(bucket, ns, sql.SQL(", ").join(columns), sql.Identifier(pool["table"]))
            if width:
                query += sql.SQL(" GROUP BY 1, 2")
            query += sql.SQL(" ORDER BY 1, 2, 3")
            yield selected, query, (milliseconds(body["t0"]), milliseconds(body["t1"]))

    def __call__(self, body):
        fingerprint = c.Fingerprint()
        with self.engine.connect() as db:
            cursors, streams = [], []
            try:
                for selected, query, bounds in self.statements(body):
                    cursor = db.connection.cursor(name=f"pdr_{len(cursors)}")
                    cursors.append(cursor)
                    cursor.itersize = 1000
                    cursor.execute(query, bounds)
                    streams.append(self.observations(cursor, selected))
                for row in heapq.merge(*streams):
                    fingerprint.add(row)
            finally:
                for cursor in cursors:
                    cursor.close()
        return fingerprint.result({"timescale" if self.timescale else "postgres"}, 1)

    def explain(self, body):
        plans = []
        for selected, query, bounds in self.statements(body):
            try:
                with self.engine.connect() as db:
                    cursor = db.connection.cursor()
                    cursor.execute(sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ") + query, bounds)
                    plans.append({"signals": selected, "plan": cursor.fetchone()[0]})
                    cursor.close()
            except Exception as exc:
                plans.append({"signals": selected, "error": str(exc)})
        return plans

    @staticmethod
    def observations(cursor, signals):
        for row in cursor:
            for signal, value in zip(signals, row[3:]):
                if value is not None:
                    yield (iso(row[0]), row[1], row[2], signal, value)


def seed(catalog, start, seconds, report, fixture_pools=None, fixture_fields=None):
    """COPY identical generated-rate observations to both engines in bounded chunks."""
    engines = [c.engine(c.ADMIN), c.engine(c.PLAIN)]
    try:
        with engines[1].begin() as db:
            # SQLAlchemy's generated models describe plain tables, without Timescale DDL.
            fixture.ArtemisRecordingSession.__table__.create(db, checkfirst=True)
            for pool in catalog["pools"]:
                fixture.MODELS[pool["table"]].create(db, checkfirst=True)
                db.execute(text(f'CREATE INDEX IF NOT EXISTS pdr_time_{pool["table"]} ON "{pool["table"]}" ("time" DESC)'))
        counts = {}
        populated_fields = {}
        for engine in engines:
            with engine.begin() as db:
                exists = db.scalar(text("SELECT count(*) FROM recording_session"))
                if exists:
                    raise RuntimeError("Read fixture requires a fresh isolated stack")
                db.execute(fixture.ArtemisRecordingSession.__table__.insert().values(session_start_ms=start, start_time=start, end_time=start + seconds * 1000 - 1, data_revision=1, external_revision=1, last_write_ms=int(time.time() * 1000)))
        for pool in catalog["pools"]:
            if fixture_pools is not None and pool["table"] not in fixture_pools:
                continue
            count = round(fixture.rate(pool) * seconds)
            fields = [f for f in pool["fields"] if fixture_fields is None or pool["group"] + "." + f["name"] in fixture_fields]
            if not fields:
                fields = pool["fields"]
            populated_fields[pool["table"]] = [f["name"] for f in fields]
            rate = Fraction(str(fixture.rate(pool)))
            ns = sql.SQL("({}::bigint + (i * 1000000000::bigint * {}::bigint / {}::bigint))").format(sql.Literal(start * 1000000), sql.Literal(rate.denominator), sql.Literal(rate.numerator))
            values = []
            for field in fields:
                kind = field["sql_type"]
                values.append(sql.SQL("i %% 2 = 0") if kind == "boolean" else sql.SQL("ARRAY[0., (i %% 100)::double precision, 3.5]::double precision[]") if kind.endswith("[]") else sql.SQL("(i %% 100)::double precision"))
            columns = ["session_start_ms", "time", "sample_time_ns", *[f["name"] for f in fields]]
            for engine in engines:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL statement_timeout = '10min'"))
                    cursor = connection.connection.cursor()
                    try:
                        for offset in range(0, count, 100000):
                            if shutil.disk_usage(Path.cwd()).free < 8 << 30:
                                raise ResourceLimit("Less than 8 GiB host disk free; fixture stopped before exhausting storage")
                            if offset % 1000000 == 0 and c.docker_free_bytes() < 8 << 30:
                                raise ResourceLimit("Less than 8 GiB Docker disk free; fixture stopped")
                            query = sql.SQL("INSERT INTO {} ({}) SELECT {}, ({} / 1000000::bigint), {}, {} FROM generate_series(%s::bigint, %s::bigint) AS i").format(sql.Identifier(pool["table"]), sql.SQL(",").join(map(sql.Identifier, columns)), sql.Literal(start), ns, ns, sql.SQL(",").join(values))
                            cursor.execute(query, (offset, min(count, offset + 100000) - 1))
                    finally:
                        cursor.close()
            counts[pool["table"]] = count
            print(json.dumps({"seeded": pool["table"], "rows_per_database": count}), flush=True)
        report["fixture"] = {"populated_fields": populated_fields, "populated_pools": sorted(counts), "seconds": seconds, "rows_per_database": sum(counts.values()), "pools": counts, "rates_hz": {p["table"]: fixture.rate(p) for p in catalog["pools"]}, "external_rate_assumptions": {p["table"]: fixture.rate(p) for p in catalog["pools"] if not p["frequency_hz"]}}
        for label, engine in zip(("timescale", "postgres"), engines):
            with engine.begin() as db:
                for name, count in counts.items():
                    actual, low, high, invalid = db.execute(text(f'SELECT count(*), min("time"), max("time"), count(*) FILTER (WHERE "time" != sample_time_ns / 1000000) FROM "{name}"')).one()
                    if invalid or low < start or high >= start + seconds * 1000:
                        raise AssertionError((label, name, "invalid fixture timestamp range", low, high, invalid))
                    if actual != count:
                        raise AssertionError((label, name, actual, count))
                    db.execute(text(f'ANALYZE "{name}"'))
                report["fixture"][label + "_bytes"] = db.scalar(text("SELECT pg_database_size(current_database())"))
                report["fixture"][label + "_version"] = db.scalar(text("SELECT version()"))
                report["fixture"][label + "_settings"] = dict(db.execute(text("SELECT name, setting FROM pg_settings WHERE name IN ('shared_buffers', 'work_mem', 'effective_cache_size', 'max_parallel_workers_per_gather', 'max_connections', 'synchronous_commit', 'fsync', 'jit')")).all())
                report["fixture"][label + "_extensions"] = dict(db.execute(text("SELECT extname, extversion FROM pg_extension")).all())
                if label == "timescale":
                    report["fixture"]["chunks"] = [dict(row._mapping) for row in db.execute(text("SELECT hypertable_name, count(*) AS chunks FROM timescaledb_information.chunks GROUP BY hypertable_name ORDER BY hypertable_name"))]
    finally:
        for engine in engines:
            engine.dispose()


def measure(call, body, expected, clients):
    barrier = threading.Barrier(clients)

    def request(_):
        barrier.wait(timeout=60)
        began = time.perf_counter()
        try:
            request_body = body[_] if isinstance(body, list) else body
            reference = expected[_] if isinstance(expected, list) else expected
            result = call(request_body)
            elapsed = (time.perf_counter() - began) * 1000
            if (result["rows"], result["sha256"]) != (reference["rows"], reference["sha256"]):
                raise AssertionError(f"Different result: {result}")
            return {"ms": elapsed, **result}
        except Exception as exc:
            return {"ms": (time.perf_counter() - began) * 1000, "error": str(exc)}

    began = time.perf_counter()
    with ThreadPoolExecutor(max_workers=clients) as workers:
        results = list(workers.map(request, range(clients)))
    wall = time.perf_counter() - began
    failures = [r for r in results if "error" in r]
    return {**c.quantiles([r["ms"] for r in results if "error" not in r]), "wall_ms": wall * 1000, "requests_per_second": (clients - len(failures)) / wall, "errors": len(failures), "clients": results}


def wait_sealed(catalog):
    container = c.compose("ps", "-q", "query_gateway")
    code = "import glob; print(len(glob.glob('/cache/*.json')))"
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        if c.docker_free_bytes() < 8 << 30:
            c.compose("stop", "query_gateway")
            raise ResourceLimit("Arrow preparation stopped: less than 8 GiB Docker disk free")
        count = int(c.command("docker", "exec", container, "python", "-c", code))
        if count == len(catalog["pools"]) * 2:
            return
        time.sleep(2)
    raise TimeoutError("Arrow sealing did not complete within one hour")



def oracle(catalog, start, seconds, body):
    """Independent fixture oracle; large DB timeouts must not skip other backends."""
    width = width_for(catalog, body)
    left, right = milliseconds(body["t0"]), milliseconds(body["t1"])
    pool_by_name = {p["table"]: p for p in catalog["pools"]}
    def ceiling(a, b):
        return -(-a // b)
    def prefix(count):
        periods, tail = divmod(count, 100)
        return periods * 4950 + tail * (tail - 1) // 2
    def stream(signal):
        pool = pool_by_name[signal["pool"]]
        rate = Fraction(str(fixture.rate(pool)))
        total = round(float(rate) * seconds)
        def index(stamp):
            return max(0, min(total, ceiling((stamp - start) * 1000000 * rate.numerator, 1000000000 * rate.denominator)))
        if width == 0:
            for i in range(index(left), index(right)):
                ns = start * 1000000 + i * 1000000000 * rate.denominator // rate.numerator
                yield (iso(ns // 1000000), start, ns, signal["signal"], float(i % 100))
        else:
            for stamp in range(left // width * width, right, width):
                lo, hi = index(max(left, stamp)), index(min(right, stamp + width))
                if hi <= lo:
                    continue
                value = (prefix(hi) - prefix(lo)) / (hi - lo) if body["agg"] == "mean" else (99 if lo // 100 != (hi - 1) // 100 else (hi - 1) % 100)
                yield (iso(stamp), start, stamp * 1000000, signal["signal"], value)
    streams = [stream(s) for s in catalog["signals"] if s["signal"] in body["signals"]]
    fingerprint = c.Fingerprint()
    for row in heapq.merge(*streams):
        fingerprint.add(row)
    return fingerprint.result({"fixture_oracle"}, 1)


def workloads(start, seconds, args):
    rng = random.Random(args.seed)
    cases = []
    for width in args.windows:
        if width > seconds:
            continue
        if width <= 600:
            positions = [(f"random_{i + 1}", rng.randrange(0, seconds - width + 1)) for i in range(args.random_windows)]
        else:
            positions = [("beginning", 0), ("middle", (seconds - width) // 2), ("end", seconds - width)] if width == 3600 else [("overview", 0)]
        unique_positions = {}
        for label, at in positions:
            unique_positions.setdefault(at, label)
        positions = [(label, at) for at, label in unique_positions.items()]
        for position, offset in positions:
            for aggregate in args.aggregates:
                if aggregate == "raw" and width > 600:
                    continue
                body = {"t0": iso(start + offset * 1000), "t1": iso(start + (offset + width) * 1000), "signals": SIGNALS, "agg": aggregate}
                if aggregate != "raw":
                    body["max_points"] = 1000
                distinct = []
                for i in range(args.clients):
                    # Evenly spread clients, with a reproducible random offset.
                    maximum = seconds - width
                    at = int((i / max(1, args.clients - 1)) * maximum)
                    distinct.append({**body, "t0": iso(start + at * 1000), "t1": iso(start + (at + width) * 1000)})
                cases.append({"window_seconds": width, "position": position, "aggregate": aggregate, "request": body, "distinct_requests": distinct, "unique_windows": len({(b["t0"], b["t1"]) for b in distinct})})
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-hours", type=float, default=24)
    parser.add_argument("--windows", type=int, nargs="+", default=[10, 600, 3600, 21600, 86400])
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--random-windows", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--aggregates", nargs="+", choices=["raw", "mean", "max"], default=["raw", "mean", "max"])
    parser.add_argument("--variants", nargs="+", choices=["postgres", "timescale", *VARIANTS], default=["postgres", "timescale", *VARIANTS])
    parser.add_argument("--fixture-fields", choices=["queried", "all"], default="queried")
    parser.add_argument("--fixture-pools", choices=["queried", "all"], default="queried")
    parser.add_argument("--disk-cache-bytes", type=int, default=16 << 30)
    parser.add_argument("--skip-plans", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if min(args.windows) <= 0 or args.clients < 1 or args.history_hours <= 0 or args.random_windows < 1 or args.disk_cache_bytes < 1:
        parser.error("Positive history, windows, clients and cache sizes are required")
    seconds = round(args.history_hours * 3600)
    if not any(w <= seconds for w in args.windows):
        parser.error("At least one requested window must fit the history")
    VARIANTS["gateway_disk"] = (0, args.disk_cache_bytes)
    VARIANTS["gateway_both"] = (256 << 20, args.disk_cache_bytes)
    report = {"host": platform.platform(), "configuration": {**vars(args), "report": str(args.report)}, "results": [], "cache_budgets": VARIANTS, "notes": ["Database/OS caches remain enabled; cold resets gateway RAM, not the OS.", "Disk variants start with sealed Arrow files.", "Direct reads use Python/psycopg2; gateway includes HTTP/JSON; both verify results.", "Four DB connections per direct pool and in the gateway.", "Numeric results are compared after rounding to eight decimal places.", "Different-window bursts run after another RAM reset; exact full-history windows cannot be distinct.", "EXPLAIN ANALYZE BUFFERS runs outside the timed trials."]}
    directs = []
    try:
        c.gateway_mode(0, 0)
        report["environment"] = c.environment()
        catalog = c.http("/v1/signals")
        report["schema_version"] = catalog["schema_version"]
        # Cross UTC midnight so a 24-hour history exercises multiple native daily chunks.
        start = (int(time.time() * 1000) // 86400000 - math.ceil(seconds / 86400) - 2) * 86400000 + 43200000
        pools = {s["pool"] for s in catalog["signals"] if s["signal"] in SIGNALS} if args.fixture_pools == "queried" else None
        seed(catalog, start, seconds, report, pools, set(SIGNALS) if args.fixture_fields == "queried" else None)
        c.save(report, args.report)
        direct = {"postgres": Direct(catalog, c.PLAIN, False), "timescale": Direct(catalog, c.READ, True)}
        directs = list(direct.values())
        expected_cache = {}
        def reference(body):
            key = json.dumps(body, sort_keys=True)
            if key not in expected_cache:
                expected_cache[key] = oracle(catalog, start, seconds, body)
            return expected_cache[key]
        for case in workloads(start, seconds, args):
            body, distinct = case["request"], case["distinct_requests"]
            report["phase"] = "reference"
            report["active_case"] = {k: case[k] for k in ("window_seconds", "position", "aggregate")}
            c.save(report, args.report)
            try:
                expected = reference(body)
                distinct_expected = [reference(b) for b in distinct]
            except Exception as exc:
                report.setdefault("reference_failures", []).append({**case, "error": str(exc)})
                c.save(report, args.report)
                continue
            for variant in args.variants:
                def reset():
                    if variant in VARIANTS:
                        l1, l2 = VARIANTS[variant]
                        c.gateway_mode(l1, l2)
                        if l2:
                            admin = c.engine(c.ADMIN)
                            with admin.begin() as db:
                                db.execute(text("UPDATE recording_session SET last_write_ms = 1"))
                            admin.dispose()
                            wait_sealed(catalog)
                call = direct.get(variant, c.gateway_read)
                record = {**case, "variant": variant, "selection": "dashboard", "expected": expected}
                report["active_case"] = record
                report["phase"] = "first_preparation"
                c.save(report, args.report)
                try:
                    reset()
                    record["first"] = measure(call, body, expected, 1)
                    report["phase"] = "cold_preparation"
                    c.save(report, args.report)
                    reset()
                    record["stats_before"] = c.stats()
                    record["cold_burst"] = measure(call, body, expected, args.clients)
                    report["phase"] = "warm_burst"
                    c.save(report, args.report)
                    record["warm_burst"] = measure(call, body, expected, args.clients)
                    report["phase"] = "different_window_burst"
                    c.save(report, args.report)
                    if case["unique_windows"] > 1:
                        reset()
                        record["different_window_burst"] = measure(call, distinct, distinct_expected, args.clients)
                    else:
                        record["different_window_burst"] = {"not_applicable": "Fewer than two unique ranges", "errors": 0, "clients": [], "n": 0}
                    record["stats_after"] = c.stats()
                    if variant in direct and not args.skip_plans:
                        record["query_plans"] = direct[variant].explain(body)
                except Exception as exc:
                    record["error"] = str(exc)
                    if isinstance(exc, ResourceLimit):
                        report["results"].append(record)
                        raise
                report["results"].append(record)
                c.save(report, args.report)
                print(json.dumps({"variant": variant, "window": case["window_seconds"], "position": case["position"], "agg": case["aggregate"], "error": record.get("error"), "phases": {k: {a: b for a, b in record[k].items() if a != "clients"} for k in ("first", "cold_burst", "warm_burst", "different_window_burst") if k in record}}), flush=True)
        report.pop("active_case", None)
        report["phase"] = "complete"
        failed = bool(report.get("reference_failures")) or any(r.get("error") or any(r.get(p, {}).get("errors", 0) for p in ("first", "cold_burst", "warm_burst", "different_window_burst")) for r in report["results"])
        report["correctness"] = "failed" if failed else "passed"
        if failed:
            raise AssertionError("Read failures recorded")
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        for direct in directs:
            direct.engine.dispose()
        c.save(report, args.report)


if __name__ == "__main__":
    main()
