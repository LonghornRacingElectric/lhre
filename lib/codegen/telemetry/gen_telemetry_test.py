import json
import re
import sys
import unittest

from runfiles import Runfiles

from lib.codegen.telemetry import gen_telemetry
from lib.spec.ir import wire
from lib.spec.ir import loader


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
        orm = gen_telemetry.render_orm(self.spec)
        prisma = gen_telemetry.render_prisma(self.spec, "-- common")
        for group, fields in gen_telemetry.snapshot_fields(self.spec).items():
            self.assertIn(f"CREATE TABLE public.{gen_telemetry.snake_case(group)}", sql)
            for _, field, _ in fields:
                self.assertRegex(sql, rf"(?m)^\s+{field}\s+")
                self.assertIn(f"{field} = Column(", orm)
                self.assertRegex(prisma, rf"(?m)^\s+{field}\s+")
        self.assertRegex(sql, r"(?m)^\s+gps\s+real\[\],")

    def test_contract_identity_ignores_source_locations_and_descriptions(self):
        copied = loader.Spec({"relocated/" + str(index): type(file).FromString(file.SerializeToString()) for index, file in enumerate(reversed(list(self.spec.files.values())))})
        for _, message in copied.messages():
            message.description = "documentation-only change"
        self.assertEqual(gen_telemetry.schema_manifest(self.spec)["schema_id"], gen_telemetry.schema_manifest(copied)["schema_id"])

    def test_reserved_numbers_and_names_are_emitted(self):
        spec = loader.Spec({name: type(file).FromString(file.SerializeToString()) for name, file in self.spec.files.items()})
        group = next(spec.groups())[1]
        group.reserved_ids.append(999)
        group.reserved_names.append("retired_field")
        proto = gen_telemetry.render_proto(spec, "test")
        self.assertIn("999;", proto)
        self.assertIn('reserved "retired_field";', proto)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
