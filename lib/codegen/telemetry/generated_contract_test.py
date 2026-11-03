from pathlib import Path
import re
import sys
import unittest

from google.protobuf import descriptor_pb2
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

from apps.telemetry.scripts.generate_schema import parse_sql_schema
from lib.codegen.telemetry.orion_models import declare_models
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
        self.assertEqual(actual, self.fields)
        for group, number in self.reserved_ids:
            container = containers[group]
            self.assertTrue(any(interval.start <= number < interval.end if not group.startswith("Enum:") else interval.start <= number <= interval.end for interval in container.reserved_range), (group, number))
        for group, name in self.reserved_names:
            wire_name = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() if group == "Root" else name
            self.assertIn(wire_name, containers[group].reserved_name)

    def test_generated_database_columns_match_the_locked_types(self):
        sql = (self.directory / "orion_db_init.sql").read_text()
        sql_tables = {
            table["name"]: {column["name"]: column for column in table["columns"]}
            for table in parse_sql_schema(sql)
        }
        sql_bodies = dict(re.findall(r"CREATE TABLE public\.(\w+) \((.*?)\);", sql, re.DOTALL))
        models = declare_models(declarative_base())
        sql_types = {
            "float": "real", "double": "double precision", "int32": "integer", "uint32": "bigint",
            "int64": "bigint", "uint64": "numeric", "bool": "boolean", "string": "text", "bytes": "bytea",
        }
        for model in models.values():
            table = model.__table__
            self.assertEqual(set(table.columns.keys()), set(sql_tables[table.name]), table.name)
        for (group, name), (_, field_type) in self.fields.items():
            if group.startswith("Enum:") or field_type.startswith("message "):
                continue
            table = models["OrionPacket" if group == "Root" else "Orion" + group].__table__
            repeated = field_type.startswith("repeated ")
            base_type = field_type.removeprefix("repeated ").split("[")[0]
            expected_type = sql_types.get(base_type, "integer") + ("[]" if repeated else "")
            self.assertEqual(str(table.columns[name].type.compile(dialect=postgresql.dialect())).lower(), expected_type, (group, name))
            self.assertRegex(
                sql_bodies[table.name],
                rf'(?m)^\s+"?{re.escape(name)}"?\s+{re.escape(expected_type)}(?:\s+NOT NULL)?,$',
            )
            column = sql_tables[table.name][name]
            self.assertEqual((column["type"], column["is_array"]), (expected_type.removesuffix("[]").split()[0], repeated), (group, name))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
