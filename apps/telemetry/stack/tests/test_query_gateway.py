"""Exercise the shared API with actual Kafka, writer, Timescale and Grafana."""
import base64
import json
import os
import subprocess
import time
import unittest
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from kafka import KafkaProducer
from sqlalchemy import create_engine, select, MetaData, Table
from sqlalchemy.orm import Session

from analysis.sql_utils.models import ArtemisIngestProgress
from stack.ingest.protobuf import artemis_pb2


def iso(stamp):
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds")


def call(path, body=None, grafana=False):
    base = "http://127.0.0.1:33000" if grafana else "http://127.0.0.1:38090"
    auth = "Basic " + base64.b64encode(b"admin:gateway-test-password").decode() if grafana else "Bearer gateway-test-token"
    request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers={"Authorization": auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=35) as response:
        return json.load(response)


class QueryGatewayTest(unittest.TestCase):
    def wait_for(self, operation, predicate=lambda value: bool(value), timeout=45):
        deadline = time.monotonic() + timeout
        error = None
        while time.monotonic() < deadline:
            try:
                result = operation()
                if predicate(result):
                    return result
            except Exception as exc:
                error = exc
            time.sleep(.2)
        self.fail(f"Timed out: {error}")

    def test_kafka_tail_persistence_paging_and_grafana(self):
        self.wait_for(lambda: call("/v1/signals"))
        producer = KafkaProducer(bootstrap_servers="localhost:39092", acks="all")
        engine = create_engine("postgresql+psycopg2://electric:gateway-test-password@localhost:35432/artemis")
        stamp = time.time_ns() // 1_000_000 // 1000 * 1000 - 2000

        def send(at, group, field, value):
            batch = artemis_pb2.TelemetryBatch(format_version=1)
            update = batch.updates.add(sample_time_ns=at * 1_000_000)
            update.values.time = at
            group_descriptor = update.values.DESCRIPTOR.fields_by_name[group]
            field_descriptor = group_descriptor.message_type.fields_by_name[field]
            if isinstance(value, list):
                getattr(getattr(update.values, group), field).extend(value)
            else:
                setattr(getattr(update.values, group), field, value)
            update.updated_fields.add(group=group_descriptor.number, field=field_descriptor.number)
            return producer.send("sensor_data", batch.SerializeToString(), headers=[("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1")]).get(timeout=15)

        def persisted(record):
            with Session(engine) as db:
                return (db.scalar(select(ArtemisIngestProgress.next_offset).where(ArtemisIngestProgress.topic == "sensor_data", ArtemisIngestProgress.partition == record.partition)) or 0) > record.offset

        query = {"car": "artemis", "signals": ["controls.apps1_travel", "pack.hv_pack_v"], "t0": iso(stamp - 1), "t1": iso(stamp + 1001), "agg": "raw", "page_size": 1000}
        try:
            first = send(stamp, "controls", "apps1_travel", 0)
            first = send(stamp, "controls", "enable", False)
            self.wait_for(lambda: persisted(first))
            self.wait_for(lambda: call("/v1/query", query), lambda p: len(p["data"]) == 1)
            compose = ["docker", "compose", "-p", "query-gateway-test", "-f", os.environ["QUERY_TEST_COMPOSE"]]
            def control(action):
                result = subprocess.run([*compose, action, "db_writer"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            control("stop")
            try:
                before = call("/v1/stats")["reads"]["postgres"]
                send(stamp, "controls", "apps1_travel", 0)
                send(stamp + 10, "controls", "apps1_travel", 80)
                last = send(stamp + 20, "pack", "hv_pack_v", 420)
                last = send(stamp + 20, "pack", "cells_v", [0.0, 3.4])
                live = self.wait_for(lambda: call("/v1/query", query), lambda p: len(p["data"]) == 3)
                self.assertEqual([r["value"] for r in live["data"]], [0, 80, 420])
                self.assertIn("ring", live["sources"])
                self.assertEqual(call("/v1/stats")["reads"]["postgres"], before)
                wide = call("/v1/query", {**query, "signals": ["controls.apps1_travel"], "t0": iso(stamp - 180_000), "agg": "mean", "bucket_ms": 60000})
                self.assertEqual(wide["data"][0]["value"], 40)
            finally:
                control("start")
            self.wait_for(lambda: persisted(last))
            self.assertEqual(len(call("/v1/query", query)["data"]), 3)
            small = {**query, "page_size": 1}
            rows = []
            while True:
                page = call("/v1/query", small)
                rows.extend(page["data"])
                if page["next_page_token"] is None:
                    break
                small["page_token"] = page["next_page_token"]
            self.assertEqual(len(rows), 3)
            metadata = create_engine("postgresql+psycopg2://electric:gateway-test-password@localhost:35432/telemetry")
            try:
                day = Table("drive_day", MetaData(), autoload_with=metadata)
                with metadata.begin() as connection:
                    day_id = connection.execute(day.insert().values(date=datetime.now(timezone.utc).date(), start_time=stamp - 1, end_time=stamp + 1001).returning(day.c.day_id)).scalar_one()
                by_day = call("/v1/query", {"car": "artemis", "drive_day": day_id, "pools": [next(p["table"] for p in call("/v1/signals")["pools"] if p["group"] == "controls" and any(f["name"] == "apps1_travel" for f in p["fields"]))], "agg": "raw"})
                self.assertEqual(len(by_day["data"]), 3)
                values = call("/v1/query", {**query, "signals": ["controls.enable", "pack.cells_v"]})["data"]
                self.assertIs(next(r["value"] for r in values if r["signal"] == "controls.enable"), False)
                self.assertEqual(next(r["value"] for r in values if r["signal"] == "pack.cells_v")[1], 3.4)
            finally:
                metadata.dispose()
            maximum = call("/v1/query", {**query, "agg": "max", "bucket_ms": 1000})
            self.assertEqual(next(r["value"] for r in maximum["data"] if r["signal"] == "controls.apps1_travel"), 80)
            self.wait_for(lambda: call("/api/datasources/uid/artemis/health", grafana=True), lambda p: p.get("status") == "OK")
            frames = call("/api/ds/query", {"from": str(stamp - 1), "to": str(stamp + 1001), "queries": [{"refId": "A", "datasource": {"uid": "artemis", "type": "lhre-artemis-datasource"}, "signals": ["controls.apps1_travel"], "agg": "max", "bucket_ms": 1000, "maxDataPoints": 1000, "intervalMs": 1000}]}, grafana=True)
            result = frames["results"]["A"]
            self.assertNotIn("error", result, result)
            self.assertIn(80, result["frames"][0]["data"]["values"][1])
            stream_query = urllib.parse.quote(json.dumps(query))
            request = urllib.request.Request("http://127.0.0.1:38090/v1/stream?query=" + stream_query, headers={"Authorization": "Bearer gateway-test-token"})
            with urllib.request.urlopen(request, timeout=15) as response:
                event = json.loads(response.readline().decode().removeprefix("data: "))
                self.assertEqual(len(event["page"]["data"]), 3)
        finally:
            producer.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
