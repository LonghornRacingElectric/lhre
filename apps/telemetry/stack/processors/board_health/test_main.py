import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from stack.ingest.protobuf import artemis_pb2
import main as health


def record(observations):
    batch = artemis_pb2.TelemetryBatch(format_version=1)
    for stamp, name in observations:
        update = batch.updates.add(sample_time_ns=stamp * 1_000_000)
        update.values.time = stamp
        setattr(update.values.board_status, name, 0)
        update.updated_fields.add(
            group=update.values.DESCRIPTOR.fields_by_name["board_status"].number,
            field=update.values.board_status.DESCRIPTOR.fields_by_name[name].number,
        )
    return SimpleNamespace(value=batch.SerializeToString(), headers=[
        ("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1"),
    ])


class SnapshotTimeTest(unittest.TestCase):
    def test_newest_observation_and_mixed_poll_timeout(self):
        now = 1_767_225_600_000
        (first, first_field), (second, second_field) = list(health.BOARD_FIELDS.items())[:2]
        polls = [record([(now, first_field), (now - 1, second_field)]),
                 record([(now + 100, second_field)])]
        clock = [0]
        consumer, producer = Mock(), Mock()

        def poll(**kwargs):
            result = polls.pop(0)
            clock[0] = 0 if polls else 11
            if not polls:
                health.stop = True
            return {0: [result]}

        consumer.poll.side_effect = poll
        with patch.object(health, "stop", False), \
                patch.object(health, "TELEMETRY_TIMEOUT_S", 10), \
                patch.object(health, "KafkaConsumer", return_value=consumer), \
                patch.object(health, "KafkaProducer", return_value=producer), \
                patch.object(health.signal, "signal"), \
                patch.object(health.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(health, "_server_now_ms", return_value=now):
            health.main()

        snapshots = [call.kwargs["value"] for call in producer.send.call_args_list
                     if call.args[0] == health.STATE_TOPIC]
        self.assertEqual(len(snapshots), 2)
        self.assertEqual((snapshots[0]["t_ms"], snapshots[0]["t_source"]), (now, "car"))
        self.assertEqual(snapshots[0]["boards"][first], "LIVE")
        self.assertEqual(snapshots[1]["boards"][first], "UNKNOWN")
        self.assertEqual(snapshots[1]["boards"][second], "LIVE")
        self.assertEqual((snapshots[1]["t_ms"], snapshots[1]["t_source"]), (now, "server"))


if __name__ == "__main__":
    unittest.main()
