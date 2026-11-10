import json
import sys
import unittest
from pathlib import Path

from apps.BEVO.release_metadata import metadata


class ReleaseMetadataTest(unittest.TestCase):
    def test_metadata_pins_exact_core_executables_and_shared_schema_artifacts(self):
        manifest, can_json, contract, descriptor = map(Path, sys.argv[1:5])
        binaries = [Path(path) for argument in sys.argv[5:] for path in argument.split()]
        actual = json.loads(manifest.read_bytes())
        self.assertEqual(actual, metadata(binaries, can_json, contract, descriptor))
        self.assertEqual(actual["platform"], "linux/arm64")
        self.assertEqual(actual["schema_id"], json.loads(contract.read_bytes())["schema_id"])
        self.assertNotIn("compatibility_protocol_version", actual)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
