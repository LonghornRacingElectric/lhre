import copy
import unittest

from lib.telemetry.contract import Manifest, Schema, compare, release_plan, seal_manifest
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

    def test_plan_excludes_renamed_tag_without_changing_server_schema(self):
        producer = fixture_schema()
        changed = copy.deepcopy(producer.manifest)
        changed["fields"][2]["names"][-1] = "renamed_voltage"
        server = Manifest(seal_manifest(changed))
        original = copy.deepcopy(server.manifest)
        plan = release_plan({"projection_version": 1, "schema_id": producer.schema_id}, producer, server)
        self.assertEqual([entry["path"] for entry in plan["excluded"]], [[3, 1]])
        self.assertIn([3, 2], [entry["path"] for entry in plan["accepted"]])
        self.assertEqual(server.manifest, original)
        self.assertEqual(plan["server"], original)

    def test_plan_preserves_enum_numeric_intersection_and_rejects_legacy_publisher(self):
        producer = fixture_schema(enum={0: "OFF", 1: "ON", 2: "NEW"})
        metadata = {"projection_version": 1, "schema_id": producer.schema_id}
        plan = release_plan(metadata, producer, fixture_schema())
        enum = next(entry for entry in plan["accepted"] if entry["path"] == [3, 4])
        self.assertEqual(enum["enum_values"], [0, 1])
        with self.assertRaises(ValueError):
            release_plan({"schema_id": producer.schema_id}, producer, fixture_schema())


if __name__ == "__main__":
    unittest.main()
