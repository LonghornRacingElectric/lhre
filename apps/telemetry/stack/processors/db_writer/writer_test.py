import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from stack.ingest.sensor_decode import car_from_headers, decode_sensor
from stack.ingest.protobuf import angelique_pb2, can_packets_pb2, template_pb2
from stack.ingest.partition_manager import PartitionManager
from stack.processors.db_writer.main import consume, InitialOffsets
from stack.ingest.mqtt_handler import MQTTHandler
from stack.ingest.packet_ids import retained_packet_high_water
import queue
import json
from stack.processors.db_writer.writer import DatabaseWriter


def record(offset=0, payload=b"packet", partition=0):
    return SimpleNamespace(topic="sensor_data", partition=partition, offset=offset,
                           value=payload, headers=[("car_type", b"Nightwatch")])


class DecodeTests(unittest.TestCase):
    def test_all_cars_preserve_zero_and_arrays(self):
        for car, message in [("Nightwatch", template_pb2.SensorData()),
                             ("Angelique", angelique_pb2.AngeliqueSensorData()),
                             ("Orion", can_packets_pb2.OrionSensorData())]:
            message.time = 1700000000000
            message.packet_id = 17
            message.dynamics.SetInParent()
            decoded = decode_sensor(message.SerializeToString(), car)
            self.assertEqual(int(decoded["packet_id"]), 17)
            self.assertIsInstance(decoded["dynamics"], dict)
            self.assertTrue(decoded["dynamics"])

    def test_invalid_identity_and_headers_stop_processing(self):
        with self.assertRaises(ValueError):
            decode_sensor(b"", "Nightwatch")
        for headers in ([], [("car_type", b"unknown")],
                        [("car_type", b"Orion"), ("car_type", b"Orion")]):
            with self.assertRaises(ValueError):
                car_from_headers(headers)

    def test_batch_is_validated_before_any_database_transaction(self):
        writer = DatabaseWriter.__new__(DatabaseWriter)
        writer._write_car = Mock()
        good = template_pb2.SensorData(time=100, packet_id=1).SerializeToString()
        with self.assertRaises(ValueError):
            writer.write([record(payload=good), record(1, b"")])
        writer._write_car.assert_not_called()


class ConsumerTests(unittest.TestCase):
    def run_consumer(self, batches, **kwargs):
        consumer = Mock()
        writer = Mock()
        consumer.poll.side_effect = batches
        stopped = lambda: consumer.poll.call_count >= len(batches)
        consume(consumer, writer, stopped, **kwargs)
        return consumer, writer

    def test_commits_only_successful_explicit_offsets(self):
        a, b = record(4), record(8, partition=1)
        consumer, writer = self.run_consumer([{0: [a], 1: [b]}])
        writer.write.assert_called_once_with([a, b])
        offsets = consumer.commit.call_args.kwargs["offsets"]
        self.assertEqual(sorted(value.offset for value in offsets.values()), [5, 9])
        consumer.close.assert_called_once_with(autocommit=False)

    def test_failure_never_commits(self):
        consumer, writer = Mock(), Mock()
        consumer.poll.return_value = {0: [record()]}
        writer.write.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            consume(consumer, writer, lambda: False, max_packets=1)
        consumer.commit.assert_not_called()
        consumer.close.assert_called_once_with(autocommit=False)

    def test_count_and_bytes_bound_batches(self):
        records = [record(i) for i in range(7)]
        _, writer = self.run_consumer([{0: records}], max_packets=3, max_bytes=12)
        sizes = [len(call.args[0]) for call in writer.write.call_args_list]
        self.assertEqual(sizes, [2, 2, 2, 1])

    def test_deadline_flushes_during_continuous_traffic(self):
        now = [0.0]
        consumer, writer = Mock(), Mock()
        def poll(**_kwargs):
            now[0] += .1
            return {0: [record(consumer.poll.call_count)]}
        consumer.poll.side_effect = poll
        with patch("stack.processors.db_writer.main.time.monotonic", side_effect=lambda: now[0]):
            consume(consumer, writer, lambda: consumer.poll.call_count >= 8, interval=.25)
        self.assertGreaterEqual(writer.write.call_count, 3)
        self.assertLessEqual(max(len(call.args[0]) for call in writer.write.call_args_list), 3)

    def test_empty_poll_flushes_by_elapsed_deadline(self):
        now = [0.0]
        consumer, writer = Mock(), Mock()
        def poll(**_kwargs):
            now[0] += .3
            return {0: [record()]} if consumer.poll.call_count == 1 else {}
        consumer.poll.side_effect = poll
        with patch("stack.processors.db_writer.main.time.monotonic", side_effect=lambda: now[0]):
            consume(consumer, writer, lambda: consumer.poll.call_count >= 3)
        writer.write.assert_called_once()


