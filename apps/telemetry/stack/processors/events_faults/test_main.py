import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from stack.ingest.protobuf import artemis_pb2

from main import process_record


def telemetry(car=b"Artemis", group="diagnostics_high", t_ms=1767225600000, **fields):
    frame = artemis_pb2.ArtemisSensorData(time=t_ms)
    descriptor = frame.DESCRIPTOR.fields_by_name[group]
    fields = fields or {"stomp_fault": False, "post_faults": 0, "run_faults": 0}
    paths = []
    for name, value in fields.items():
        setattr(getattr(frame, group), name, value)
        paths.append(artemis_pb2.FieldPath(
            group=descriptor.number, field=descriptor.message_type.fields_by_name[name].number,
        ))
    batch = artemis_pb2.TelemetryBatch(format_version=1, updates=[artemis_pb2.TelemetryUpdate(
        sample_time_ns=t_ms * 1_000_000, values=frame, updated_fields=paths,
    )])
    return SimpleNamespace(
        headers=[("car_type", car), ("payload_format", b"artemis_updates_v1")],
        value=batch.SerializeToString(),
    )


class FaultChannelTest(unittest.TestCase):
    def setUp(self):
        self.producer = Mock()
        self.diagnostics = {}

    def process(self, record):
        return process_record(record, self.producer, self.diagnostics, "fault_events")

    def test_decode_raise_repeat_clear(self):
        self.assertEqual(self.process(telemetry()), [])
        self.assertEqual(self.process(telemetry(stomp_fault=True)), [{
            "kind": "fault_raised", "fault": "stomp_fault",
            "car": "artemis", "t_ms": 1767225600000,
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
        for car in (b"Orion", b"unknown"):
            self.assertEqual(self.process(telemetry(car=car, stomp_fault=True)), [])
        record = telemetry(stomp_fault=True)
        record.headers = []
        self.assertEqual(self.process(record), [])
        self.producer.send.assert_not_called()

    def test_missing_diagnostics_does_not_clear_faults(self):
        self.process(telemetry(stomp_fault=True))
        self.assertEqual(self.process(telemetry(group="controls", apps1_v=0)), [])
        self.assertEqual(self.process(telemetry())[0]["kind"], "fault_cleared")

    def test_partial_diagnostics_only_clear_observed_faults(self):
        self.process(telemetry(stomp_fault=True))
        raised, = self.process(telemetry(run_faults=4))
        self.assertEqual((raised["kind"], raised["fault"]), ("fault_raised", "run_faults[2]"))
        cleared, = self.process(telemetry(stomp_fault=False))
        self.assertEqual((cleared["kind"], cleared["fault"]), ("fault_cleared", "stomp_fault"))
        cleared, = self.process(telemetry(run_faults=0))
        self.assertEqual((cleared["kind"], cleared["fault"]), ("fault_cleared", "run_faults[2]"))

    def test_batch_transitions_keep_each_observation_time(self):
        record = telemetry(stomp_fault=True)
        batch = artemis_pb2.TelemetryBatch.FromString(record.value)
        batch.updates.extend(artemis_pb2.TelemetryBatch.FromString(
            telemetry(t_ms=1767225600010, stomp_fault=False).value,
        ).updates)
        record.value = batch.SerializeToString()
        self.assertEqual([(event["kind"], event["t_ms"]) for event in self.process(record)], [
            ("fault_raised", 1767225600000), ("fault_cleared", 1767225600010),
        ])

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
