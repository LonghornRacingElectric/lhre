import copy
import unittest

from lib.telemetry.contract import Schema, compare, seal_manifest
from lib.telemetry.testing import fixture_schema


class ContractTest(unittest.TestCase):
    def test_additions_preserve_existing_field_definitions_in_both_directions(self):
        old, new = fixture_schema(), fixture_schema(extra=True)
        for source, target in ((old, new), (new, old)):
            accepted, _ = compare(source, target)
            self.assertEqual(set(accepted), set(old.fields))

    def test_type_unit_and_layout_changes_identify_only_affected_fields(self):
        server = fixture_schema()
        for producer, path in (
            (fixture_schema(value_type="string"), (3, 1)),
            (fixture_schema(unit="mV"), (3, 1)),
            (fixture_schema(layout=("right", "left")), (3, 3)),
            (fixture_schema(layout=("left", "right", "center")), (3, 3)),
        ):
            accepted, rejected = compare(producer, server)
            self.assertNotIn(path, accepted)
            self.assertIn(path, rejected)
            self.assertIn((3, 2), accepted)

    def test_enum_additions_report_the_shared_values(self):
        producer = fixture_schema(enum={0: "OFF", 1: "ON", 2: "NEW"})
        accepted, _ = compare(producer, fixture_schema())
        self.assertEqual(accepted[(3, 4)], [0, 1])

    def test_repurposed_enum_numbers_are_incompatible(self):
        producer = fixture_schema(enum={0: "OFF", 1: "OTHER"})
        accepted, rejected = compare(producer, fixture_schema())
        self.assertNotIn((3, 4), accepted)
        self.assertIn((3, 4), rejected)

    def test_descriptor_must_agree_with_identity_and_fields(self):
        schema = fixture_schema()
        changed = copy.deepcopy(schema.manifest)
        changed["fields"][2]["type"] = "double"
        with self.assertRaises(ValueError):
            Schema(changed, schema.descriptor_set)
        with self.assertRaises(ValueError):
            Schema(seal_manifest(changed), schema.descriptor_set)

    def test_field_declaration_order_does_not_change_identity(self):
        schema = fixture_schema()
        reordered = copy.deepcopy(schema.manifest)
        reordered["fields"].reverse()
        self.assertEqual(seal_manifest(reordered)["schema_id"], schema.schema_id)


if __name__ == "__main__":
    unittest.main()
