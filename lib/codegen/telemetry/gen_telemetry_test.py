import json
import re
import sys
import unittest

from runfiles import Runfiles

from lib.codegen.telemetry import gen_telemetry
from lib.spec.ir import wire


class TelemetryGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resolver = Runfiles.Create()
        paths = [resolver.Rlocation(path) for path in sys.argv[1:]]
        cls.spec = gen_telemetry.checked_spec(paths)

    def test_proto_contains_every_ledger_field(self):
        proto = gen_telemetry.render_proto(self.spec, "test")
        for (group, field), (field_type, _) in wire.field_types(self.spec).items():
            field_id = wire.field_ids(self.spec)[(group, field)]
            self.assertRegex(proto, rf"\b{re.escape(field_type)} {field} = {field_id};")
        self.assertIn("repeated float gps = 1;", proto)
        self.assertIn("BoardStatus board_status = 9;", proto)

    def test_json_tracks_every_can_message(self):
        packets = json.loads(gen_telemetry.render_json(self.spec))
        messages = [message for _, message in sorted(self.spec.messages(), key=lambda item: item[1].can_id)]
        self.assertEqual(len(packets), len(messages))
        for packet, message in zip(packets, messages, strict=True):
            self.assertEqual(packet["packet_id"], message.can_id)
            self.assertEqual(packet["from"], [message.from_board])
            self.assertEqual(packet["to"], list(message.to_boards))
            self.assertEqual(packet["data_length"], message.dlc)
            self.assertEqual(packet["quantity"], gen_telemetry.validator.effective_quantity(message))
            self.assertEqual(packet["bus"], message.bus)

    def test_sql_tracks_every_snapshot_field(self):
        sql = gen_telemetry.render_sql(self.spec, "test", "-- common")
        for group, fields in gen_telemetry.snapshot_fields(self.spec).items():
            self.assertIn(f"CREATE TABLE public.{gen_telemetry.snake_case(group)}", sql)
            for _, field, _ in fields:
                self.assertRegex(sql, rf"(?m)^\s+{field}\s+")
        self.assertRegex(sql, r"(?m)^\s+gps\s+real\[\],")

    def test_legacy_inverter_sources_keep_the_same_snapshot_fields(self):
        messages = {message.name: message for _, message in self.spec.messages()}
        packets = {packet["packet_id"]: packet for packet in json.loads(gen_telemetry.render_json(self.spec))}
        proto = gen_telemetry.render_proto(self.spec, "test")
        for message_name, field in (
            ("INVERTER_STATUS", "motor_speed"),
            ("INVERTER_TSO", "torque_feedback"),
            ("INVERTER_SPEED", "motor_speed"),
            ("INVERTER_SPEED", "torque_feedback"),
        ):
            with self.subTest(message=message_name, field=field):
                message = messages[message_name]
                index, signal = next((index, signal) for index, signal in enumerate(message.signal)
                                     if signal.name == field)
                self.assertTrue(signal.telemetry.shared_source)
                self.assertEqual(signal.telemetry.group, "Controls")
                self.assertEqual(signal.telemetry.field, field)
                mapping = packets[message.can_id]["bytes"][index]["protobuf"]
                self.assertEqual(mapping["field"], field)
                self.assertEqual(mapping["proto_id"], signal.telemetry.id)
                self.assertEqual(mapping["type"], "float")
                self.assertEqual(proto.count(f"float {field} = {signal.telemetry.id};"), 1)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
