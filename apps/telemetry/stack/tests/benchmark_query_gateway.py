"""Repeatable trackside workload against the isolated query Compose stack."""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import heapq
import io
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from kafka import KafkaProducer
from sqlalchemy import MetaData, Table, create_engine, func, select, update

from analysis.sql_utils.models import ARTEMIS_MODELS, ArtemisIngestProgress, ArtemisRecordingSession
from stack.ingest.protobuf import artemis_pb2


ROOT = "http://127.0.0.1:38090"
MODELS = {m.__tablename__: m.__table__ for m in ARTEMIS_MODELS.values()}
COMPOSE_FILE = Path(os.environ.get("QUERY_TEST_COMPOSE", "apps/telemetry/stack/tests/query-gateway.compose.yml"))
COMPOSE = ["docker", "compose", "-p", "query-gateway-test", "-f", str(COMPOSE_FILE), "-f", str(COMPOSE_FILE.with_name("query-gateway.benchmark.yml"))]


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds")


def http(path, body=None, grafana=False):
    root = "http://127.0.0.1:33000" if grafana else ROOT
    token = "Basic YWRtaW46Z2F0ZXdheS10ZXN0LXBhc3N3b3Jk" if grafana else "Bearer gateway-test-token"
    req = urllib.request.Request(root + path, data=None if body is None else json.dumps(body).encode(), headers={"Authorization": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.load(response)


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def wait(operation, predicate=bool, timeout=180):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            value = operation()
            if predicate(value):
                return value
        except Exception as exc:
            error = exc
        time.sleep(.2)
    raise RuntimeError(f"Wait timed out: {error}")


def quantiles(values):
    ordered = sorted(values)
    return {"n": len(values), "p50_ms": round(statistics.median(values), 2), "p95_ms": round(ordered[math.ceil(len(values) * .95) - 1], 2), "max_ms": round(max(values), 2)}


def value(field, index):
    if field["sql_type"] == "boolean":
        return index % 2 == 0
    if field["sql_type"].endswith("[]"):
        return [0., float(index % 100), 3.5]
    return float(index % 100)


def rate(pool):
    # External sources have no CAN period. These are explicit workload assumptions.
    return pool["frequency_hz"] or (10 if pool["group"] == "dynamics" else 1)


def seed(engine, pools, starts, seconds):
    now = time.time_ns() // 1_000_000
    counts = {}
    for start in starts:
        with engine.begin() as db:
            db.execute(ArtemisRecordingSession.__table__.insert().values(session_start_ms=start, start_time=start, end_time=start + seconds * 1000 - 1, data_revision=1, external_revision=1, last_write_ms=now))
        for pool in pools:
            count = round(rate(pool) * seconds)
            fields = pool["fields"]
            with engine.begin() as db:
                for offset in range(0, count, 1000):
                    rows = []
                    for i in range(offset, min(count, offset + 1000)):
                        ns = start * 1_000_000 + int(i * 1_000_000_000 / rate(pool))
                        rows.append({"session_start_ms": start, "time": ns // 1_000_000, "sample_time_ns": ns, **{f["name"]: value(f, i) for f in fields}})
                    db.execute(MODELS[pool["table"]].insert(), rows)
            counts[pool["table"]] = counts.get(pool["table"], 0) + count
    with engine.connect() as db:
        for name, count in counts.items():
            assert db.scalar(select(func.count()).select_from(MODELS[name])) == count, name
    return counts


def query(body):
    rows, sources, pages = [], set(), 0
    request = {"car": "artemis", "page_size": 10000, **body}
    while True:
        page = http("/v1/query", request)
        rows.extend(page["data"])
        sources.update(page["sources"])
        pages += 1
        if not page["next_page_token"]:
            return rows, sources, pages
        request["page_token"] = page["next_page_token"]


def measure(report, name, body, repeats):
    samples, sources, pages = [], set(), []
    for _ in range(repeats):
        began = time.perf_counter()
        rows, used, count = query(body)
        samples.append((time.perf_counter() - began) * 1000)
        sources.update(used)
        pages.append(count)
        assert rows, name
    result = {"name": name, **quantiles(samples), "rows": len(rows), "max_pages": max(pages), "sources": sorted(sources)}
    report["queries"].append(result)
    print(json.dumps(result), flush=True)
    return rows


def export_csv(report, first):
    body = {"car": "artemis", "t0": iso(first + 60_000), "t1": iso(first + 80_000), "signals": ["controls.apps1_travel", "controls.commanded_torque"], "agg": "raw", "page_size": 10000}
    began = time.perf_counter()
    rows, size, pages = 0, 0, 0
    while True:
        req = urllib.request.Request(ROOT + "/v1/query", data=json.dumps(body).encode(), headers={"Authorization": "Bearer gateway-test-token", "Content-Type": "application/json", "Accept": "text/csv"})
        with urllib.request.urlopen(req, timeout=35) as response:
            payload = response.read()
            size += len(payload)
            pages += 1
            rows += len(list(csv.DictReader(io.StringIO(payload.decode()))))
            body["page_token"] = response.headers.get("X-Next-Page-Token")
        if not body["page_token"]:
            break
    assert rows == (333 + 100) * 20, rows
    report["csv_export"] = {"ms": round((time.perf_counter() - began) * 1000, 2), "rows": rows, "bytes": size, "pages": pages}


def live(report, pools, engine, history, clients, seconds):
    producer = KafkaProducer(bootstrap_servers="localhost:39092", acks="all", linger_ms=5)
    start = time.time_ns()
    stop = threading.Event()
    freshness, client_ms, gaps, errors, writer_lag = [], [], [], [], []
    live_query = {"car": "artemis", "signals": ["controls.apps1_travel"], "agg": "raw", "t1": "live", "window_ms": 1000, "page_size": 10000}
    req = urllib.request.Request(ROOT + "/v1/stream?query=" + urllib.parse.quote(json.dumps(live_query)), headers={"Authorization": "Bearer gateway-test-token"})

    def watch():
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                for line in response:
                    if stop.is_set():
                        return
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        if event["type"] == "gap":
                            gaps.append(event["message"])
                        elif event["page"]["data"]:
                            newest = max(int(r["sample_time_ns"]) for r in event["page"]["data"])
                            freshness.append((time.time_ns() - newest) / 1_000_000)
        except Exception as exc:
            if not stop.is_set():
                errors.append(str(exc))

    def browse():
        while not stop.is_set():
            began = time.perf_counter()
            try:
                assert query(history)[0]
                client_ms.append((time.perf_counter() - began) * 1000)
            except Exception as exc:
                errors.append(str(exc))
            stop.wait(.5)

    # Create each pool template from the generated descriptor and catalog.
    templates = []
    for pool in pools:
        update_message = artemis_pb2.TelemetryUpdate()
        group = update_message.values.DESCRIPTOR.fields_by_name[pool["group"]]
        for field in pool["fields"]:
            descriptor = group.message_type.fields_by_name[field["name"]]
            update_message.updated_fields.add(group=group.number, field=descriptor.number)
        templates.append((pool, update_message, group))
    schedule = [(start, i, 0) for i in range(len(pools))]
    heapq.heapify(schedule)
    paused = threading.Event()
    paused_frames = 0
    def pause_writer():
        nonlocal paused_frames
        if stop.wait(seconds / 3):
            return
        command(*COMPOSE, "stop", "db_writer")
        paused.set()
        before = len(freshness)
        stop.wait(min(5, seconds / 3))
        paused_frames = len(freshness) - before
        command(*COMPOSE, "start", "db_writer")
        paused.clear()

    def observe_writer():
        table = MODELS["controls_333hz"]
        while not stop.is_set():
            with engine.connect() as db:
                newest = db.scalar(select(table.c.sample_time_ns).where(table.c.time >= start // 1_000_000).order_by(table.c.time.desc(), table.c.sample_time_ns.desc()).limit(1))
            if newest is not None:
                writer_lag.append({"elapsed_s": round((time.time_ns() - start) / 1_000_000_000, 2), "lag_ms": round((time.time_ns() - newest) / 1_000_000, 2), "paused": paused.is_set()})
            stop.wait(1)

    last_record = None
    publish_lag = 0
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=clients + 3) as workers:
        workers.submit(watch)
        workers.submit(observe_writer)
        fault = workers.submit(pause_writer)
        for _ in range(clients):
            workers.submit(browse)
        try:
            while time.monotonic() - began < seconds:
                batch = artemis_pb2.TelemetryBatch(format_version=1)
                now = time.time_ns()
                publish_lag = max(publish_lag, (now - schedule[0][0]) / 1_000_000)
                while schedule[0][0] <= now and len(batch.updates) < 64:
                    ns, i, sample = heapq.heappop(schedule)
                    pool, template, group = templates[i]
                    item = batch.updates.add()
                    item.CopyFrom(template)
                    item.sample_time_ns, item.values.time = ns, ns // 1_000_000
                    values = getattr(item.values, pool["group"])
                    for field in pool["fields"]:
                        descriptor = group.message_type.fields_by_name[field["name"]]
                        val = value(field, sample)
                        if descriptor.is_repeated:
                            getattr(values, field["name"]).extend(val)
                        else:
                            setattr(values, field["name"], val if descriptor.type in (1, 2, 8) else int(val))
                    heapq.heappush(schedule, (start + int((sample + 1) * 1_000_000_000 / rate(pool)), i, sample + 1))
                if batch.updates:
                    last_record = producer.send("sensor_data", batch.SerializeToString(), headers=[("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1")]).get(timeout=15)
                if schedule[0][0] > time.time_ns():
                    time.sleep(.01)
            producer.flush()
            publish_seconds = time.monotonic() - began
            fault.result(timeout=30)
            finished = time.perf_counter()
            def persisted():
                with engine.connect() as db:
                    return db.scalar(select(ArtemisIngestProgress.next_offset).where(ArtemisIngestProgress.topic == "sensor_data", ArtemisIngestProgress.partition == last_record.partition))
            wait(persisted, lambda n: n is not None and n > last_record.offset)
            lag = (time.perf_counter() - finished) * 1000
            body = {"signals": ["controls.apps1_travel"], "agg": "raw", "t0": iso(start // 1_000_000), "t1": iso(time.time_ns() // 1_000_000 + 1)}
            rows, _, _ = query(body)
            control = next(i for i, (pool, _, _) in enumerate(templates) if pool["table"] == "controls_333hz")
            expected = next(sample for _, i, sample in schedule if i == control)
            assert len(rows) == expected, (len(rows), expected)
            assert len({r["sample_time_ns"] for r in rows}) == expected
            assert all(r["value"] == i % 100 for i, r in enumerate(rows))
            live_counts = {pools[i]["table"]: count for _, i, count in schedule}
            with engine.connect() as db:
                for name, count in live_counts.items():
                    table = MODELS[name]
                    assert db.scalar(select(func.count()).select_from(table).where(table.c.time >= start // 1_000_000)) == count, name
            assert paused_frames > 0 and not gaps and not errors, (paused_frames, gaps, errors)
            steady_lag = [sample["lag_ms"] for sample in writer_lag if seconds - 10 <= sample["elapsed_s"] <= seconds and not sample["paused"]]
            report["live"] = {"seconds": seconds, "clients": clients, "target_rows_per_second": sum(rate(p) for p in pools), "published_rows": sum(live_counts.values()), "published_rows_per_second": round(sum(live_counts.values()) / publish_seconds, 2), "max_publish_lag_ms": round(publish_lag, 2), "last_offset": last_record.offset, "writer_catchup_ms": round(lag, 2), "writer_lag_samples": writer_lag, "writer_lag_last_ten_seconds": quantiles(steady_lag) if steady_lag else None, "freshness": quantiles(freshness), "concurrent_history": quantiles(client_ms), "frames_while_writer_paused": paused_frames, "verified_samples": expected, "gaps": gaps, "errors": errors}
        finally:
            stop.set()
            if paused.is_set():
                command(*COMPOSE, "start", "db_writer")
            producer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--live-seconds", type=int, default=15)
    parser.add_argument("--clients", type=int, default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.minutes < 4 or args.live_seconds < 9 or args.clients < 1:
        parser.error("Use at least four historical minutes, nine live seconds and one client")
    report = {"started_utc": iso(time.time_ns() // 1_000_000), "host": platform.platform(), "synthetic": True, "queries": [], "configuration": vars(args) | {"report": str(args.report)}, "docker": json.loads(command("docker", "info", "--format", '{{json .}}'))}
    # Keep host paths and unrelated Docker configuration out of the report.
    report["docker"] = {k: report["docker"].get(k) for k in ("NCPU", "MemTotal", "Architecture", "ServerVersion")}
    engine = create_engine("postgresql+psycopg2://electric:gateway-test-password@localhost:35432/artemis")
    metadata = create_engine("postgresql+psycopg2://electric:gateway-test-password@localhost:35432/telemetry")
    try:
        catalog = wait(lambda: http("/v1/signals"))
        pools = catalog["pools"]
        report["schema_version"] = catalog["schema_version"]
        seconds = args.minutes * 60 // 2
        first = (time.time_ns() // 1_000_000 - 86_400_000) // 60000 * 60000
        starts = [first, first + seconds * 1000 + 360_000]
        began = time.perf_counter()
        counts = seed(engine, pools, starts, seconds)
        report["fixture"] = {"rows": sum(counts.values()), "pool_rows": counts, "load_seconds": round(time.perf_counter() - began, 2), "recordings": 2, "pit_gap_seconds": 360, "external_rates": {p["table"]: rate(p) for p in pools if not p["frequency_hz"]}}
        print(json.dumps(report["fixture"]), flush=True)
        day = Table("drive_day", MetaData(), autoload_with=metadata)
        end = starts[-1] + seconds * 1000
        with metadata.begin() as db:
            day_id = db.execute(day.insert().values(date=datetime.fromtimestamp(first / 1000, timezone.utc).date(), start_time=first, end_time=end).returning(day.c.day_id)).scalar_one()
        assert len(http("/v1/sessions")["sessions"]) == 2
        signals = ["controls.apps1_travel", "controls.commanded_torque", "pack.hv_pack_v", "thermal.ambient_temp", "dynamics.bl_wheel_speed"]
        assert set(signals) <= {s["signal"] for s in catalog["signals"]}
        overview = {"drive_day": day_id, "signals": signals, "agg": "max", "bucket_ms": 1000, "max_points": 1000}
        cold = measure(report, "overview_first", overview, 1)
        assert {int(r["session_start_ms"]) for r in cold} == set(starts)
        assert not any(starts[0] + seconds * 1000 <= datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp() * 1000 < starts[1] for r in cold)
        measure(report, "overview_repeated", overview, 10)
        zoom = {"t0": iso(first + 60_000), "t1": iso(first + 70_000), "signals": signals, "agg": "max", "bucket_ms": 100, "max_points": 1000}
        measure(report, "zoom_unsealed", zoom, 5)
        measure(report, "all_pools", {"drive_day": day_id, "signals": ["*"], "agg": "last", "max_points": 100}, 3)
        raw = {"t0": iso(first + 60_000), "t1": iso(first + 62_000), "signals": ["controls.apps1_travel"], "agg": "raw", "page_size": 100}
        rows = measure(report, "raw_paged_export", raw, 3)
        assert len(rows) == 666 and all(r["value"] == (19980 + i) % 100 for i, r in enumerate(rows))
        export_csv(report, first)
        with engine.begin() as db:
            db.execute(update(ArtemisRecordingSession).values(last_write_ms=time.time_ns() // 1_000_000 - 360_000))
        gateway_id = command(*COMPOSE, "ps", "-q", "query_gateway")
        file_probe = "import glob,json,os; p=glob.glob('/cache/*.json'); print(json.dumps({'manifests':len(p),'bytes':sum(os.path.getsize(x) for x in glob.glob('/cache/*.arrow'))}))"
        began = time.perf_counter()
        files = wait(lambda: json.loads(command("docker", "exec", gateway_id, "python", "-c", file_probe)), lambda v: v["manifests"] == len(pools) * 4, timeout=240)
        report["seal"] = {**files, "seconds_including_scheduler_wait": round(time.perf_counter() - began, 2)}
        measure(report, "overview_sealed", overview, 10)
        measure(report, "zoom_sealed", zoom, 10)
        # Independent max/mean checks across non-aligned bounds and native rates.
        for agg in ("max", "mean"):
            body = {"t0": iso(first + 60_123), "t1": iso(first + 62_456), "signals": ["controls.apps1_travel"], "agg": agg, "bucket_ms": 1000, "max_points": 1000}
            rows, _, _ = query(body)
            expected = {}
            for i in range(60 * 333, 63 * 333):
                ms = first + i * 1000 // 333
                if first + 60_123 <= ms < first + 62_456:
                    expected.setdefault(ms // 1000 * 1000, []).append(i % 100)
            for row in rows:
                stamp = round(datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).timestamp() * 1000)
                values = expected.pop(stamp)
                assert math.isclose(row["value"], max(values) if agg == "max" else statistics.mean(values), abs_tol=1e-8)
            assert not expected
        grafana = {"from": str(first), "to": str(end), "queries": [{"refId": "A", "datasource": {"uid": "artemis", "type": "lhre-artemis-datasource"}, "signals": signals, "agg": "max", "maxDataPoints": 1000, "intervalMs": 1000}]}
        began = time.perf_counter()
        result = http("/api/ds/query", grafana, grafana=True)["results"]["A"]
        assert "error" not in result and result["frames"]
        report["grafana_ms"] = round((time.perf_counter() - began) * 1000, 2)
        live(report, pools, engine, zoom, args.clients, args.live_seconds)
        report["gateway_stats"] = http("/v1/stats")
        ring_bytes = report["gateway_stats"]["ring"]["bytes"]
        if ring_bytes:
            # The byte cap may already have evicted records during a long run.
            fraction_retained = report["gateway_stats"]["ring"]["records"] / (report["live"]["last_offset"] + 1)
            report["ring_budget_seconds_at_measured_rate"] = round(64 * 1024 * 1024 / ring_bytes * args.live_seconds * fraction_retained, 1)
        with engine.connect() as db:
            report["database_bytes"] = db.scalar(select(func.pg_database_size("artemis")))
        report["docker_stats"] = command("docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}", *command(*COMPOSE, "ps", "-q").split())
        report["image_architectures"] = command("docker", "image", "inspect", *command(*COMPOSE, "config", "--images").split(), "--format", "{{index .RepoTags 0}} {{.Architecture}}").splitlines()
        report["correctness"] = "passed"
    except Exception as exc:
        report["correctness"] = "failed"
        report["error"] = repr(exc)
        raise
    finally:
        engine.dispose()
        metadata.dispose()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Report: {args.report}", flush=True)


if __name__ == "__main__":
    main()
