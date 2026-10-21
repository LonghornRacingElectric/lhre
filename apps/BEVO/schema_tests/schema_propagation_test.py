import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from google.protobuf.message_factory import GetMessageClass
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

from lib.telemetry.contract import Schema, release_compatible


class SchemaPropagationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = Path(sys.argv[1]).parent
        cls.appended = Path(sys.argv[2]).parent
        cls.encoder = Path(sys.argv[3]).resolve()
        cls.old_schema = cls.read_schema(cls.baseline)
        cls.new_schema = cls.read_schema(cls.appended)

    @staticmethod
    def read_schema(directory):
        return Schema((directory / "schema_contract.json").read_bytes(), (directory / "schema_descriptor.pb").read_bytes())

    def test_append_reaches_generated_server_artifacts_without_changing_existing_fields(self):
        for path, field in self.old_schema.fields.items():
            self.assertEqual(self.new_schema.fields[path], field)
        self.assertNotEqual(self.old_schema.schema_id, self.new_schema.schema_id)
        self.assertTrue(release_compatible(self.old_schema, self.new_schema))
        self.assertTrue(release_compatible(self.new_schema, self.old_schema))
        self.assertNotIn((3, 2), self.old_schema.fields)
        self.assertEqual(self.new_schema.fields[(3, 2)]["names"], ["controls", "samples"])
        self.assertEqual(self.new_schema.fields[(3, 2)]["type"], "uint32")
        self.assertTrue(self.new_schema.fields[(3, 2)]["repeated"])
        self.assertEqual(self.new_schema.fields[(3, 3)]["type"], "bool")

        module_spec = importlib.util.spec_from_file_location("fixture_models", self.appended / "orion_models.py")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        models = module.declare_models(declarative_base())
        columns = models["OrionControls"].__table__.columns
        sql = (self.appended / "orion_db_init.sql").read_text()
        prisma = (self.appended / "orion.prisma").read_text()
        for name, sql_type, prisma_type in (("samples", "bigint[]", "BigInt[]"), ("active", "boolean", "Boolean?")):
            self.assertEqual(str(columns[name].type.compile(dialect=postgresql.dialect())).lower(), sql_type)
            self.assertRegex(sql, rf"(?m)^\s+{name}\s+{re.escape(sql_type)},$")
            self.assertRegex(prisma, rf"(?m)^\s+{name}\s+{re.escape(prisma_type)}$")
            self.assertNotRegex((self.baseline / "orion_db_init.sql").read_text(), rf"(?m)^\s+{name}\s+")

        packet = next(packet for packet in json.loads((self.appended / "can.json").read_text()) if packet["packet_id"] == 257)
        self.assertEqual((packet["from"], packet["to"], packet["bus"], packet["data_length"], packet["quantity"]), (["VCU"], ["BEVO"], "Critical", 2, 2))
        self.assertEqual(packet["bytes"][0]["protobuf"]["proto_id"], 2)
        self.assertEqual(packet["bytes"][1]["bitfield_encoding"], [{"protobuf_field": "active", "bit_index": 0, "proto_id": 3}])

    def test_compiled_bevo_tables_emit_data_decodable_by_the_server_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet.pb"
            result = subprocess.run([str(self.encoder), str(output)], check=True, capture_output=True, text=True)
            compiled = json.loads(result.stdout)
            packet = GetMessageClass(self.new_schema.descriptor).FromString(output.read_bytes())
        self.assertEqual((packet.packet_id, packet.time, packet.controls.speed), (123, 456, 12.5))
        self.assertEqual(list(packet.controls.samples), [7, 0])
        self.assertFalse(packet.controls.active)
        self.assertEqual(compiled["from"], ["VCU"])
        self.assertEqual(compiled["quantity"], 2)
        self.assertEqual(compiled["bit_index"], 0)
        self.assertIn(["samples", 2], compiled["repeated_fields"])
        for name in ("controls.speed", "controls.samples[0]", "controls.samples[1]", "controls.active"):
            self.assertIn(name, compiled["headers"])
        old_packet = GetMessageClass(self.old_schema.descriptor).FromString(packet.SerializeToString())
        self.assertEqual(old_packet.controls.speed, packet.controls.speed)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
