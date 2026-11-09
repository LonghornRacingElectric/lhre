"""MQTT -> Kafka -> the existing writer -> car-specific database storage."""
import json
import time
import unittest
import uuid

from sqlalchemy import create_engine, select, func, URL
from sqlalchemy.orm import Session

from telemetry.stack.tests.test_utils import TelemetryConfig, MQTTTestClient, KafkaTestClient, TestDataGenerator, wait_for_service, check_kafka_connection
from stack.ingest.sensor_decode import CAR_MESSAGES
from stack.ingest.protobuf import artemis_pb2 as artemis
from telemetry.analysis.sql_utils.models import OrionPacket, OrionDynamics, ArtemisDynamics333hz


class TestDataFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = TelemetryConfig.from_env()
        cls.data_generator = TestDataGenerator(seed=42)

    @staticmethod
    def _prime_consumer(consumer, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            consumer.poll(timeout_ms=250)
            if consumer.assignment():
                return
        raise AssertionError("Kafka consumer did not receive an assignment")

    def _round_trip(self, car):
        message = CAR_MESSAGES[car](time=int(time.time() * 1000))
        if car == "Orion": message.packet_id=time.time_ns() // 1000
        message.dynamics.steer_col_angle = 1.25
        message.controls.apps1_v = 0
        if car == "Artemis":
            payload = artemis.TelemetryBatch(format_version=1,updates=[artemis.TelemetryUpdate(sample_time_ns=message.time*1_000_000+1,values=message,updated_fields=[
                artemis.FieldPath(group=3,field=message.dynamics.DESCRIPTOR.fields_by_name["steer_col_angle"].number),
                artemis.FieldPath(group=4,field=message.controls.DESCRIPTOR.fields_by_name["apps1_v"].number),
            ])]).SerializeToString()
        else: payload = message.SerializeToString()
        raw_client = KafkaTestClient(self.config)
        grafana_client = KafkaTestClient(self.config)
        suffix = uuid.uuid4().hex
        raw = raw_client.create_consumer("sensor_data", group_id=f"raw-{suffix}", auto_offset_reset="latest")
        grafana = grafana_client.create_consumer(f"grafana_data_{car.lower()}", group_id=f"grafana-{suffix}", auto_offset_reset="latest")
        engine = create_engine(URL.create("postgresql+psycopg2", username=self.config.db_user,
            password=self.config.db_password, host=self.config.db_host, port=self.config.db_port, database=car.lower()))
        try:
            self._prime_consumer(raw)
            self._prime_consumer(grafana)
            with MQTTTestClient(self.config) as mqtt:
                mqtt.publish(car.lower() + "/data", payload, qos=1)
                mqtt.publish(car.lower() + "/data", payload, qos=1)
            found_raw = found_grafana = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (found_raw and found_grafana):
                for records in raw.poll(timeout_ms=250).values():
                    found_raw |= any(r.value == payload and dict(r.headers).get("car_type") == car.encode() for r in records)
                for records in grafana.poll(timeout_ms=250).values():
                    for record in records:
                        value = json.loads(record.value)
                        found_grafana |= value.get("time") == message.time and value.get("car_type") == car
            self.assertTrue(found_raw, f"{car} raw Kafka record missing")
            self.assertTrue(found_grafana, f"{car} Grafana Kafka record missing")
            if car == "Artemis":
                statement = select(ArtemisDynamics333hz.steer_col_angle).where(ArtemisDynamics333hz.time == message.time)
            else:
                statement = select(OrionDynamics.steer_col_angle).join(OrionPacket, OrionPacket.packet_id == OrionDynamics.packet_id).where(OrionPacket.packet_id == message.packet_id)
            rows = []
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with Session(engine) as session:
                    rows = session.scalars(statement).all()
                if rows:
                    break
                time.sleep(0.25)
            self.assertEqual(rows, [1.25], f"{car} sample must persist exactly once")
        finally:
            engine.dispose()
            raw_client.close()
            grafana_client.close()

    def test_orion_packet_storage(self):
        self._round_trip("Orion")

    def test_artemis_frequency_pool_storage(self):
        self._round_trip("Artemis")


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
            producer.send("sensor_data", value=test_data, headers=[("car_type", b"Orion")]).get(timeout=10)
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
            from stack.ingest.protobuf.can_packets_pb2 import OrionSensorData as SensorData

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
            producer.send("sensor_data", value=test_data, headers=[("car_type", b"Orion")]).get(timeout=10)
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
