import copy
import json
from pathlib import Path
import tempfile
import sys
import tarfile
from types import SimpleNamespace
import unittest

from apps.telemetry.schema_registry.server import profile
from apps.telemetry.schema_registry.deployment import DockerDeployment
from lib.telemetry.contract import Schema
from lib.telemetry.testing import fixture_schema


class ServerProfileTest(unittest.TestCase):
    def test_packaged_server_schema_is_at_the_deployment_reader_path(self):
        with tarfile.open(sys.argv[1]) as archive:
            files = {member.name.lstrip("./"): archive.extractfile(member).read() for member in archive if member.isfile()}
        self.assertEqual(set(files), {"schema/schema_contract.json", "schema/schema_descriptor.pb"})
        Schema(files["schema/schema_contract.json"], files["schema/schema_descriptor.pb"])

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

    def deployment(self, schemas, replace=False, stopped=False, mounted=False):
        state = [{"id": name, "image_id": "image-" + name, "image": "local:dev", "running": not stopped,
                  "restarting": False, "mounts": [{"Destination": "/schema"}] if mounted else []}
                 for name in ("ingest", "kafka-bridge")]
        calls = []
        def run(command, **kwargs):
            if command[1] == "inspect":
                inspected = copy.deepcopy(state)
                if replace and calls:
                    inspected[0]["id"] = "replacement"
                calls.append("inspect")
                return SimpleNamespace(stdout="\n".join(json.dumps(item) for item in inspected))
            self.assertEqual(command[1], "cp")
            container, source = command[2].split(":", 1)
            schema = schemas[container]
            value = json.dumps(schema.manifest).encode() if source.endswith(".json") else schema.descriptor_set
            Path(command[3]).write_bytes(value)
            return SimpleNamespace(stdout="")
        return DockerDeployment(runner=run)

    def test_deployment_uses_running_consumers_and_rejects_mixed_or_changing_images(self):
        old, new = fixture_schema(), fixture_schema(extra=True)
        configuration = {"releases": []}
        result, descriptor = self.deployment({"ingest": new, "kafka-bridge": new}).snapshot(configuration)
        self.assertEqual(result["schema"]["schema_id"], new.schema_id)
        self.assertEqual(descriptor, new.descriptor_set)
        self.assertEqual([item["id"] for item in result["deployment"]["consumers"]], ["ingest", "kafka-bridge"])
        for deployment in (
            self.deployment({"ingest": old, "kafka-bridge": new}),
            self.deployment({"ingest": new, "kafka-bridge": new}, replace=True),
            self.deployment({"ingest": new, "kafka-bridge": new}, stopped=True),
            self.deployment({"ingest": new, "kafka-bridge": new}, mounted=True),
        ):
            with self.assertRaises(ValueError):
                deployment.snapshot(configuration)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
