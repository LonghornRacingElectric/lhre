"""Measure actual legacy and current MQTT-to-database pipelines without readers."""
import argparse
from datetime import datetime, timezone
import random
import heapq
import json
import os
from pathlib import Path
import threading
import time

from paho.mqtt import client as mqtt
from psycopg2 import sql

import common as c
import traffic
from metrics import Timeline


def kafka_offsets(service):
    container = c.compose("ps", "-q", service)
    lines = c.command("docker", "exec", container, "/opt/kafka/bin/kafka-get-offsets.sh", "--bootstrap-server", "localhost:9092", "--topic", "sensor_data").splitlines()
    return sum(int(line.rsplit(":", 1)[1]) for line in lines if line.startswith("sensor_data:"))


def run_case(pipeline, pools, seconds, multiplier, legacy, timeout, checkpoint=None):
    c.compose("stop", "ingest", "kafka-bridge", "legacy_ingest", "legacy_bridge", "db_writer", "legacy_kafka")
    launched_at = datetime.now(timezone.utc).isoformat()
    if pipeline == "current":
        c.compose("up", "-d", "--wait", "kafka")
        c.compose("up", "-d", "db_writer", "kafka-bridge", "ingest")
        service = "ingest"
    else:
        c.compose("stop", "kafka")
        c.compose("up", "-d", "--wait", "legacy_kafka")
        c.compose("up", "-d", "legacy_bridge", "legacy_ingest")
        service = "legacy_ingest"
    c.wait(lambda: c.compose("logs", "--since", launched_at, service), lambda value: "connected to Mosquitto" in value)
    bridge = "kafka-bridge" if pipeline == "current" else "legacy_bridge"
    broker = "kafka" if pipeline == "current" else "legacy_kafka"
    c.wait(lambda: c.compose("logs", "--since", launched_at, bridge), lambda value: "gRPC server listening" in value)
    offset_before = kafka_offsets(broker)
    time.sleep(.25)
    url = c.ADMIN if pipeline == "current" else c.ADMIN.rsplit("/", 1)[0] + "/orion"
    database = c.engine(url)
    started = time.time_ns()
    origin = time.monotonic()
    pending, lock = {}, threading.Lock()
    stop = threading.Event()
    errors, latencies, backlog = [], [], []
    persisted = [0] * len(pools)
    sent = [0] * len(pools)
    last = [0] * len(pools)
    max_backlog = 0
    latency_count = 0
    rng = random.Random(0)
    resource_samples = []
    timeline = Timeline(10)
    events = []
    broker_metrics = {}
    mqtt_acked = [0]
    broker_lock = threading.Lock()

    def sample_resources():
        while not stop.is_set():
            try:
                state_path = Path(os.environ["PDR_STATE_DIR"]) / ("current.json" if pipeline == "current" else "legacy.json")
                state = json.loads(state_path.read_text()) if state_path.exists() else None
                if state and state["observed_at_ms"] < started // 1000000 - 2000:
                    state = None
                with broker_lock:
                    stats = dict(broker_metrics)
                with lock:
                    point = {"elapsed_s": time.monotonic() - origin, "pending": len(pending),
                             "sent": sum(sent), "mqtt_acked": mqtt_acked[0], "persisted": sum(persisted), "ingest": state,
                             "mqtt_broker": stats, "publisher_outgoing": len(client._out_messages)}
                    backlog.append(point)
                    snapshot = {"pipeline": pipeline, "duration_s": seconds,
                                "target_updates_per_second": sum(p["frequency_hz"] for p in pools) * multiplier,
                                "sent": sum(sent), "persisted": sum(persisted), "missing": len(pending),
                                "timeline": timeline.snapshot(), "backlog": list(backlog), "events": list(events), "errors": list(errors)}
                if checkpoint and int(point["elapsed_s"]) % 10 == 0:
                    checkpoint(snapshot)
                if int(point["elapsed_s"]) % 10 == 0:
                    ids = [c.compose("ps", "-a", "-q", name) for name in (service, bridge, "db_writer" if pipeline == "current" else broker)]
                    for identity in ids:
                        status = json.loads(c.command("docker", "inspect", "--format", "{{json .State}}", identity))
                        if not status["Running"]:
                            with lock:
                                events.append({"elapsed_s": point["elapsed_s"], "kind": "process_exit", "container": identity, "exit_code": status["ExitCode"], "oom_killed": status["OOMKilled"]})
                                errors.append("Pipeline process exited; see events")
                            return
            except Exception as exc:
                with lock:
                    events.append({"elapsed_s": time.monotonic() - origin, "kind": "monitor_error", "message": str(exc)})
            stop.wait(1)

    def observe():
        nonlocal latency_count
        try:
            while not stop.is_set():
                with database.connect() as db:
                    cursor = db.connection.cursor()
                    for i, pool in enumerate(pools):
                        fields = [f["name"] for f in pool["fields"]]
                        if pipeline == "current":
                            statement = sql.SQL('SELECT sample_time_ns, {} FROM {} WHERE "time" >= %s AND sample_time_ns > %s ORDER BY sample_time_ns').format(sql.SQL(", ").join(map(sql.Identifier, fields)), sql.Identifier(pool["table"]))
                        else:
                            statement = sql.SQL('SELECT p.packet_id, {} FROM packet p JOIN {} s USING (packet_id) WHERE p."time" >= %s AND p.packet_id > %s ORDER BY p.packet_id').format(sql.SQL(", ").join(sql.SQL("s.{}").format(sql.Identifier(f)) for f in fields), sql.Identifier(pool["group"]))
                        lower_ms = max(started // 1_000_000, last[i] // 1_000_000) if pipeline == "current" else started // 1_000_000
                        cursor.execute(statement, (lower_ms, last[i]))
                        rows = cursor.fetchall()
                        observed = time.monotonic()
                        with lock:
                            for row in rows:
                                identity, values = row[0], dict(zip(fields, row[1:]))
                                # Another pool can share an Orion group. Track by sent identity.
                                entry = pending.pop((i, identity), None)
                                last[i] = max(last[i], identity)
                                if entry is None:
                                    continue
                                sent_at, sample_index = entry
                                expected = traffic.expected_values(pool, sample_index)
                                if values != expected:
                                    errors.append(f"Wrong value in {pool['table']} at {identity}")
                                persisted[i] += 1
                                latency_count += 1
                                timeline.commit(sent_at - origin, (observed - sent_at) * 1000)
                                latency = (observed - sent_at) * 1000
                                if len(latencies) < 100000:
                                    latencies.append(latency)
                                else:
                                    slot = rng.randrange(latency_count)
                                    if slot < len(latencies):
                                        latencies[slot] = latency
                    cursor.close()
                stop.wait(.05)
        except Exception as exc:
            errors.append(str(exc))

    client = mqtt.Client(client_id="pdr-producer")
    connected = threading.Event()
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("$SYS/#")
            connected.set()
    def broker_message(client, userdata, message):
        if not any(word in message.topic for word in ("dropped", "disconnected", "stored", "store/messages", "publish/messages")):
            return
        with broker_lock:
            broker_metrics[message.topic] = message.payload.decode(errors="replace")
    def disconnected(client, userdata, rc):
        if not stop.is_set():
            with lock:
                events.append({"elapsed_s": time.monotonic() - origin, "kind": "publisher_disconnect", "reason": rc})
    def acknowledged(client, userdata, mid):
        with lock:
            mqtt_acked[0] += 1
    client.on_connect, client.on_message, client.on_disconnect = on_connect, broker_message, disconnected
    client.on_publish = acknowledged
    client.max_inflight_messages_set(1000)
    client.max_queued_messages_set(10000)
    client.connect("localhost", 31883)
    client.loop_start()
    if not connected.wait(20):
        raise TimeoutError("Producer MQTT connection failed")
    worker = threading.Thread(target=observe)
    worker.start()
    sampler = threading.Thread(target=sample_resources)
    sampler.start()
    schedule = [(0., i, 0) for i in range(len(pools))]
    heapq.heapify(schedule)
    packet_id = 1
    # Distinct packet IDs across cases, all within the signed bigint range.
    packet_base = started // 1000
    generator_lag = 0
    sent_bytes = 0
    last_publish = None
    try:
        while schedule[0][0] < seconds:
            due, i, sample = heapq.heappop(schedule)
            delay = origin + due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            generator_lag = max(generator_lag, (time.monotonic() - origin - due) * 1000)
            pool = pools[i]
            ns = started + int(due * 1e9)
            identity = ns if pipeline == "current" else packet_base + packet_id
            payload, value = traffic.encode(pool, ns, sample, identity, legacy if pipeline == "legacy" else None)
            with lock:
                pending[(i, identity)] = (time.monotonic(), sample)
                timeline.send(time.monotonic() - origin)
                max_backlog = max(max_backlog, len(pending))
            result = client.publish("artemis/data" if pipeline == "current" else "orion/data", payload, qos=1)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                with lock:
                    pending.pop((i, identity), None)
                errors.append(f"Generator MQTT publish failed: {result.rc}")
                break
            last_publish = result
            sent[i] += 1
            sent_bytes += len(payload)
            packet_id += 1
            heapq.heappush(schedule, ((sample + 1) / (pool["frequency_hz"] * multiplier), i, sample + 1))
            if len(pending) > 1000000:
                errors.append("Backlog exceeded 1000000 observations; load is unsustainable")
                break
            if errors or time.monotonic() - origin > seconds + 1:
                break
        if last_publish:
            last_publish.wait_for_publish(timeout=30)
            if not last_publish.is_published():
                errors.append("Generator final MQTT acknowledgement timed out")
        finished = time.monotonic()
        with lock:
            pending_at_publish_end = len(pending)
        deadline = finished + timeout
        while time.monotonic() < deadline:
            with lock:
                remaining = len(pending)
            if not remaining or errors:
                break
            time.sleep(.05)
        with lock:
            remaining = len(pending)
        result = {"pipeline": pipeline, "duration_s": seconds, "multiplier": multiplier, "target_updates_per_second": sum(p["frequency_hz"] for p in pools) * multiplier, "sent": sum(sent), "persisted": sum(persisted), "missing": remaining, "payload_bytes": sent_bytes, "publish_elapsed_s": finished - origin, "publish_rate": sum(sent) / (finished - origin), "generator_max_lag_ms": generator_lag, "commit_visibility_latency": c.quantiles(latencies), "latency_sampling": "deterministic reservoir of at most 100000 observations across the trial; polled every 50ms plus scan time", "catchup_ms": (time.monotonic() - finished) * 1000, "max_pending": max_backlog, "pending_at_publish_end": pending_at_publish_end, "backlog": backlog, "timeline": timeline.snapshot(), "events": events, "errors": errors, "pool_counts": {p["table"]: {"sent": sent[i], "persisted": persisted[i]} for i, p in enumerate(pools)}}
        # Verify final stored cardinality independently of the visibility observer.
        with database.connect() as db:
            cursor = db.connection.cursor()
            duplicates = 0
            for i, pool in enumerate(pools):
                if pipeline == "current":
                    statement = sql.SQL('SELECT count(*), count(DISTINCT sample_time_ns) FROM {} WHERE "time" >= %s AND {} IS NOT NULL').format(sql.Identifier(pool["table"]), sql.Identifier(pool["fields"][0]["name"]))
                else:
                    marker = pool["fields"][0]
                    column = sql.SQL("s.{}").format(sql.Identifier(marker["name"]))
                    present = sql.SQL("cardinality({}) > 0").format(column) if marker["sql_type"].endswith("[]") else sql.SQL("{} IS TRUE").format(column) if marker["sql_type"] == "boolean" else sql.SQL("{} <> 0").format(column)
                    statement = sql.SQL('SELECT count(*), count(DISTINCT p.packet_id) FROM packet p JOIN {} s USING (packet_id) WHERE p."time" >= %s AND {}').format(sql.Identifier(pool["group"]), present)
                cursor.execute(statement, (started // 1_000_000,))
                count, unique = cursor.fetchone()
                duplicates += count - unique
                if count != sent[i]:
                    errors.append(f"Stored count mismatch for {pool['table']}: {count} != {sent[i]}")
            cursor.close()
        result["duplicates"] = duplicates
        kafka_deadline = time.monotonic() + timeout
        while True:
            kafka_records = kafka_offsets(broker) - offset_before
            if kafka_records >= sum(sent) or time.monotonic() >= kafka_deadline:
                break
            time.sleep(1)
        result["kafka_records"] = kafka_records
        if kafka_records != sum(sent):
            errors.append(f"Kafka record mismatch: {kafka_records} != {sum(sent)}")
        if errors or remaining:
            result["service_errors"] = [line[:1000] for line in c.compose("logs", "--since", launched_at, service, "db_writer").splitlines() if "ERROR" in line][-20:]
        result["resources_during_ingest"] = resource_samples
        result["resources_after_ingest"] = c.resources()
        result["sustainable"] = not errors and not remaining and not duplicates and generator_lag < 1000 and finished - origin <= seconds + 1 and pending_at_publish_end <= 500 + 2 * result["target_updates_per_second"]
        result["capacity_status"] = "generator_limited" if generator_lag >= 1000 or any("Generator" in error for error in errors) else "sustained" if result["sustainable"] else "pipeline_limited"
        return result
    finally:
        stop.set()
        worker.join(timeout=10)
        sampler.join(timeout=10)
        client.disconnect()
        client.loop_stop()
        database.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--legacy-fix-diagnostics", action="store_true", help="Explicitly benchmark a labeled copy with the missing Orion diagnostic model restored")
    parser.add_argument("--prepare-only", action="store_true", help="Build/hash legacy images without starting workloads")
    parser.add_argument("--durations", type=int, nargs="+", default=[1800])
    parser.add_argument("--pipelines", nargs="+", choices=["legacy", "current"], default=["legacy", "current"])
    parser.add_argument("--rates", type=int, nargs="+", default=[10, 100, 333])

    parser.add_argument("--drain-timeout", type=int, default=60)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if min(args.durations + args.rates + [args.drain_timeout]) <= 0:
        parser.error("Durations, rates and multipliers must be positive")
    report = {"pipeline_defaults": {"legacy_flush_jobs": 40, "legacy_idle_flush_seconds": 5, "current_batch_packets": 500, "current_batch_seconds": .25, "mqtt_qos": 1}, "results": [], "notes": ["Actual old Orion and current Artemis code/schema: whole-pipeline comparison.", "All fields shared by each selected native pool and the legacy schema; nonzero scalars, three-element arrays and true booleans; one update per MQTT message, QoS 1.", "Commit visibility is polled, not instrumented at the transaction commit.", "No read workloads run; the observer queries are measurement overhead.", "Sustainable means no errors/loss/duplicates, generator lag under 1s and end-of-publication backlog no larger than 500 + two seconds of offered updates."]}
    try:
        report["legacy"] = traffic.build_legacy(args.legacy_root, args.legacy_fix_diagnostics)
        if args.prepare_only:
            report["completed"] = True
            return
        os.environ["PDR_LEGACY_INGEST_IMAGE"] = report["legacy"]["ingest_image"]
        traffic.initialize_legacy(args.legacy_root)
        legacy = traffic.legacy_message(args.legacy_root)
        report["environment"] = c.environment()
        catalog = c.http("/v1/signals")
        report["schema_version"] = catalog["schema_version"]
        native, excluded = traffic.comparable_pools(catalog, legacy)
        report["native_pools"], report["excluded_unmatched_pools"] = native, excluded
        fixed = next(p for p in native if p["table"] == "controls_333hz")
        report["configuration"] = {**vars(args), "legacy_root": str(args.legacy_root), "report": str(args.report)}
        c.compose("stop", "query_gateway")
        for duration in args.durations:
            cases = [(f"fixed_{rate}", [{**fixed, "frequency_hz": rate}], 1) for rate in args.rates]
            for name, pools, multiplier in cases:
                for pipeline in args.pipelines:
                    active = args.report.with_suffix(".active.json")
                    report["active_report"] = str(active)
                    c.save(report, args.report)
                    def checkpoint(snapshot):
                        snapshot["case"] = name
                        c.save(snapshot, active)
                    result = run_case(pipeline, pools, duration, multiplier, legacy, args.drain_timeout, checkpoint)
                    if pipeline == "legacy" and args.legacy_fix_diagnostics:
                        result["pipeline"] = "legacy_diagnostics_fixed"
                    result["case"] = name
                    c.save(result, active)
                    report["results"].append(result)
                    c.save(report, args.report)
                    print(json.dumps({k: v for k, v in result.items() if k not in ("backlog", "timeline", "resources_during_ingest", "resources_after_ingest")}), flush=True)
        report["completed"] = True
        report["correctness"] = "failed" if any((r["errors"] or r["missing"] or r["duplicates"]) for r in report["results"]) else "passed"
        if report["correctness"] == "failed":
            raise AssertionError("Ingest correctness failures recorded")
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        c.save(report, args.report)


if __name__ == "__main__":
    main()
