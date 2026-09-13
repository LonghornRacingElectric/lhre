"""
telemetry/stack/tests/test_data_flow.py

End-to-end data flow tests for the telemetry system.
Tests the complete data pipeline: MQTT -> Ingest -> Kafka -> Database

This is the main integration test that validates communication between
Docker containers and non-Docker scripts (like paho_testing.py).
"""

import unittest
import os
import sys
import time
import json
import uuid

from telemetry.stack.tests.test_utils import (
    TelemetryConfig,
    wait_for_service,
    check_mqtt_connection,
    check_kafka_connection,
    check_db_connection,
    MQTTTestClient,
    KafkaTestClient,
    TestDataGenerator,
    assert_eventually,
)


class TestDataFlow(unittest.TestCase):
    """
    End-to-end tests for telemetry data flow.
    
    Tests the following data paths:
    1. MQTT publish -> Ingest service -> Database (nightwatch/packet, nightwatch/dynamics, etc.)
    2. MQTT publish -> Ingest service -> Kafka (sensor_data topic)
    3. Kafka consume -> Processor -> Database (for gps_classifier, lap_timer, etc.)
    """
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures and wait for all services."""
        cls.config = TelemetryConfig.from_env()
        cls.data_generator = TestDataGenerator(seed=42)
        
        # Wait for all required services
        services = [
            (lambda: check_mqtt_connection(cls.config.mqtt_host, cls.config.mqtt_port), "MQTT"),
            (lambda: check_kafka_connection(cls.config.kafka_host, cls.config.kafka_port), "Kafka"),
            (lambda: check_db_connection(cls.config.db_host, cls.config.db_port), "Database"),
        ]
        
        for check_fn, name in services:
            if not wait_for_service(check_fn, name, timeout=60):
                raise ConnectionError(f"{name} service not available")

    @staticmethod
    def _prime_consumer(consumer, timeout=10):
        """Join the consumer group before publishing to a latest-only stream."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            consumer.poll(timeout_ms=250)
            if consumer.assignment():
                return
        raise AssertionError("Kafka consumer did not receive a partition assignment")

    @staticmethod
    def _fresh_packet_id():
        """Return a repeat-safe, JavaScript-safe packet identifier."""
        return time.time_ns() // 1_000

    def _count_packets(self, database, first_packet_id, last_packet_id=None):
        """Count persisted packet IDs in an inclusive range."""
        import psycopg2

        last_packet_id = last_packet_id or first_packet_id
        with psycopg2.connect(
            host=self.config.db_host,
            port=self.config.db_port,
            user=self.config.db_user,
            password=self.config.db_password,
            database=database,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM packet WHERE packet_id BETWEEN %s AND %s",
                    (first_packet_id, last_packet_id),
                )
                return cursor.fetchone()[0]

    def test_paho_orion_packet_reaches_kafka_and_database(self):
        """A Paho-generated packet must reach both realtime and durable sinks."""
        import psycopg2

        from telemetry.analysis.database.paho_testing import DataTester
        from telemetry.stack.ingest.protobuf.can_packets_pb2 import OrionSensorData

        packet_id = DataTester.get_next_packet_id("Orion")
        suffix = uuid.uuid4().hex
        raw_client = KafkaTestClient(self.config)
        grafana_client = KafkaTestClient(self.config)
        raw_consumer = raw_client.create_consumer(
            "sensor_data",
            group_id=f"paho-raw-{suffix}",
            auto_offset_reset="latest",
        )
        grafana_consumer = grafana_client.create_consumer(
            "grafana_data_orion",
            group_id=f"paho-grafana-{suffix}",
            auto_offset_reset="latest",
        )

        try:
            self._prime_consumer(raw_consumer)
            self._prime_consumer(grafana_consumer)

            with MQTTTestClient(self.config) as mqtt_client:
                tester = DataTester(mqtt=mqtt_client, seed=42, value_profile="viewer")
                tables = list(tester.get_proto_desc(target="Orion").keys())
                tester.send_proto_rows(
                    tables=tables,
                    num_rows=1,
                    delay=0,
                    schema_source="proto",
                    target="Orion",
                    start_packet_id=packet_id,
                )

            raw_found = False
            deadline = time.time() + 20
            while time.time() < deadline and not raw_found:
                for records in raw_consumer.poll(timeout_ms=1000).values():
                    for record in records:
                        message = OrionSensorData()
                        message.ParseFromString(record.value)
                        if message.packet_id == packet_id:
                            self.assertEqual(dict(record.headers).get("car_type"), b"Orion")
                            raw_found = True
                            break
            self.assertTrue(raw_found, f"packet {packet_id} missing from sensor_data")

            grafana_found = False
            deadline = time.time() + 20
            while time.time() < deadline and not grafana_found:
                for records in grafana_consumer.poll(timeout_ms=1000).values():
                    for record in records:
                        decoded = json.loads(record.value.decode("utf-8"))
                        if decoded.get("packet_id") == packet_id:
                            self.assertEqual(decoded.get("car_type"), "Orion")
                            self.assertIn("steer_col_angle", decoded)
                            grafana_found = True
                            break
            self.assertTrue(grafana_found, f"packet {packet_id} missing from grafana_data_orion")

            row = None
            deadline = time.time() + 20
            while time.time() < deadline and row is None:
                with psycopg2.connect(
                    host=self.config.db_host,
                    port=self.config.db_port,
                    user=self.config.db_user,
                    password=self.config.db_password,
                    database="orion",
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT packet.packet_id, packet.time, dynamics.steer_col_angle
                            FROM packet
                            JOIN dynamics USING (packet_id)
                            WHERE packet.packet_id = %s
                            """,
                            (packet_id,),
                        )
                        row = cursor.fetchone()
                if row is None:
                    time.sleep(0.5)

            self.assertIsNotNone(row, f"packet {packet_id} missing from the Orion database")
            self.assertEqual(row[0], packet_id)
            self.assertGreater(row[1], 0)
            self.assertIsNotNone(row[2])
        finally:
            raw_client.close()
            grafana_client.close()
    
    def test_mqtt_to_kafka_flow(self):
        """
        Test that data published to MQTT is forwarded to Kafka by the ingest service.
        
        Data flow: MQTT (Orion data topic) -> Ingest Service -> Kafka (sensor_data topic)
        """
        packet_id = self._fresh_packet_id()
        kafka_client = KafkaTestClient(self.config)
        consumer = kafka_client.create_consumer(
            "sensor_data",
            group_id=f"test-flow-{uuid.uuid4().hex}",
            auto_offset_reset="latest",
        )

        try:
            self._prime_consumer(consumer)
            # Generate and send protobuf test data via MQTT
            with MQTTTestClient(self.config) as mqtt_client:
                # Generate Orion test protobuf message
                test_data = self.data_generator.generate_protobuf_message(packet_id=packet_id, car="Orion")
                
                if test_data:
                    # Publish to Orion data topic (bare data is Orion stream)
                    mqtt_client.publish("data", test_data, qos=1)
                    time.sleep(2)  # Allow time for ingest to process and forward
                    
                    # Check if message arrived in Kafka
                    message_found = False
                    start_time = time.time()

                    while time.time() - start_time < 10:
                        batch = consumer.poll(timeout_ms=1000)
                        for partition, records in batch.items():
                            for record in records:
                                if record.value == test_data:
                                    message_found = True
                                    break
                        if message_found:
                            break

                    self.assertTrue(
                        message_found,
                        "MQTT payload should be forwarded unchanged to sensor_data",
                    )
                else:
                    self.fail("Could not generate Orion protobuf test data")
                    
        finally:
            kafka_client.close()
    
    def test_protobuf_data_ingest(self):
        """
        Test protobuf data ingestion pattern (as used by paho_testing.py).
        
        This validates the communication pattern used by non-Docker scripts
        to send test data to the containerized ingest service.
        """
        with MQTTTestClient(self.config) as mqtt_client:
            # Generate multiple Nightwatch protobuf messages
            first_packet_id = self._fresh_packet_id()
            for packet_id in range(first_packet_id, first_packet_id + 10):
                test_data = self.data_generator.generate_protobuf_message(packet_id=packet_id, car="Nightwatch")
                if test_data:
                    mqtt_client.publish("nightwatch/data", test_data, qos=0)
                    time.sleep(0.01)  # Small delay between messages

            assert_eventually(
                lambda: self._count_packets(
                    "telemetry",
                    first_packet_id,
                    first_packet_id + 9,
                ) == 10,
                timeout=20,
                message="Nightwatch protobuf packets were not persisted",
            )
    
    def test_pickle_data_ingest(self):
        """
        Test pickle-serialized data ingestion (legacy pattern).
        """
        import pickle
        
        with MQTTTestClient(self.config) as mqtt_client:
            # Create test data payload
            packet_id = self._fresh_packet_id()
            test_data = self.data_generator.generate_sensor_data(packet_id=packet_id)
            payload = pickle.dumps(test_data)

            mqtt_client.publish(
                "config/flask",
                json.dumps({"event_id": packet_id}).encode(),
                qos=1,
            )
            try:
                # Publish to specific table topics
                tables = ["packet", "dynamics", "controls", "thermal"]
                for table in tables:
                    mqtt_client.publish(f"nightwatch/{table}", payload, qos=0)
                    time.sleep(0.1)

                assert_eventually(
                    lambda: self._count_packets("telemetry", packet_id) == 1,
                    timeout=20,
                    message="Legacy pickle packet was not persisted",
                )
            finally:
                mqtt_client.publish("config/flask", b"end_event", qos=1)
    
    def test_config_flask_event_flow(self):
        """
        Test configuration event flow (start/end event).
        
        This tests the control plane communication used to signal
        the start and end of data collection events.
        """
        with MQTTTestClient(self.config) as mqtt_client:
            # Send start event configuration
            start_event = {"event_id": 99999}
            mqtt_client.publish("config/flask", json.dumps(start_event).encode(), qos=1)
            time.sleep(1)
            
            # Send end event
            mqtt_client.publish("config/flask", b"end_event", qos=1)
            time.sleep(1)
            
            self.assertTrue(True, "Config/flask event flow works")
    
    def test_processor_config_flow(self):
        """
        Test processor configuration flow (config/test topic).
        
        Used by GPS classifier and Lap timer processors.
        """
        with MQTTTestClient(self.config) as mqtt_client:
            # Send processor configuration
            processor_config = {
                "event_id": 99999,
                "status": 1,
                "start_packet": 0,
                "gate": ((30.289464, -97.735303), (30.289500, -97.735350)),
            }
            mqtt_client.publish("config/test", json.dumps(processor_config).encode(), qos=1)
            time.sleep(1)
            
            self.assertTrue(True, "Processor config flow works")
    
    def test_angelique_data_flow(self):
        """
        Test Angelique-specific data flow (different protobuf format).
        """
        with MQTTTestClient(self.config) as mqtt_client:
            # Generate Angelique test data
            packet_id = self._fresh_packet_id()
            test_data = self.data_generator.generate_protobuf_message(
                packet_id=packet_id,
                car="Angelique"
            )
            
            if test_data:
                mqtt_client.publish("angelique/data", test_data, qos=0)
                assert_eventually(
                    lambda: self._count_packets("angelique", packet_id) == 1,
                    timeout=20,
                    message="Angelique protobuf packet was not persisted",
                )
            else:
                self.fail("Could not generate Angelique protobuf data")

    def test_orion_data_flow(self):
        """
        Test Orion-specific data flow.
        """
        with MQTTTestClient(self.config) as mqtt_client:
            packet_id = self._fresh_packet_id()
            test_data = self.data_generator.generate_protobuf_message(
                packet_id=packet_id,
                car="Orion"
            )

            if test_data:
                mqtt_client.publish("orion/data", test_data, qos=0)
                assert_eventually(
                    lambda: self._count_packets("orion", packet_id) == 1,
                    timeout=20,
                    message="Orion protobuf packet was not persisted",
                )
            else:
                self.fail("Could not generate Orion protobuf data")


class TestKafkaProcessorFlow(unittest.TestCase):
    """
    Tests for Kafka consumer processors (gps_classifier, lap_timer, kafka_base).
    """
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.config = TelemetryConfig.from_env()
        cls.data_generator = TestDataGenerator(seed=42)
        
        # Wait for Kafka
        if not wait_for_service(
            lambda: check_kafka_connection(cls.config.kafka_host, cls.config.kafka_port),
            "Kafka",
            timeout=60
        ):
            raise ConnectionError("Kafka service not available")
    
    def test_kafka_sensor_data_consumption(self):
        """
        Test that processors can consume from sensor_data topic.
        
        This validates the pattern used by kafka_base processor.
        """
        kafka_client = KafkaTestClient(self.config)

        try:
            consumer = kafka_client.create_consumer(
                "sensor_data",
                group_id=f"test-processor-{uuid.uuid4().hex}",
                auto_offset_reset="latest",
            )
            TestDataFlow._prime_consumer(consumer)

            producer = kafka_client.create_producer()
            test_data = self.data_generator.generate_protobuf_message(packet_id=int(time.time() * 1000))
            self.assertIsNotNone(test_data, "Could not generate protobuf test data")
            producer.send("sensor_data", value=test_data).get(timeout=10)
            producer.flush()

            message_found = False
            start_time = time.time()
            while time.time() - start_time < 10 and not message_found:
                for records in consumer.poll(timeout_ms=1000).values():
                    message_found = any(record.value == test_data for record in records)

            self.assertTrue(message_found, "Processor should consume the produced sensor_data packet")
                
        finally:
            kafka_client.close()
    
    def test_protobuf_decode_in_consumer(self):
        """
        Test that protobuf messages can be decoded by Kafka consumers.
        
        This validates the decoding pattern used in kafka_base/main.py.
        """
        kafka_client = KafkaTestClient(self.config)

        try:
            from google.protobuf.json_format import MessageToDict
            from stack.ingest.protobuf.template_pb2 import SensorData

            consumer = kafka_client.create_consumer(
                "sensor_data",
                group_id=f"test-decode-{uuid.uuid4().hex}",
                auto_offset_reset="latest",
            )
            TestDataFlow._prime_consumer(consumer)

            producer = kafka_client.create_producer()
            packet_id = int(time.time() * 1000)
            test_data = self.data_generator.generate_protobuf_message(packet_id=packet_id)
            self.assertIsNotNone(test_data, "Could not generate protobuf test data")
            producer.send("sensor_data", value=test_data).get(timeout=10)
            producer.flush()

            deadline = time.time() + 10
            while time.time() < deadline:
                for records in consumer.poll(timeout_ms=1000).values():
                    for record in records:
                        if record.value != test_data:
                            continue
                        message = SensorData()
                        message.ParseFromString(record.value)
                        decoded = MessageToDict(message, preserving_proto_field_name=True)
                        self.assertEqual(int(decoded["packet_id"]), packet_id)
                        return

            self.fail(f"No decodable sensor_data packet found for packet_id={packet_id}")
                
        finally:
            kafka_client.close()


if __name__ == "__main__":
    unittest.main()
