"""Backend logic tests for the spec editor: JSON round-trip fidelity,
digest-guarded saves, and refusal paths. The page itself is exercised by
hand — everything it can break is re-checked here and by //lib/spec."""

import pathlib
import tempfile
import unittest

from tools.spec import editor

BUSES = """# proto-file: lib/spec/proto/can_spec.proto
# proto-message: lhre.canspec.SpecFile

bus {
  name: "Critical"
  bitrate_bps: 1000000
}
"""

GROUPS = """# proto-file: lib/spec/proto/can_spec.proto
# proto-message: lhre.canspec.SpecFile

groups {
  group {
    name: "Vehicle"
    next_free_id: 2
  }
}
"""

MESSAGES = """# proto-file: lib/spec/proto/can_spec.proto
# proto-message: lhre.canspec.SpecFile

message {
  name: "STATUS"
  can_id: 256
  from_board: "VCU"
  bus: "Critical"
  dlc: 8
  frequency_hz: 10.0
  signal {
    name: "speed"
    encoding {
      bit_length: 16
      scale: 0.01
    }
    logical {
      physical {
        unit: "kph"
      }
    }
    telemetry {
      group: "Vehicle"
      field: "speed"
      id: 1
    }
  }
}
"""


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        spec_dir = self.root / editor.SPEC_DIR / "messages"
        spec_dir.mkdir(parents=True)
        (self.root / editor.SPEC_DIR / "buses.textproto").write_text(BUSES)
        (self.root / editor.SPEC_DIR / "groups.textproto").write_text(GROUPS)
        (spec_dir / "vcu.textproto").write_text(MESSAGES)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self):
        return editor.spec_payload(self.root)

    def test_json_roundtrip_is_canonical_identity(self):
        p = self.payload()
        written, _ = editor.save_files(self.root, p["files"], p["digest"])
        self.assertEqual(written, [], "untouched spec must save as a no-op")

    def test_save_writes_canonical_form(self):
        p = self.payload()
        msg = p["files"]["lib/spec/messages/vcu.textproto"]["message"][0]
        msg["description"] = "now with a description"
        written, _ = editor.save_files(self.root, p["files"], p["digest"])
        self.assertEqual(written, ["lib/spec/messages/vcu.textproto"])
        text = (self.root / "lib/spec/messages/vcu.textproto").read_text()
        self.assertIn("now with a description", text)
        self.assertTrue(text.startswith("# proto-file:"), "must go through the canonical serializer")

    def test_save_rejects_stale_digest(self):
        p = self.payload()
        (self.root / editor.SPEC_DIR / "buses.textproto").write_text(
            BUSES.replace("1000000", "500000"))
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            editor.save_files(self.root, p["files"], p["digest"])

    def test_save_rejects_invalid_spec(self):
        p = self.payload()
        msg = p["files"]["lib/spec/messages/vcu.textproto"]["message"][0]
        msg["signal"][0]["telemetry"]["id"] = 7  # above next_free_id
        with self.assertRaisesRegex(ValueError, "validation failed"):
            editor.save_files(self.root, p["files"], p["digest"])

    def test_save_rejects_paths_outside_spec_dir(self):
        p = self.payload()
        p["files"]["lib/spec/../evil.textproto"] = {}
        with self.assertRaisesRegex(ValueError, "refusing path"):
            editor.save_files(self.root, p["files"], p["digest"])
        del p["files"]["lib/spec/../evil.textproto"]
        p["files"]["boards/VCU/BUILD.bazel"] = {}
        with self.assertRaisesRegex(ValueError, "refusing path"):
            editor.save_files(self.root, p["files"], p["digest"])

    def test_new_message_file(self):
        p = self.payload()
        p["files"]["lib/spec/messages/hvc.textproto"] = {
            "message": [{
                "name": "HVC_STATUS", "can_id": 512, "from_board": "HVC",
                "bus": "Critical", "dlc": 8,
                "signal": [{
                    "name": "ok",
                    "encoding": {"start_bit": 0, "bit_length": 1},
                    "logical": {"boolean": False},
                }],
            }],
        }
        written, _ = editor.save_files(self.root, p["files"], p["digest"])
        self.assertIn("lib/spec/messages/hvc.textproto", written)
        self.assertIn("HVC_STATUS",
                      (self.root / "lib/spec/messages/hvc.textproto").read_text())


if __name__ == "__main__":
    unittest.main()
