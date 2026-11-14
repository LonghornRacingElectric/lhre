"""Identical-result comparisons of direct Timescale and gateway cache tiers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import threading
import time

from sqlalchemy import BigInteger, Float, MetaData, Table, cast, create_engine, func, literal, select, update
from sqlalchemy.dialects import postgresql

from stack.tests import benchmark_query_gateway as bench


def milliseconds(stamp):
    return round(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def digest(rows):
    def normalize(value):
        if type(value) is float and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        return value
    return hashlib.sha256(json.dumps(normalize(rows), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Direct:
    def __init__(self, catalog):
        self.catalog = catalog
        self.db = create_engine("postgresql+psycopg2://analysis:gateway-test-analysis@localhost:35432/artemis", pool_size=4, max_overflow=0)
        self.metadata = create_engine("postgresql+psycopg2://analysis:gateway-test-analysis@localhost:35432/telemetry", pool_size=4, max_overflow=0)
        self.day = Table("drive_day", MetaData(), autoload_with=self.metadata)
        self.reads = 0
        self.lock = threading.Lock()

    def close(self):
        self.db.dispose()
        self.metadata.dispose()

    def __call__(self, body):
        selected = [s for s in self.catalog["signals"] if body["signals"] == ["*"] or s["signal"] in body["signals"]]
        start, end = milliseconds(body["t0"]), milliseconds(body["t1"])
        if "drive_day" in body:
            with self.metadata.connect() as db:
                bounds = db.execute(select(self.day.c.start_time, self.day.c.end_time).where(self.day.c.day_id == body["drive_day"])).one()
                start, end = max(start, bounds.start_time), min(end, bounds.end_time)
        width = body.get("bucket_ms", 0)
        aggregate = body["agg"]
        if aggregate != "raw":
            width = max(width, math.ceil((end - start) / body["max_points"]), 1)
            for pool in self.catalog["pools"]:
                if pool["frequency_hz"] and any(s["pool"] == pool["table"] for s in selected):
                    width = max(width, math.ceil(1000 / pool["frequency_hz"]))
            if not body.get("bucket_ms") and width >= 1000:
                width = math.ceil(width / 1000) * 1000
        output = []
        with self.db.connect() as db:
            for pool in self.catalog["pools"]:
                signals = [s for s in selected if s["pool"] == pool["table"]]
                if not signals:
                    continue
                table = bench.MODELS[pool["table"]]
                bucket = table.c.time if aggregate == "raw" else func.time_bucket(cast(literal(width), BigInteger), table.c.time)
                columns = [table.c.session_start_ms, bucket.label("ts")]
                columns.append(table.c.sample_time_ns if aggregate == "raw" else (bucket * 1_000_000).label("ns"))
                for signal in signals:
                    column = table.c[signal["signal"].split(".")[1]]
                    if aggregate == "last":
                        column = func.last(column, table.c.sample_time_ns).filter(column.is_not(None))
                    elif aggregate == "max":
                        column = func.max(column)
                    elif aggregate == "mean":
                        column = func.avg(column)
                    columns.append(column)
                statement = select(*columns).where(table.c.time >= start, table.c.time < end)
                if aggregate != "raw":
                    statement = statement.group_by(table.c.session_start_ms, bucket)
                with self.lock:
                    self.reads += 1
                for row in db.execute(statement):
                    for signal, value in zip(signals, row[3:]):
                        if value is not None:
                            output.append({"ts": bench.iso(row[1]).replace("+00:00", "Z"), "session_start_ms": str(row[0]), "sample_time_ns": str(row[2]), "signal": signal["signal"], "value": value})
        output.sort(key=lambda row: (row["ts"], int(row["session_start_ms"]), int(row["sample_time_ns"]), row["signal"]))
        return output, {"direct_timescale"}, 1


def telemetry_reads(direct=None):
    return direct.reads if direct else bench.http("/v1/stats")["reads"]["postgres"]


def measure(call, body, expected, repeats, direct=None):
    first = time.perf_counter()
    rows, used, pages = call(body)
    first_ms = (time.perf_counter() - first) * 1000
    assert digest(rows) == expected, "First result differs from direct Timescale"
    for _ in range(3):
        assert digest(call(body)[0]) == expected
    before = telemetry_reads(direct)
    times, sources = [], set()
    for _ in range(repeats):
        began = time.perf_counter()
        rows, used, pages = call(body)
        times.append((time.perf_counter() - began) * 1000)
        sources.update(used)
        assert digest(rows) == expected, "Result differs from direct Timescale"
    return {**bench.quantiles(times), "samples_ms": times, "first_ms": round(first_ms, 2), "rows": len(rows), "pages": pages, "sources": sorted(sources), "telemetry_db_reads": telemetry_reads(direct) - before, "result_sha256": expected}


def concurrent(call, cases, expected, clients, repeats, direct=None):
    def client(count):
        times = []
        for i in range(count):
            name = "overview" if i % 5 == 0 else "zoom"
            began = time.perf_counter()
            rows, _, _ = call(cases[name])
            elapsed = (time.perf_counter() - began) * 1000
            assert digest(rows) == expected[name], name
            times.append(elapsed)
        return times
    with ThreadPoolExecutor(max_workers=clients) as workers:
        list(workers.map(client, [1] * clients))
        before = telemetry_reads(direct)
        began = time.perf_counter()
        times = [ms for group in workers.map(client, [repeats] * clients) for ms in group]
        elapsed = time.perf_counter() - began
    return {**bench.quantiles(times), "samples_ms": times, "requests_per_second": round(len(times) / elapsed, 2), "telemetry_db_reads": telemetry_reads(direct) - before}


def mode(l1, l2):
    os.environ["QUERY_BENCH_L1_BYTES"], os.environ["QUERY_BENCH_L2_BYTES"] = str(l1), str(l2)
    bench.command(*bench.COMPOSE, "up", "-d", "--no-deps", "--force-recreate", "query_gateway")
    bench.wait(lambda: bench.http("/v1/sessions"))
    bench.wait(lambda: bench.http("/v1/stats"), lambda value: value["ring"]["ready"])


def run(report, name, call, cases, expected, args, direct=None):
    result = {"queries": {}}
    report["variants"][name] = result
    for case, body in cases.items():
        result["queries"][case] = measure(call, body, expected[case], args.repeats, direct)
        print(json.dumps({"variant": name, "case": case, **{k:v for k,v in result["queries"][case].items() if k != "samples_ms"}}), flush=True)
    result["concurrent"] = {str(clients): concurrent(call, cases, expected, clients, 10, direct) for clients in args.clients}
    print(json.dumps({"variant": name, "concurrent": {k:v for k,v in result["concurrent"].items() if k != "samples_ms"}}), flush=True)


def grafana_comparison(report, cases, catalog, args):
    plugins = bench.http("/api/plugins", grafana=True)
    kind = next(p["id"] for p in plugins if p["id"] in ("postgres", "grafana-postgresql-datasource"))
    source = bench.http("/api/datasources", {"uid": "artemis-direct-benchmark", "name": "Artemis direct benchmark", "type": kind, "access": "proxy", "url": "db:5432", "user": "analysis", "jsonData": {"database": "artemis", "sslmode": "disable", "postgresVersion": 1700, "timescaledb": True, "maxOpenConns": 4, "maxIdleConns": 4}, "secureJsonData": {"password": "gateway-test-analysis"}}, grafana=True)
    assert source.get("id") or source.get("datasource"), source

    def fetch(body):
        results = bench.http("/api/ds/query", body, grafana=True)["results"]
        observations = []
        for result in results.values():
            assert "error" not in result and result.get("frames"), result
            for frame in result["frames"]:
                fields, values = frame["schema"]["fields"], frame["data"]["values"]
                clock = next(i for i, field in enumerate(fields) if field["type"] == "time")
                for i, field in enumerate(fields):
                    if i != clock:
                        observations.extend((int(stamp), field["name"], value) for stamp, value in zip(values[clock], values[i]) if value is not None)
        return sorted(observations)

    report["grafana_comparison"] = {}
    for name in ("overview", "zoom", "raw"):
        case = cases[name]
        start, end = milliseconds(case["t0"]), milliseconds(case["t1"])
        width = max(case.get("bucket_ms", 0), math.ceil((end - start) / case.get("max_points", 1000)))
        direct = {"from": str(start), "to": str(end), "queries": []}
        for signal in case["signals"]:
            descriptor = next(s for s in catalog["signals"] if s["signal"] == signal)
            table = bench.MODELS[descriptor["pool"]]
            bucket = table.c.time if case["agg"] == "raw" else func.time_bucket(cast(literal(width), BigInteger), table.c.time)
            column = table.c[signal.split(".")[1]]
            column = column if case["agg"] == "raw" else func.max(column)
            statement = select(func.to_timestamp(cast(bucket, Float) / 1000.).label("time"), column.label(signal)).where(table.c.time >= start, table.c.time < end)
            if case["agg"] != "raw":
                statement = statement.group_by(table.c.session_start_ms, bucket)
            statement = statement.order_by(bucket)
            # SQL is generated from the same validated tables and signal selection.
            sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            direct["queries"].append({"refId": str(len(direct["queries"])), "datasource": {"uid": "artemis-direct-benchmark", "type": kind}, "rawSql": sql, "format": "time_series", "maxDataPoints": case.get("max_points", 1000), "intervalMs": width})
        gateway = {"from": str(start), "to": str(end), "queries": [{"refId": "A", "datasource": {"uid": "artemis", "type": "lhre-artemis-datasource"}, "signals": case["signals"], "agg": case["agg"], "bucket_ms": case.get("bucket_ms", 0), "maxDataPoints": case.get("max_points", 1000), "intervalMs": width}]}
        expected = fetch(direct)
        for _ in range(3):
            assert fetch(gateway) == expected, name
            assert fetch(direct) == expected, name
        samples = {"direct": [], "gateway": []}
        for repeat in range(args.repeats):
            for label in (("direct", "gateway") if repeat % 2 == 0 else ("gateway", "direct")):
                began = time.perf_counter()
                rows = fetch(direct if label == "direct" else gateway)
                samples[label].append((time.perf_counter() - began) * 1000)
                assert rows == expected, name
        result = {label: {**bench.quantiles(times), "samples_ms": times} for label, times in samples.items()}
        result["median_speedup"] = round(result["direct"]["p50_ms"] / result["gateway"]["p50_ms"], 3)
        result["observations"] = len(expected)
        result["concurrent"] = {}
        for clients in args.clients:
            timings = {}
            for label, body in (("direct", direct), ("gateway", gateway)):
                def refresh(_):
                    began = time.perf_counter()
                    assert fetch(body) == expected, name
                    return (time.perf_counter() - began) * 1000
                with ThreadPoolExecutor(max_workers=clients) as workers:
                    before = telemetry_reads(None)
                    values = list(workers.map(refresh, range(clients * 5)))
                timings[label] = {**bench.quantiles(values), "gateway_telemetry_reads": telemetry_reads(None) - before}
            result["concurrent"][str(clients)] = timings
        report["grafana_comparison"][name] = result
        print(json.dumps({"grafana_case": name, "direct_ms": result["direct"]["p50_ms"], "gateway_ms": result["gateway"]["p50_ms"], "speedup": result["median_speedup"]}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--clients", type=int, nargs="+", default=[3])
    parser.add_argument("--grafana-only", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.minutes < 4 or args.repeats < 5 or min(args.clients) < 1:
        parser.error("Use at least four minutes, five repeats and one client")
    report = {"started_utc": bench.iso(time.time_ns() // 1_000_000), "host": platform.platform(), "variants": {}, "configuration": {**vars(args), "report": str(args.report)}}
    engine = create_engine("postgresql+psycopg2://electric:gateway-test-password@localhost:35432/artemis")
    metadata = create_engine("postgresql+psycopg2://electric:gateway-test-password@localhost:35432/telemetry")
    direct = None
    try:
        mode(0, 0)
        catalog = bench.http("/v1/signals")
        report["schema_version"] = catalog["schema_version"]
        seconds = args.minutes * 60 // 2
        start = (time.time_ns() // 1_000_000 - 86_400_000) // 60000 * 60000
        starts = [start, start + seconds * 1000 + 360_000]
        counts = bench.seed(engine, catalog["pools"], starts, seconds)
        report["fixture"] = {"rows": sum(counts.values()), "pool_rows": counts, "active_seconds": seconds * 2}
        with engine.begin() as db:
            db.execute(update(bench.ArtemisRecordingSession).values(last_write_ms=time.time_ns() // 1_000_000 - 360_000))
        day = Table("drive_day", MetaData(), autoload_with=metadata)
        end = starts[-1] + seconds * 1000
        with metadata.begin() as db:
            day_id = db.execute(day.insert().values(date=datetime.fromtimestamp(start / 1000, timezone.utc).date(), start_time=start, end_time=end).returning(day.c.day_id)).scalar_one()
        signals = ["controls.apps1_travel", "controls.commanded_torque", "pack.hv_pack_v", "thermal.ambient_temp", "dynamics.bl_wheel_speed"]
        cases = {
            "overview": {"t0": bench.iso(start), "t1": bench.iso(end), "drive_day": day_id, "signals": signals, "agg": "max", "bucket_ms": 1000, "max_points": 1000},
            "zoom": {"t0": bench.iso(start + 60_000), "t1": bench.iso(start + 70_000), "signals": signals, "agg": "max", "bucket_ms": 100, "max_points": 1000},
            "raw": {"t0": bench.iso(start + 60_000), "t1": bench.iso(start + 62_000), "signals": signals[:2], "agg": "raw", "page_size": 10000},
            "all_pools": {"t0": bench.iso(start), "t1": bench.iso(end), "drive_day": day_id, "signals": ["*"], "agg": "last", "max_points": 100},
        }
        report["requests"] = cases
        direct = Direct(catalog)
        expected = {name: digest(direct(body)[0]) for name, body in cases.items()}
        if args.grafana_only:
            mode(256 << 20, 1 << 30)
            gateway_id = bench.command(*bench.COMPOSE, "ps", "-q", "query_gateway")
            probe = "import glob; print(len(glob.glob('/cache/*.json')))"
            bench.wait(lambda: int(bench.command("docker", "exec", gateway_id, "python", "-c", probe)), lambda count: count == len(catalog["pools"]) * 4, timeout=240)
            grafana_comparison(report, cases, catalog, args)
            report["correctness"] = "both Grafana datasources returned identical plotted observations"
            return
        run(report, "direct_timescale_before", direct, cases, expected, args, direct)
        run(report, "gateway_postgres", bench.query, cases, expected, args)
        mode(256 << 20, 0)
        run(report, "gateway_l1", bench.query, cases, expected, args)
        mode(0, 1 << 30)
        gateway_id = bench.command(*bench.COMPOSE, "ps", "-q", "query_gateway")
        probe = "import glob; print(len(glob.glob('/cache/*.json')))"
        bench.wait(lambda: int(bench.command("docker", "exec", gateway_id, "python", "-c", probe)), lambda count: count == len(catalog["pools"]) * 4, timeout=240)
        run(report, "gateway_l2", bench.query, cases, expected, args)
        mode(256 << 20, 1 << 30)
        run(report, "gateway_l1_l2", bench.query, cases, expected, args)
        run(report, "direct_timescale_after", direct, cases, expected, args, direct)
        report["speedups"] = {}
        for name in cases:
            reference = bench.quantiles(report["variants"]["direct_timescale_before"]["queries"][name]["samples_ms"] + report["variants"]["direct_timescale_after"]["queries"][name]["samples_ms"])
            pg = report["variants"]["gateway_postgres"]["queries"][name]
            report["speedups"][name] = {"direct_reference": reference, "variants": {}}
            for variant in ("gateway_postgres", "gateway_l1", "gateway_l2", "gateway_l1_l2"):
                result = report["variants"][variant]["queries"][name]
                report["speedups"][name]["variants"][variant] = {"median_vs_direct": round(reference["p50_ms"] / result["p50_ms"], 3), "median_vs_gateway_postgres": round(pg["p50_ms"] / result["p50_ms"], 3), "p95_vs_gateway_postgres": round(pg["p95_ms"] / result["p95_ms"], 3)}
        report["correctness"] = "all variants returned identical observations"
    except Exception as exc:
        report["error"] = repr(exc)
        raise
    finally:
        if direct:
            direct.close()
        engine.dispose()
        metadata.dispose()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Report: {args.report}", flush=True)


if __name__ == "__main__":
    main()
