import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from stack.ingest.protobuf import can_packets_pb2

from main import process_record


def telemetry(car=b"Orion", **faults):
    frame = can_packets_pb2.OrionSensorData(time=1767225600000)
    frame.diagnostics_high.SetInParent()
    for name, value in faults.items():
        setattr(frame.diagnostics_high, name, value)
    return SimpleNamespace(
        headers=[("car_type", car)], value=frame.SerializeToString(),
    )


class FaultChannelTest(unittest.TestCase):
    def setUp(self):
        self.producer = Mock()
        self.active = {}

    def process(self, record):
        return process_record(record, self.producer, self.active, "fault_events")

    def test_decode_raise_repeat_clear(self):
        self.assertEqual(self.process(telemetry()), [])
        self.assertEqual(self.process(telemetry(stomp_fault=True)), [{
            "kind": "fault_raised", "fault": "stomp_fault",
            "car": "orion", "t_ms": 1767225600000,
        }])
        self.assertEqual(self.process(telemetry(stomp_fault=True)), [])
        cleared, = self.process(telemetry())
        self.assertEqual(cleared["kind"], "fault_cleared")
        self.assertEqual(self.producer.send.call_count, 2)
        self.producer.send.assert_called_with("fault_events", value=cleared)

    def test_inverter_bits_publish_individual_events(self):
        events = self.process(telemetry(run_faults=5))
        self.assertEqual([event["fault"] for event in events],
                         ["run_faults[0]", "run_faults[2]"])

    def test_other_cars_and_untagged_frames_are_skipped(self):
        for car in (b"Angelique", b"Nightwatch", b"unknown"):
            self.assertEqual(self.process(telemetry(car=car, stomp_fault=True)), [])
        record = telemetry(stomp_fault=True)
        record.headers = []
        self.assertEqual(self.process(record), [])
        self.producer.send.assert_not_called()

    def test_missing_diagnostics_does_not_clear_faults(self):
        self.process(telemetry(stomp_fault=True))
        record = telemetry()
        record.value = can_packets_pb2.OrionSensorData(time=1).SerializeToString()
        self.assertEqual(self.process(record), [])
        self.assertEqual(self.process(telemetry())[0]["kind"], "fault_cleared")

    def test_malformed_protobuf_does_not_clear_faults(self):
        self.process(telemetry(stomp_fault=True))
        record = telemetry()
        record.value = b"\xff"
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.process(record), [])
        self.assertEqual(self.process(telemetry())[0]["kind"], "fault_cleared")

    def test_invalid_mask_does_not_clear_faults(self):
        self.process(telemetry(run_faults=4))
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.process(telemetry(run_faults=float("nan"))), [])
        self.assertEqual(self.process(telemetry())[0]["kind"], "fault_cleared")

    def test_publish_failure_keeps_previous_fault_set(self):
        self.producer.send.return_value.get.side_effect = TimeoutError("broker unavailable")
        with self.assertRaises(TimeoutError):
            self.process(telemetry(stomp_fault=True))
        self.producer.send.return_value.get.side_effect = None
        self.assertEqual(self.process(telemetry(stomp_fault=True))[0]["kind"], "fault_raised")


if __name__ == "__main__":
    unittest.main()
