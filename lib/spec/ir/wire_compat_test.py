import unittest

from lib.spec.ir.wire_compat import compatibility_errors, parse_lock


class WireCompatTest(unittest.TestCase):
    def test_root_metadata_in_group_headers_is_part_of_the_contract(self):
        for attributes in ("snapshot_id=3 next_free_id=2", "next_free_id=2 snapshot_id=3"):
            with self.subTest(attributes=attributes):
                baseline = f"[Vehicle] {attributes}\nVehicle.speed 1 float\n"
                fields, _, _, high_water = parse_lock(baseline)
                self.assertEqual(fields[("Root", "Vehicle")], (3, "message Vehicle"))
                self.assertEqual(high_water, {"Vehicle": 2})
                self.assertEqual(compatibility_errors(baseline, "Root.Vehicle 3 message Vehicle\n" + baseline), [])
                self.assertTrue(compatibility_errors(baseline, baseline.replace("snapshot_id=3", "snapshot_id=4")))
                with self.assertRaisesRegex(ValueError, "conflicting wire lock entries"):
                    parse_lock("Root.Vehicle 4 message Vehicle\n" + baseline)

    def test_append_and_proper_deletion(self):
        baseline = "[Controls] next_free_id=2\nControls.enabled 1 bool\n"
        self.assertEqual(compatibility_errors(baseline, baseline + "Controls.new 2 float\n"), [])
        self.assertEqual(compatibility_errors(baseline, "Controls RESERVED_ID 1\nControls RESERVED_NAME enabled\n"), [])

    def test_reuse_type_change_and_lost_tombstone(self):
        baseline = "Controls.enabled 1 bool\nControls RESERVED_ID 2\n"
        for current in ("Controls.other 1 bool\n", "Controls.enabled 1 int32\n", "Controls.enabled 1 bool\n"):
            self.assertTrue(compatibility_errors(baseline, current))

    def test_retiring_a_root_group_protects_its_entire_numeric_subtree(self):
        baseline = "Root.Vehicle 3 message Vehicle\nVehicle.speed 1 float\nVehicle RESERVED_ID 2\n"
        current = "Root RESERVED_ID 3\nRoot RESERVED_NAME Vehicle\n"
        self.assertEqual(compatibility_errors(baseline, current), [])
        self.assertTrue(compatibility_errors(baseline, current.replace("Root RESERVED_ID 3\n", "")))

    def test_first_documented_external_bound_is_not_a_wire_type_change(self):
        self.assertEqual(compatibility_errors("Dynamics.gps 1 repeated float\n", "Dynamics.gps 1 repeated float[2]\n"), [])
        self.assertTrue(compatibility_errors("Dynamics.gps 1 repeated float[2]\n", "Dynamics.gps 1 repeated float[3]\n"))


if __name__ == "__main__":
    unittest.main()
