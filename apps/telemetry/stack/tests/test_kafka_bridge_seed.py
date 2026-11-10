"""Verify the Kafka bridge publishes its Artemis Grafana seed."""

import json
import time
import unittest
import uuid

from telemetry.stack.tests.test_utils import (
    TelemetryConfig,
    KafkaTestClient,
    check_kafka_connection,
    wait_for_service,
)


class TestKafkaBridgeSeed(unittest.TestCase):
    """Validate Kafka bridge seedTopic behavior."""

    @classmethod
    def setUpClass(cls):
        cls.config = TelemetryConfig.from_env()
        # Ensure Kafka is reachable before consuming
        wait_for_service(
            lambda: check_kafka_connection(cls.config.kafka_host, cls.config.kafka_port),
            "Kafka Broker",
            timeout=60,
        )

    def test_grafana_topics_seeded(self):
        cars = ["artemis"]
        suffix = uuid.uuid4().hex

        for car in cars:
            client = KafkaTestClient(self.config)
            topic = f"grafana_data_{car}"
            consumer = client.create_consumer(
                topic,
                group_id=f"bridge-seed-test-{car}-{suffix}",
                auto_offset_reset="earliest",
            )

            try:
                seed_found = False
                start = time.time()
                while time.time() - start < 15:
                    batch = consumer.poll(timeout_ms=1000)
                    for _, records in batch.items():
                        for record in records:
                            try:
                                payload = json.loads(record.value.decode("utf-8"))
                            except Exception:
                                continue

                            if payload.get("car_type") == "Artemis" and payload.get("time", 0) > 0:
                                seed_found = True
                                break
                        if seed_found:
                            break
                    if seed_found:
                        break

                self.assertTrue(seed_found, f"Kafka bridge should seed {topic} at startup")
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
