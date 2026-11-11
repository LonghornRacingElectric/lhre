import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from apps.BEVO.updater.package import archive
from apps.BEVO.updater.updater import unpack
from apps.BEVO.updater.oci import Registry
from apps.telemetry.schema_registry.bundle import FILES, validate
from apps.telemetry.schema_registry.catalog import Catalog


class CatalogTest(unittest.TestCase):
    def test_discovery_is_not_activation_and_registry_failure_preserves_catalog(self):
        files = unpack(Path(sys.argv[1]).read_bytes(), FILES, 8 * 1024 * 1024)
        release = json.loads(files["release.json"])
        release["revision"] = "a" * 40
        files["release.json"] = json.dumps(release).encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            for name, data in files.items():
                (assets / name).write_bytes(data)
            packed = root / "metadata.tar.gz"
            archive(packed, {name: assets / name for name in files})
            payload = packed.read_bytes()
            blob_id = "sha256:" + hashlib.sha256(payload).hexdigest()
            document = json.dumps({"schemaVersion": 2, "layers": [{"digest": blob_id, "size": len(payload)}]}).encode()
            available = True
            def fetch(url, limit, headers):
                if not available:
                    raise OSError("registry unavailable")
                return payload if "/blobs/" in url else document
            registry = Registry("ghcr.io/org/schemas", reader=fetch)
            catalog = Catalog({"repository": registry.repository, "cache": str(root / "cache")}, registry)
            reference = catalog.sync()
            _, schema, storage = validate(files)
            deployed = {"schema": schema.manifest, "can_json_sha256": "0" * 64, "storage_id": storage["storage_id"]}
            self.assertEqual(catalog.augment(deployed)["releases"], [])
            self.assertNotIn("active", catalog.index())
            deployed["can_json_sha256"] = release["can_json_sha256"]
            self.assertEqual(catalog.augment(deployed)["releases"], [release["bevo_release"]])
            self.assertEqual(catalog.index()["active"], reference)
            available = False
            with self.assertRaises(OSError):
                catalog.sync()
            self.assertEqual(catalog.augment(deployed)["releases"], [release["bevo_release"]])
            (catalog.directory(reference) / "schema_descriptor.pb").write_bytes(b"broken")
            with self.assertRaises(ValueError):
                catalog.read(reference)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
