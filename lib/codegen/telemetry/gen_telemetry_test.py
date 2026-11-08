import json
import re
import sys
import unittest

from runfiles import Runfiles

from lib.codegen.telemetry import catalog, gen_telemetry
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

    def test_catalog_hash_ignores_paths_and_file_order(self):
        from lib.spec.ir import loader
        original = json.loads(gen_telemetry.render_signals_json(self.spec))
        relocated = loader.Spec({f"elsewhere/{i}.textproto": value
                                 for i, value in enumerate(reversed(list(self.spec.files.values())))})
        self.assertEqual(json.loads(gen_telemetry.render_signals_json(relocated)), original)

    def test_catalog_generation_requires_car_identity(self):
        from lib.spec.ir import loader
        with self.assertRaisesRegex(ValueError, "exactly one"):
            gen_telemetry.render_signals_json(loader.Spec({}))

    def test_json_tracks_every_can_message(self):
        packets = json.loads(gen_telemetry.render_can_json(self.spec))
        messages = [message for _, message in sorted(self.spec.messages(), key=lambda item: item[1].can_id)]
        self.assertEqual(len(packets), len(messages))
        for packet, message in zip(packets, messages, strict=True):
            self.assertEqual(packet["packet_id"], message.can_id)
            self.assertEqual(packet["from"], [message.from_board])
            self.assertEqual(packet["to"], list(message.to_boards))
            self.assertEqual(packet["data_length"], message.dlc)
            self.assertEqual(packet["quantity"], gen_telemetry.validator.effective_quantity(message))
            self.assertEqual(packet["bus"], message.bus)

    def test_signals_cover_every_snapshot_field_once(self):
        catalog = json.loads(gen_telemetry.render_signals_json(self.spec))
        rows = {row["signal"]: row for row in catalog["signals"]}
        expected = {f"{gen_telemetry.snake_case(group)}.{field}"
                    for group, field in wire.field_types(self.spec)}
        self.assertEqual(set(rows), expected)
        self.assertEqual(len(rows), len(catalog["signals"]))
        for row in rows.values():
            self.assertIn("rate_hz", row)
            self.assertNotIn("native_rate_hz", row)
            self.assertNotIn("uplink_rate_hz", row)
        self.assertEqual(catalog["car"], "orion")
        self.assertRegex(catalog["schema_version"], r"^[a-f0-9]{64}$")
        self.assertTrue(all("schema_version" not in row for row in rows.values()))
        self.assertEqual(rows["dynamics.gps"]["unit"], "deg")
        self.assertEqual(rows["dynamics.gps_speed"]["unit"], "kn")
        self.assertIsNone(rows["dynamics.gps_precise"]["unit"])
        self.assertTrue(rows["dynamics.gps"]["repeated"])
        for field in ("gps", "gps_imu", "gps_speed", "gps_precise"):
            row = rows[f"dynamics.{field}"]
            self.assertEqual(row["rate_hz"], 50)
        health = rows["board_status.hvc_last_seen_s"]
        self.assertEqual((health["module"], health["unit"], health["channel_type"]),
                         ("gateway", "s", "health"))

    def test_catalog_preserves_event_and_uplink_metadata(self):
        from lib.spec.ir import loader
        parsed = loader.parse_file('''
car: "test"
groups { group { name: "Events" snapshot_id: 3 next_free_id: 2
  external_field { name: "fault" id: 1 type: PROTO_BOOL
    uplink_rate_hz: 5 channel_type: EVENT }
} }
''')
        spec = loader.Spec({"test.textproto": parsed})
        row = json.loads(gen_telemetry.render_signals_json(spec))["signals"][0]
        self.assertEqual(row["channel_type"], "event")
        self.assertEqual(row["rate_hz"], 5)

    def test_can_uplink_rate_override_and_native_fallback(self):
        from lib.spec.ir import loader
        for frequency, override, expected in ((100, "", 100), (100, "uplink_rate_hz: 10", 10), (0, "", 0)):
            with self.subTest(frequency=frequency, override=override):
                parsed = loader.parse_file(f'''
car: "test"
bus {{ name: "Critical" bitrate_bps: 1000000 }}
groups {{ group {{ name: "Vehicle" snapshot_id: 3 next_free_id: 2 }} }}
message {{ name: "STATUS" can_id: 256 from_board: "VCU" bus: "Critical" dlc: 1
  frequency_hz: {frequency}
  signal {{ name: "speed" encoding {{ bit_length: 8 }} logical {{ physical {{ unit: "m/s" }} }}
    telemetry {{ group: "Vehicle" field: "speed" id: 1 {override} }} }}
}}
''')
                spec = loader.Spec({"test.textproto": parsed})
                self.assertEqual(gen_telemetry.validator.validate(spec)[0], [])
                row = json.loads(gen_telemetry.render_signals_json(spec))["signals"][0]
                self.assertEqual(row["rate_hz"], expected)

    def test_sql_tracks_every_snapshot_field(self):
        sql = gen_telemetry.render_sql(self.spec, "test", "-- common")
        for group, fields in gen_telemetry.snapshot_fields(self.spec).items():
            self.assertIn(f"CREATE TABLE public.{gen_telemetry.snake_case(group)}", sql)
            for _, field, _ in fields:
                self.assertRegex(sql, rf"(?m)^\s+{field}\s+")
        self.assertRegex(sql, r"(?m)^\s+gps\s+real\[\],")

    def test_legacy_inverter_sources_keep_the_same_snapshot_fields(self):
        messages = {message.name: message for _, message in self.spec.messages()}
        packets = {packet["packet_id"]: packet for packet in json.loads(gen_telemetry.render_can_json(self.spec))}
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
