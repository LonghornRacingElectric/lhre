from pathlib import Path
import re
import sys
import unittest

from google.protobuf import descriptor_pb2
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

from lib.codegen.telemetry.artemis_models import declare_models, POOLS
from lib.spec.ir.wire_compat import parse_lock
from lib.telemetry.contract import SCALAR_TYPES, Schema


class GeneratedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(sys.argv[1]).parent
        cls.schema = Schema(Path(sys.argv[1]).read_bytes(), (cls.directory / "schema_descriptor.pb").read_bytes())
        cls.fields, cls.reserved_ids, cls.reserved_names, _ = parse_lock(Path(sys.argv[2]).read_text())

    def test_emitted_descriptor_preserves_the_reviewed_wire_lock(self):
        scalar_names = {number: name for name, number in SCALAR_TYPES.items()}
        messages = {"Root": self.schema.descriptor}
        messages.update({field.message_type.name: field.message_type for field in self.schema.descriptor.fields if field.message_type})
        actual = {}
        for group, message in messages.items():
            for field in message.fields:
                if field.message_type:
                    name, field_type = field.message_type.name, "message " + field.message_type.name
                    self.assertEqual(field.name, re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower())
                else:
                    name = field.name
                    field_type = field.enum_type.name if field.enum_type else scalar_names[field.type]
                    if field.is_repeated:
                        group_id = next(root.number for root in self.schema.descriptor.fields if root.message_type == message)
                        layout = self.schema.fields[(group_id, field.number)]["layout"]
                        field_type = f"repeated {field_type}[{len(layout)}]"
                actual[(group, name)] = (field.number, field_type)
        descriptor = descriptor_pb2.FileDescriptorSet.FromString(self.schema.descriptor_set).file[0]
        containers = {"Root" if message.name == self.schema.descriptor.name else message.name: message for message in descriptor.message_type}
        for enum in descriptor.enum_type:
            group = "Enum:" + enum.name
            containers[group] = enum
            for value in enum.value:
                actual[(group, value.name)] = (value.number, "enum")
        # The lock also records bus-only enums that are not snapshot types.
        expected = {key: value for key, value in self.fields.items()
                    if not key[0].startswith("Enum:") or key[0] in containers}
        self.assertEqual(actual, expected)
        for group, number in self.reserved_ids:
            if group not in containers:
                continue
            container = containers[group]
            self.assertTrue(any(interval.start <= number < interval.end if not group.startswith("Enum:") else interval.start <= number <= interval.end for interval in container.reserved_range), (group, number))
        for group, name in self.reserved_names:
            if group not in containers:
                continue
            wire_name = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() if group == "Root" else name
            self.assertIn(wire_name, containers[group].reserved_name)

    def test_generated_database_columns_match_the_locked_types(self):
        sql = (self.directory / "artemis_db_init.sql").read_text()
        models = declare_models(declarative_base())
        by_table = {model.__tablename__: model.__table__ for model in models.values()}
        expected = {(group, name) for (group, name), (_, kind) in self.fields.items()
                    if group != "Root" and not group.startswith("Enum:") and not kind.startswith("message ")}
        seen = set()
        for pool in POOLS:
            table = by_table[pool["table"]]
            self.assertEqual(list(table.primary_key.columns.keys()), ["session_start_ms", "time"])
            self.assertFalse(table.foreign_keys)
            for field in pool["fields"]:
                path = tuple(field["path"])
                group_name = next(g["name"] for g in self.schema.manifest["groups"] if g["id"] == path[0])
                seen.add((group_name, field["name"]))
                self.assertEqual(str(table.columns[field["name"]].type.compile(dialect=postgresql.dialect())).lower(), field["sql_type"])
                self.assertIn(f"{field['name']} {field['sql_type']}", sql)
        self.assertEqual(seen, expected)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
