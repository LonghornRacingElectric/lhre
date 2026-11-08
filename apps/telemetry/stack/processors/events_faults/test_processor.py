import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from stack.ingest.protobuf import can_packets_pb2

from processor import FaultProcessor, car_from_headers


def telemetry(offset=0, t_ms=1767225600000, car=b"Orion", **faults):
    frame = can_packets_pb2.OrionSensorData(packet_id=offset + 1, time=t_ms)
    frame.diagnostics_high.SetInParent()
    for name, value in faults.items():
        setattr(frame.diagnostics_high, name, value)
    return SimpleNamespace(
        topic="sensor_data", partition=0, offset=offset,
        headers=[("car_type", car)], value=frame.SerializeToString(),
    )


class FaultProcessorTest(unittest.TestCase):
    def setUp(self):
        self.producer = Mock()
        self.processor = FaultProcessor(self.producer)

    def test_real_protobuf_raise_repeat_clear(self):
        self.assertEqual(self.processor.process(telemetry()), [])
        raised = self.processor.process(telemetry(1, stomp_fault=True))
        self.assertEqual(self.processor.process(telemetry(2, stomp_fault=True)), [])
        cleared = self.processor.process(telemetry(3))
        self.assertEqual([event["kind"] for event in raised + cleared],
                         ["fault_raised", "fault_cleared"])
        self.assertEqual(self.producer.send.call_count, 2)

    def test_preserves_timestamp_packet_and_kafka_identity(self):
        event, = self.processor.process(telemetry(12, t_ms=1000, run_faults=4))
        self.assertEqual(event, {
            "kind": "fault_raised", "fault": "run_faults[2]", "car": "orion",
            "t_ms": 1000, "packet_id": 13,
            "event_id": "sensor_data:0:12:fault_raised:run_faults[2]",
        })
        self.producer.send.assert_called_once_with("fault_events", value=event, key=b"orion")
        self.producer.send.return_value.get.assert_called_once_with(timeout=10)

    def test_custom_output_topic(self):
        FaultProcessor(self.producer, "test_faults").process(telemetry(stomp_fault=True))
        self.assertEqual(self.producer.send.call_args.args[0], "test_faults")

    def test_header_normalization(self):
        self.assertEqual(car_from_headers([(b"CAR_TYPE", b" Orion ")]), "orion")
        self.assertIsNone(car_from_headers([]))
        self.assertIsNone(car_from_headers([("car_type", b"Orion"), ("car_type", b"Orion")]))

    def test_other_car_or_missing_header_does_not_clear_orion(self):
        self.processor.process(telemetry(stomp_fault=True))
        for car in (b"Angelique", b"Nightwatch", b"unknown"):
            self.assertEqual(self.processor.process(telemetry(car=car)), [])
        record = telemetry()
        record.headers = []
        self.assertEqual(self.processor.process(record), [])
        event, = self.processor.process(telemetry(1))
        self.assertEqual(event["kind"], "fault_cleared")

    def test_malformed_or_missing_data_preserves_state(self):
        self.processor.process(telemetry(stomp_fault=True))
        for value in (b"\xff", b"", can_packets_pb2.OrionSensorData(time=1).SerializeToString()):
            with self.subTest(value=value):
                record = telemetry()
                record.value = value
                with self.assertLogs(level="WARNING"):
                    self.assertEqual(self.processor.process(record), [])
        event, = self.processor.process(telemetry(1))
        self.assertEqual(event["kind"], "fault_cleared")

    def test_invalid_header_does_not_clear_state(self):
        self.processor.process(telemetry(stomp_fault=True))
        record = telemetry(car=b"\xff")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.processor.process(record), [])
        self.assertEqual(self.processor.process(telemetry(1))[0]["kind"], "fault_cleared")

    def test_invalid_masks_do_not_clear_state(self):
        self.processor.process(telemetry(run_faults=4))
        for mask in (-1, 0.5, float("nan"), float("inf"), 1 << 24):
            with self.subTest(mask=mask), self.assertLogs(level="WARNING"):
                self.assertEqual(self.processor.process(telemetry(1, run_faults=mask)), [])
        self.assertEqual(self.producer.send.call_count, 1)
        self.assertEqual(self.processor.process(telemetry(2))[0]["kind"], "fault_cleared")

    def test_zero_timestamp_does_not_replace_observation_time(self):
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.processor.process(telemetry(t_ms=0, stomp_fault=True)), [])
        self.producer.send.assert_not_called()

    def test_older_frame_does_not_reverse_newer_state(self):
        self.processor.process(telemetry(t_ms=2000, stomp_fault=True))
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.processor.process(telemetry(1, t_ms=1000)), [])
        self.assertEqual(self.processor.process(telemetry(2, t_ms=3000))[0]["kind"], "fault_cleared")

    def test_events_acknowledged_before_offsets_commit(self):
        calls = []
        self.producer.send.side_effect = lambda *args, **kwargs: SimpleNamespace(
            get=lambda timeout: calls.append("ack"),
        )
        consumer = Mock()
        consumer.commit.side_effect = lambda: calls.append("commit")
        self.processor.process_batch(consumer, {0: [telemetry(stomp_fault=True, run_faults=4)]})
        self.assertEqual(calls, ["ack", "ack", "commit"])

    def test_publish_failure_does_not_commit_or_accept_new_state(self):
        self.producer.send.return_value.get.side_effect = TimeoutError("broker unavailable")
        consumer = Mock()
        record = telemetry(stomp_fault=True)
        with self.assertRaises(TimeoutError):
            self.processor.process_batch(consumer, {0: [record]})
        consumer.commit.assert_not_called()
        attempted = self.producer.send.call_args.kwargs["value"]
        self.producer.send.return_value.get.side_effect = None
        event, = self.processor.process(record)
        self.assertEqual(event, attempted)

    def test_partial_publish_failure_can_replay_same_event_ids(self):
        self.producer.send.return_value.get.side_effect = [None, TimeoutError("broker unavailable")]
        consumer = Mock()
        record = telemetry(stomp_fault=True, run_faults=4)
        with self.assertRaises(TimeoutError):
            self.processor.process_batch(consumer, {0: [record]})
        consumer.commit.assert_not_called()
        attempted = [call.kwargs["value"] for call in self.producer.send.call_args_list]
        self.producer.send.return_value.get.side_effect = None
        self.assertEqual(self.processor.process(record), attempted)

    def test_later_failure_does_not_commit_any_part_of_batch(self):
        self.producer.send.return_value.get.side_effect = [None, TimeoutError("broker unavailable")]
        consumer = Mock()
        with self.assertRaises(TimeoutError):
            self.processor.process_batch(consumer, {0: [telemetry(stomp_fault=True), telemetry(1)]})
        consumer.commit.assert_not_called()

    def test_bad_record_is_skipped_without_blocking_later_records(self):
        record = telemetry()
        record.value = b"\xff"
        consumer = Mock()
        with self.assertLogs(level="WARNING"):
            self.processor.process_batch(consumer, {0: [record, telemetry(1, stomp_fault=True)]})
        self.producer.send.assert_called_once()
        consumer.commit.assert_called_once_with()

    def test_empty_poll_does_not_commit(self):
        consumer = Mock()
        self.processor.process_batch(consumer, {})
        consumer.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
