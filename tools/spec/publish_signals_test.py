import json
from pathlib import Path
import tempfile
import unittest

from lib.codegen.telemetry import catalog
from tools.spec.publish_signals import publish


def sample(car="artemis", rate=50):
    return catalog.build(car, [{"signal": "dynamics.gps", "group": "dynamics", "value_type": "float",
                                "repeated": True, "module": "gateway", "unit": "deg",
                                "rate_hz": rate, "channel_type": "telemetry"}])


class PublishSignalsTest(unittest.TestCase):
    def test_identity_depends_on_content_not_format_or_order(self):
        first = sample()
        reordered = {key: first[key] for key in reversed(first)}
        self.assertEqual(catalog.validate(json.loads(json.dumps(reordered))), first)
        self.assertEqual(sample(rate=50), sample(rate=50.0))
        self.assertNotEqual(first["schema_version"], sample(rate=25)["schema_version"])
        self.assertNotEqual(first["schema_version"], sample(car="orion")["schema_version"])

    def test_idempotent_archive_new_versions_and_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "catalogs"
            source = Path(temporary) / "source.json"
            first = sample()
            source.write_text(json.dumps(first))
            result = publish(source, root)
            self.assertTrue(result["created"])
            pointer = root / "artemis/current.json"
            archive = root / f'artemis/{first["schema_version"]}.json'
            timestamps = (pointer.stat().st_mtime_ns, archive.stat().st_mtime_ns)
            self.assertEqual(publish(source, root)["created"], False)
            self.assertEqual(publish(source, root)["activated"], False)
            self.assertEqual(timestamps, (pointer.stat().st_mtime_ns, archive.stat().st_mtime_ns))
            second = sample(rate=25)
            source.write_text(json.dumps(second))
            publish(source, root)
            self.assertEqual(json.loads(pointer.read_text())["schema_version"], second["schema_version"])
            self.assertTrue(archive.exists())
            publish(archive, root)
            self.assertEqual(json.loads(pointer.read_text())["schema_version"], first["schema_version"])
            source.write_text(json.dumps(sample(car="orion")))
            publish(source, root)
            self.assertTrue((root / "orion/current.json").exists())

    def test_rejects_tampering_and_preserves_existing_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.json"
            value = sample()
            source.write_text(json.dumps(value))
            publish(source, temporary)
            destination = Path(temporary) / f'artemis/{value["schema_version"]}.json'
            destination.write_text("corrupt")
            with self.assertRaises(ValueError):
                publish(source, temporary)
            self.assertEqual(destination.read_text(), "corrupt")
            value["signals"][0]["rate_hz"] = 12
            source.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "content"):
                publish(source, temporary)

    def test_version_is_only_at_catalog_level(self):
        value = sample()
        self.assertTrue(all("schema_version" not in row for row in value["signals"]))
        self.assertEqual(catalog.validate(value), value)
        value["signals"][0]["schema_version"] = value["schema_version"]
        with self.assertRaisesRegex(ValueError, "only at catalog level"):
            catalog.validate(value)

    def test_invalid_car_cannot_escape_archive_root(self):
        with self.assertRaises(ValueError):
            sample(car="../orion")


if __name__ == "__main__":
    unittest.main()
