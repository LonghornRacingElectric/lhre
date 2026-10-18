"""Rate annotations must preserve CAN decoding and the protobuf wire contract."""

import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import generate_can_json as gen
import generate_can_proto as proto


class SignalRatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.packets = Path(self.temp.name) / "packets.csv"
        self.bits = Path(self.temp.name) / "bits.csv"
        self.write_bits("Enabled; enabled #1", "Fault; fault #2")

    def write_bits(self, *cells):
        with self.bits.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Bitfield", *[f"b[{i}]" for i in range(8)]])
            writer.writerow(["status", *cells, *[""] * (8 - len(cells))])

    def write_packet(self, cell, frequency="100", quantity="1"):
        with self.packets.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "CAN ID", "From", "To", "Packet Info", "Frequency (Hz)",
                "Data Length Code (DLC)", "Quantity", "Data[0]", "Bus",
            ])
            writer.writeheader()
            writer.writerow({
                "CAN ID": "0x100", "From": "VCU", "To": "Pi",
                "Packet Info": "Test packet", "Frequency (Hz)": frequency,
                "Data Length Code (DLC)": "2", "Quantity": quantity,
                "Data[0]": cell, "Bus": "Critical",
            })

    def load(self):
        return gen.process_csv(str(self.packets),
                               gen.load_bitfield_definitions(str(self.bits)),
                               str(self.bits))

    def test_rates_preserve_array_indices_and_proto(self):
        base = "Voltage[i+1] (uint16, 0.0001V); cells_v[2] (float) #4"
        self.write_packet(base, quantity="33")
        original = self.load()
        expected_proto, _ = proto.generate_proto_text(
            proto.parse_can_model_to_partitions(original), "Orion")
        for cell in (
            "Voltage[i+1] (uint16, 0.0001V) [uplink_hz=10, log_hz=0]; cells_v[2] (float) #4",
            base + " [uplink_hz=10, log_hz=0]",
        ):
            with self.subTest(cell=cell):
                self.write_packet(cell, quantity="33")
                packets = self.load()
                signal = packets[0]["bytes"][0]
                self.assertEqual(signal.pop("uplink_hz"), 10)
                self.assertEqual(signal.pop("log_hz"), 0)
                self.assertEqual(packets, original)
                self.assertEqual(signal["protobuf"]["field_index"], 2)
                self.assertTrue(signal["protobuf"]["repeated"])
                actual_proto, _ = proto.generate_proto_text(
                    proto.parse_can_model_to_partitions(self.load()), "Orion")
                self.assertEqual(actual_proto, expected_proto)

    def test_omitted_rates_add_no_limit_including_event_packets(self):
        for frequency in ("100", "0", "NA"):
            with self.subTest(frequency=frequency):
                self.write_packet("Speed (uint16); vehicle_speed (float)", frequency)
                signal = self.load()[0]["bytes"][0]
                self.assertNotIn("uplink_hz", signal)
                self.assertNotIn("log_hz", signal)

    def test_rates_are_independent_and_allow_fractional_hz(self):
        for annotation, expected in (
            ("[uplink_hz=0.25]", {"uplink_hz": 0.25}),
            ("[log_hz=1e2]", {"log_hz": 100}),
            ("[uplink_hz=0] [log_hz=200]", {"uplink_hz": 0, "log_hz": 200}),
        ):
            with self.subTest(annotation=annotation):
                self.write_packet(f"Speed (uint16) {annotation}; vehicle_speed (float)")
                signal = self.load()[0]["bytes"][0]
                rates = {k: signal[k] for k in ("uplink_hz", "log_hz") if k in signal}
                self.assertEqual(rates, expected)

    def test_bit_rates_override_parent_defaults(self):
        self.write_bits("Enabled [uplink_hz=0]; enabled #1",
                        "Fault; fault [log_hz=0.5] #2")
        self.write_packet("Status (bitfield, status) [uplink_hz=10, log_hz=50]")
        signals = {s["name"]: s for s in self.load()[0]["bytes"]}
        for name, uplink, log in (("enabled", 0, 50), ("fault", 10, 0.5), ("Status", 10, 50)):
            self.assertEqual(signals[name]["uplink_hz"], uplink)
            self.assertEqual(signals[name]["log_hz"], log)
        self.assertEqual(signals["fault"]["protobuf"]["proto_id"], 2)

    def test_bitfield_updater_preserves_annotations_and_ids(self):
        self.write_bits("Enabled; enabled [uplink_hz=0]", "Fault [log_hz=5]; fault")
        ids = {"DiagnosticsLow": {"enabled": 1, "fault": 2}}
        self.assertEqual(proto.write_back_bitfield_ids(str(self.bits), ids), 2)
        annotated = self.bits.read_text()
        self.assertEqual(proto.write_back_bitfield_ids(str(self.bits), ids), 0)
        self.assertEqual(self.bits.read_text(), annotated)
        bits = gen.load_bitfield_definitions(str(self.bits))["status"]
        self.assertEqual(bits[0]["proto_id"], 1)
        self.assertEqual(bits[0]["uplink_hz"], 0)
        self.assertEqual(bits[1]["proto_id"], 2)
        self.assertEqual(bits[1]["log_hz"], 5)

    def test_updaters_do_not_duplicate_ids_before_rate_annotations(self):
        self.write_bits("Enabled; enabled #1 [uplink_hz=0]")
        self.assertEqual(proto.write_back_bitfield_ids(
            str(self.bits), {"DiagnosticsLow": {"enabled": 1}}), 0)
        self.write_packet("Speed (uint16); wheel_speed (float) #4 [log_hz=100]")
        ids = {"Dynamics": {"wheel_speed": 4}}
        self.assertEqual(proto.write_back_proto_ids(str(self.packets), ids), 0)
        self.write_packet("Speed (uint16); wheel_speed (float) [log_hz=100]")
        self.assertEqual(proto.write_back_proto_ids(str(self.packets), ids), 1)
        self.assertEqual(proto.write_back_proto_ids(str(self.packets), ids), 0)
        signal = self.load()[0]["bytes"][0]
        self.assertEqual(signal["log_hz"], 100)
        self.assertEqual(signal["protobuf"]["proto_id"], 4)

    def test_invalid_rates_fail_generation_with_location(self):
        for annotation in (
            "[uplink_hz=-1]", "[log_hz=nan]", "[log_hz=inf]",
            "[log_hz=1e999]", "[log_hz=fast]", "[log_hz=]",
            "[log_hz=1, log_hz=2]", "[log_hz=1] [log_hz=2]",
            "[uplnk_hz=10]", "[uplink_hz]", "[log_hz=1,]",
            "[uplink_hz=10", "uplink_hz=10]",
        ):
            with self.subTest(annotation=annotation):
                self.write_packet(f"Speed (uint16) {annotation}; vehicle_speed (float)")
                with self.assertRaisesRegex(gen.RateMetadataError, r"row 2, ID 0x100, Data\[0\]"):
                    self.load()
        self.write_bits("Enabled; enabled [log_hz=-1]")
        with self.assertRaisesRegex(gen.RateMetadataError, "row 2, bit 0"):
            gen.load_bitfield_definitions(str(self.bits))

    def test_cli_emits_rates_and_rejects_invalid_input(self):
        output = Path(self.temp.name) / "can.json"
        command = [sys.executable, gen.__file__, str(self.packets), str(self.bits), str(output)]
        self.write_packet("Speed (uint16) [uplink_hz=10, log_hz=0]; vehicle_speed (float)")
        subprocess.run(command, check=True, capture_output=True, text=True)
        signal = json.loads(output.read_text())[0]["bytes"][0]
        self.assertEqual(signal["uplink_hz"], 10)
        self.assertEqual(signal["log_hz"], 0)
        self.write_packet("Speed (uint16) [log_hz=-1]; vehicle_speed (float)")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Data[0]", result.stderr)

    def test_can_budget_counts_quantity_and_ignores_signal_rates(self):
        self.write_packet("Speed (uint16) [uplink_hz=0, log_hz=0]; vehicle_speed (float)", quantity="3")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            gen.calculate_bus_utilization(self.load(), "Critical")
        # (2 payload bytes * 8 + 47 metadata bits + 7 stuff bits) * 100 Hz * 3 IDs.
        self.assertIn("21,000 bits/sec", output.getvalue())


if __name__ == "__main__":
    unittest.main()
