"""Recent persisted-window parity and Kafka-versus-database visibility."""
import argparse
import json
from pathlib import Path
import threading
import time
import urllib.error

from kafka import KafkaProducer
from sqlalchemy import text
from stack.ingest.protobuf import artemis_pb2

import common as c
import reads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=int, nargs="+", default=[10, 60, 600, 3600])
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--aggregates", nargs="+", choices=["raw", "mean", "max"], default=["raw", "mean", "max"])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if min(args.windows) <= 0 or args.clients <= 0:
        parser.error("Positive windows and clients are required")
    report = {"configuration": {**vars(args), "report": str(args.report)}, "results": [], "notes": ["Recent queries use the DB baseline plus available Kafka coverage; a one-hour range cannot fit wholly in the 15-minute ring.", "Persisted-window reads use identical fixtures in both databases and the gateway.", "Freshness compares the actual Timescale writer with gateway visibility; plain Postgres has no Kafka writer in this stack.", "Freshness timings include polling and request cost; not instrumented at commit."]}
    direct = []
    try:
        c.gateway_mode(0, 0)
        report["environment"] = c.environment()
        catalog = c.http("/v1/signals")
        end = int(time.time() * 1000) // 1000 * 1000
        start = end - max(args.windows) * 1000
        reads.seed(catalog, start, max(args.windows), report)
        direct = [reads.Direct(catalog, c.PLAIN, False), reads.Direct(catalog, c.READ, True)]
        for window in args.windows:
            for aggregate in args.aggregates:
                body = {"t0": reads.iso(end - window * 1000), "t1": reads.iso(end), "signals": reads.SIGNALS, "agg": aggregate}
                if aggregate != "raw":
                    body["max_points"] = 1000
                expected = direct[0](body)
                for label, call in (("postgres", direct[0]), ("timescale", direct[1]), ("gateway_baseline_ring", c.gateway_read)):
                    # Restart immediately before each gateway case to measure a baseline miss.
                    if label == "gateway_baseline_ring":
                        c.gateway_mode(0, 0)
                    record = {"variant": label, "window_seconds": window, "aggregate": aggregate, "request": body, "expected": expected}
                    record["first"] = reads.measure(call, body, expected, 1)
                    if label == "gateway_baseline_ring":
                        c.gateway_mode(0, 0)
                    record["cold_burst"] = reads.measure(call, body, expected, args.clients)
                    record["warm_burst"] = reads.measure(call, body, expected, args.clients)
                    report["results"].append(record)
                    c.save(report, args.report)
                    print(json.dumps({"variant": label, "window": window, "agg": aggregate, "first_ms": record["first"].get("p50_ms"), "warm_ms": record["warm_burst"].get("p50_ms")}), flush=True)
        # Publish sequential probes only after prior probes persist, keeping identity stable.
        producer = KafkaProducer(bootstrap_servers="127.0.0.1:39092", acks="all")
        database = c.engine(c.ADMIN)
        report["freshness"] = []
        try:
            for i in range(20):
                stamp = time.time_ns()
                batch = artemis_pb2.TelemetryBatch(format_version=1)
                update = batch.updates.add(sample_time_ns=stamp)
                update.values.time = stamp // 1_000_000
                update.values.controls.apps1_travel = float(i + 1)
                group = update.values.DESCRIPTOR.fields_by_name["controls"]
                field = group.message_type.fields_by_name["apps1_travel"]
                update.updated_fields.add(group=group.number, field=field.number)
                began = time.perf_counter()
                producer.send("sensor_data", batch.SerializeToString(), headers=[("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1")]).get(timeout=30)
                observed, errors = {}, []
                transient_errors = []

                def poll(source):
                    try:
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            if source == "timescale":
                                with database.connect() as db:
                                    found = db.scalar(text('SELECT EXISTS (SELECT 1 FROM controls_333hz WHERE "time" = :ms AND sample_time_ns = :ns)'), {"ms": stamp // 1_000_000, "ns": stamp})
                            else:
                                try:
                                    page = c.http("/v1/query", {"car": "artemis", "signals": ["controls.apps1_travel"], "agg": "raw", "t0": reads.iso(stamp // 1_000_000), "t1": reads.iso(stamp // 1_000_000 + 1)})
                                except urllib.error.HTTPError as exc:
                                    if exc.code != 503:
                                        raise
                                    transient_errors.append(exc.read().decode()[:1000])
                                    time.sleep(.01)
                                    continue
                                found = any(row["sample_time_ns"] == str(stamp) for row in page["data"])
                            if found:
                                if source == "gateway":
                                    observed["gateway_sources"] = page["sources"]
                                    observed["gateway_provisional"] = page["provisional"]
                                observed[source + "_ms"] = (time.perf_counter() - began) * 1000
                                return
                            time.sleep(.01)
                        raise TimeoutError(source)
                    except Exception as exc:
                        errors.append(str(exc))

                workers = [threading.Thread(target=poll, args=(s,)) for s in ("timescale", "gateway")]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join()
                report["freshness"].append({"sample_time_ns": str(stamp), **observed, "errors": errors, "transient_errors": transient_errors})
                c.save(report, args.report)
        finally:
            producer.close()
            database.dispose()
        report["resources"] = c.resources()
        failures = any(r[s]["errors"] for r in report["results"] for s in ("first", "cold_burst", "warm_burst")) or any(p["errors"] for p in report["freshness"])
        report["correctness"] = "failed" if failures else "passed"
        if failures:
            raise AssertionError("Recent read or freshness failures recorded")
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        for reader in direct:
            reader.engine.dispose()
        c.save(report, args.report)


if __name__ == "__main__":
    main()
