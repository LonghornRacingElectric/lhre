import json
from pathlib import Path
import tempfile
import unittest

from apps.telemetry.schema_registry.server import profile
from lib.telemetry.testing import fixture_schema


class ServerProfileTest(unittest.TestCase):
    def test_profile_uses_the_explicitly_selected_server_schema(self):
        schema = fixture_schema(extra=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema_contract.json").write_text(json.dumps(schema.manifest))
            (root / "schema_descriptor.pb").write_bytes(schema.descriptor_set)
            reference = "ghcr.io/org/bevo@sha256:" + "a" * 64
            result, descriptor = profile(root, {"releases": [reference]})
            self.assertEqual(result["schema"]["schema_id"], schema.schema_id)
            self.assertEqual(descriptor, schema.descriptor_set)
            self.assertEqual(result["releases"], [reference])
            with self.assertRaises(ValueError):
                profile(root, {"releases": ["ghcr.io/org/bevo:latest"]})
            (root / "schema_descriptor.pb").write_bytes(fixture_schema().descriptor_set)
            with self.assertRaises(ValueError):
                profile(root, {"releases": [reference]})


if __name__ == "__main__":
    unittest.main()