class ControlTests(unittest.TestCase):
    def test_welcome_uses_kafka_backlog_and_drains_pending_forwards(self):
        handler = MQTTHandler.__new__(MQTTHandler)
        handler.bridge_queue = Mock()
        handler.client = Mock()
        with patch("stack.ingest.mqtt_handler.QueryBuilder") as builder, patch(
                "stack.ingest.mqtt_handler.retained_packet_high_water", return_value=42) as highwater:
            builder.return_value.__enter__.return_value.session.query.return_value.order_by.return_value.first.return_value = (5,)
            handler._client_connected(SimpleNamespace(payload=b"BEVO-Angelique"))
        handler.bridge_queue.join.assert_called_once()
        highwater.assert_called_once_with("Angelique", 5)
        self.assertEqual(json.loads(handler.client.publish.call_args.args[1])["packet_id"], 43)

    def test_proto_callback_never_queues_database_work(self):
        handler = MQTTHandler.__new__(MQTTHandler)
        handler.orion_log_path = None
        handler.orion_csv_config = None
        handler.db_queues = {car: Mock() for car in ("Nightwatch", "Orion", "Angelique")}
        for car in handler.db_queues:
            handler._proto_ingest(b"wire bytes", car=car)
            handler.db_queues[car].put_nowait.assert_not_called()

    def test_csv_callback_snapshots_header_and_path_together(self):
        handler = MQTTHandler.__new__(MQTTHandler)
        handler.orion_csv_config = ("old.csv", ("packet.time", "packet.packet_id"))
        handler.csv_queue = queue.Queue()
        handler._proto_ingest(b"raw", car="Orion")
        handler.orion_csv_config = ("new.csv", ("different_header",))
        self.assertEqual(handler.csv_queue.get_nowait(),
                         (b"raw", "Orion", "old.csv", ("packet.time", "packet.packet_id")))

    def test_bridge_retries_failure_before_completing_queue_item(self):
        handler = MQTTHandler.__new__(MQTTHandler)
        handler._shutdown = False
        handler.bridge_queue = queue.Queue()
        handler.bridge_queue.put((b"raw", "Orion"))
        handler.bridge_queue.put(None)
        handler.bridge_stub = Mock()
        handler.bridge_stub.SendSensorData.side_effect = [SimpleNamespace(success=False, message="unavailable"), SimpleNamespace(success=True)]
        handler.grpc_channel = Mock()
        handler._make_bridge_channel = Mock(return_value=Mock())
        with patch("stack.ingest.mqtt_handler.bridge_pb2_grpc.BridgeServiceStub", return_value=handler.bridge_stub), patch("stack.ingest.mqtt_handler.time.sleep"):
            handler._bridge_worker()
        self.assertEqual(handler.bridge_stub.SendSensorData.call_count, 2)
        self.assertEqual(handler.bridge_queue.unfinished_tasks, 0)

    def test_kafka_scan_includes_unpersisted_packet_ids(self):
        message = angelique_pb2.AngeliqueSensorData(time=100, packet_id=99)
        event = record(payload=message.SerializeToString())
        event.headers = [("car_type", b"Angelique")]
        consumer = Mock()
        consumer.partitions_for_topic.return_value = {0}
        with patch("stack.ingest.packet_ids.KafkaConsumer", return_value=consumer):
            from kafka import TopicPartition
            partition = TopicPartition("sensor_data", 0)
            consumer.beginning_offsets.return_value = {partition: 0}
            consumer.end_offsets.return_value = {partition: 1}
            consumer.position.side_effect = [0, 1]
            consumer.poll.return_value = {partition: [event]}
            self.assertEqual(retained_packet_high_water("Angelique", 5), 99)
        consumer.close.assert_called_once_with(autocommit=False)

    def test_retention_gap_requires_operator_recovery(self):
        consumer = Mock()
        consumer.beginning_offsets.return_value = {0: 10}
        consumer.committed.return_value = 5
        assignment = InitialOffsets(consumer)
        assignment.on_partitions_assigned([0])
        consumer.pause.assert_called_once_with(0)
        with self.assertRaisesRegex(RuntimeError, "retention gap"):
            assignment.check()
        consumer.seek.assert_not_called()



class PartitionTests(unittest.TestCase):
    def test_multiple_gaps_create_distinct_windows_without_autoflush(self):
        session = Mock()
        session.query.return_value.order_by.return_value.first.return_value = None
        PartitionManager(gap_seconds=1).record_batch(session, [100, 150, 150, 2000, 2100, 4000])
        windows = [call.args[0] for call in session.add.call_args_list]
        self.assertEqual([(row.start_time, row.end_time) for row in windows],
                         [(100, 150), (2000, 2100), (4000, 4000)])


if __name__ == "__main__":
    unittest.main()
