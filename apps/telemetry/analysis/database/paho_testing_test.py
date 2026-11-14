import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from analysis.database import paho_testing
from analysis.database.paho_testing import DataTester
from stack.ingest.sensor_decode import decode_updates


class PahoFixturesTest(unittest.TestCase):
    def test_profiles_schema_selection_and_row_controls(self):
        viewer = DataTester(None, seed=42, value_profile="viewer")
        generic = DataTester(None, seed=42, value_profile="generic")
        desc = viewer.get_proto_desc(tables="controls")
        self.assertGreater(viewer.create_row(desc["controls"], 1)["apps1_travel"], 1)
        self.assertLess(generic.create_row(desc["controls"], 1)["apps1_travel"], 1)
        self.assertIn("controls_333hz", viewer.get_desc(db=True))
        trimmed = viewer.get_proto_desc(tables="controls_333hz", rm_cols="apps1_v")
        self.assertNotIn("apps1_v", trimmed["controls_333hz"])

        mqtt = Mock()
        messages = []
        mqtt.publish.side_effect = lambda topic, payload, **kwargs: messages.extend(decode_updates(payload))
        with patch.object(paho_testing.mqtt_client, "Client", return_value=mqtt), patch.object(paho_testing.time, "sleep") as sleep:
            paho_testing.main(["--rows", "3", "--delay", "0.01", "--profile", "viewer", "--schema-source", "orm", "--tables", "controls_333hz"])
        self.assertEqual(len(messages), 3)
        self.assertEqual(sleep.call_args_list, [call(.01)] * 3)
        self.assertTrue(all(set(row) == {"time", "sample_time_ns", "controls"} for row in messages))
        self.assertTrue(all(row["controls"]["apps1_travel"] > 1 for row in messages))

    def test_logger_csv_merge_mapping_and_fresh_field_presence(self):
        base = 1794484800000000001
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, headers, rows):
                with (root / name).open("w", newline="") as source:
                    writer = csv.writer(source)
                    writer.writerow(headers)
                    writer.writerows(rows)
            write("artemis_1_controls_333hz.csv",
                  ["time", "sample_time_ns", "updated_fields", "controls.apps1_travel", "controls.enable", "controls.apps2_travel"],
                  [[base // 1_000_000, base, json.dumps(["controls.apps1_travel", "controls.enable"]), "0", "false", "99"],
                   [(base + 2_000_000) // 1_000_000, base + 2_000_000, json.dumps(["controls.enable"]), "", "true", ""]])
            write("artemis_1_dynamics_external.csv",
                  ["time", "sample_time_ns", "updated_fields", "dynamics.gps[0]", "dynamics.gps[1]"],
                  [[(base + 1_000_000) // 1_000_000, base + 1_000_000, json.dumps(["dynamics.gps"]), "", ""]])
            tester = DataTester(None, csv_path=root)
            desc = tester.get_proto_desc()
            rows = [decode_updates(tester.create_proto_message(i, desc, use_csv=True).SerializeToString())[0] for i in range(3)]
            self.assertEqual([row["sample_time_ns"] for row in rows], [base, base + 1_000_000, base + 2_000_000])
            self.assertEqual(rows[0]["controls"], {"apps1_travel": 0, "enable": False})
            self.assertEqual(rows[1]["dynamics"], {"gps": []})
            self.assertEqual(rows[2]["controls"], {"enable": True})

            slots = [0, 1, 10, 11, 2, 3, 4, 5, 6, 7, 8, 9]
            write("mapped.csv", ["time", "sample_time_ns", "updated_fields", "pedal", *[f"pack.cells_v[{i}]" for i in slots]],
                  [[base // 1_000_000, base, json.dumps(["controls.apps1_travel", "pack.cells_v"]), "25", *[str(i) for i in slots]]])
            mapping = root / "mapping.json"
            mapping.write_text(json.dumps({"controls.apps1_travel": "pedal"}))
            tester = DataTester(None, csv_path=root / "mapped.csv", mapping_path=mapping)
            row = tester.create_row_from_csv()
            self.assertEqual(row["pack.cells_v"], list(range(12)))
            self.assertEqual(row["controls.apps1_travel"], 25)
            self.assertEqual(tester.create_row_from_csv(), row)


if __name__ == "__main__":
    unittest.main()
