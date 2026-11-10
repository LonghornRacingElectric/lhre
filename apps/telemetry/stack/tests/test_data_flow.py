"""Artemis MQTT-to-Kafka forwarding and raw-topic consumption."""
import json
import time
import unittest
import uuid

from telemetry.stack.tests.test_utils import TelemetryConfig, MQTTTestClient, KafkaTestClient
from stack.ingest.protobuf import artemis_pb2 as artemis


class TestDataFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = TelemetryConfig.from_env()

    @staticmethod
    def _prime_consumer(consumer, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            consumer.poll(timeout_ms=250)
            if consumer.assignment():
                return
        raise AssertionError("Kafka consumer did not receive an assignment")

    def test_artemis_mqtt_to_kafka(self):
        message = artemis.ArtemisSensorData(time=int(time.time() * 1000))
        message.dynamics.steer_col_angle = 1.25
        message.controls.apps1_v = 0
        batch = artemis.TelemetryBatch(format_version=1, updates=[artemis.TelemetryUpdate(
            sample_time_ns=message.time * 1_000_000 + 1, values=message, updated_fields=[
                artemis.FieldPath(group=3, field=message.dynamics.DESCRIPTOR.fields_by_name["steer_col_angle"].number),
                artemis.FieldPath(group=4, field=message.controls.DESCRIPTOR.fields_by_name["apps1_v"].number),
            ])])
        payload = batch.SerializeToString()
        batch.updates[0].values.time -= 1
        batch.updates[0].sample_time_ns -= 1_000_000
        rejected_payload = batch.SerializeToString()
        raw_client = KafkaTestClient(self.config)
        grafana_client = KafkaTestClient(self.config)
        suffix = uuid.uuid4().hex
        raw = raw_client.create_consumer("sensor_data", group_id=f"raw-{suffix}", auto_offset_reset="latest")
        grafana = grafana_client.create_consumer("grafana_data_artemis", group_id=f"grafana-{suffix}", auto_offset_reset="latest")
        try:
            self._prime_consumer(raw)
            self._prime_consumer(grafana)
            with MQTTTestClient(self.config) as mqtt:
                mqtt.publish("orion/data", rejected_payload, qos=1)
                mqtt.publish("artemis/data", payload, qos=1)
            found_raw = found_grafana = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (found_raw and found_grafana):
                for records in raw.poll(timeout_ms=250).values():
                    for record in records:
                        self.assertNotEqual(record.value, rejected_payload)
                        if record.value == payload:
                            self.assertEqual(dict(record.headers).get("car_type"), b"Artemis")
                            self.assertEqual(dict(record.headers).get("payload_format"), b"artemis_updates_v1")
                            found_raw = True
                for records in grafana.poll(timeout_ms=250).values():
                    for record in records:
                        value = json.loads(record.value)
                        found_grafana |= value.get("time") == message.time and value.get("car_type") == "Artemis"
            self.assertTrue(found_raw, "Artemis raw Kafka record missing")
            self.assertTrue(found_grafana, "Artemis live Kafka record missing")
        finally:
            raw_client.close()
            grafana_client.close()


if __name__ == "__main__":
    unittest.main()
