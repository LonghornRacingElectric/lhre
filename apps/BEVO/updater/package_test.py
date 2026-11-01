import copy
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest

from apps.BEVO.updater.oci import Registry
from apps.BEVO.updater.updater import EXECUTABLES, MAX_RELEASE_BYTES, METADATA_FILES, Updater, release_schema, unpack, validate_binaries
from lib.telemetry.contract import seal_manifest
from apps.telemetry.schema_registry.server import create_server, profile


class PackageTest(unittest.TestCase):
    def test_real_bazel_release_layers_match_metadata_and_executables(self):
        _, metadata_path, binary_path, _ = map(Path, sys.argv[1:])
        files = unpack(metadata_path.read_bytes(), METADATA_FILES, 2 * 1024 * 1024)
        metadata, _ = release_schema(files)
        binaries = unpack(binary_path.read_bytes(), {"bin/" + name for name in EXECUTABLES}, MAX_RELEASE_BYTES)
        validate_binaries(binaries, metadata)

    def test_updater_zip_runs_without_site_packages_or_checkout(self):
        archive = Path(sys.argv[1]).resolve()
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-I", "-S", str(archive), "--help"], cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("clear-override", result.stdout)

    def test_actual_oci_image_can_be_selected_and_installed_without_docker(self):
        layout = Path(sys.argv[4])
        descriptor = json.loads((layout / "index.json").read_bytes())["manifests"][0]
        def read_blob(url, limit, headers):
            digest = url.rsplit("/", 1)[-1].removeprefix("sha256:")
            return (layout / "blobs/sha256" / digest).read_bytes()
        registry = Registry("ghcr.io/org/bevo", reader=read_blob)
        reference = "ghcr.io/org/bevo@" + descriptor["digest"]
        metadata = unpack(Path(sys.argv[2]).read_bytes(), METADATA_FILES, 2 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            updater = Updater(directory, registry)
            selection = updater.choose({"format_version": 1, "schema": json.loads(metadata["schema_contract.json"]), "releases": [reference]})
            updater.install(selection)
            self.assertEqual(updater.installed().schema_id, selection["schema_id"])

            deployed = Path(directory) / "deployed-server"
            server = copy.deepcopy(json.loads(metadata["schema_contract.json"]))
            measurement = next(field for field in server["fields"] if len(field["path"]) == 2)
            excluded_path = measurement["path"]
            measurement["unit"] += "_different_unit"
            reconciled = updater.choose({"format_version": 1, "schema": seal_manifest(server), "releases": []})
            self.assertEqual(reconciled["action"], "reconcile")
            self.assertEqual([field["path"] for field in reconciled["excluded"]], [excluded_path])
            updater.install(reconciled)
            self.assertEqual(updater.installed().schema_id, selection["schema_id"])

            deployed.mkdir()
            (deployed / "schema_contract.json").write_text(json.dumps(seal_manifest(server)))
            (deployed / "schema_descriptor.pb").write_bytes(metadata["schema_descriptor.pb"])
            remaining_reads = None
            def snapshot():
                nonlocal remaining_reads
                if remaining_reads is not None:
                    if remaining_reads == 0:
                        raise ValueError("mixed deployed schemas")
                    remaining_reads -= 1
                return profile(deployed, {"releases": []})
            authority = create_server(snapshot, ("127.0.0.1", 0))
            thread = threading.Thread(target=authority.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{authority.server_address[1]}/profile.json"
                command = [sys.executable, "-I", "-S", str(Path(sys.argv[1]).resolve()), "update", "--root", directory, "--server-url", url]
                next(field for field in server["fields"] if field["path"] == excluded_path)["unit"] += "_next"
                (deployed / "schema_contract.json").write_text(json.dumps(seal_manifest(server)))
                update = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(update.returncode, 0, update.stderr)
                self.assertEqual(json.loads(update.stdout)["action"], "reconcile")
                plan = json.loads((Path(directory) / "current/reconciliation.json").read_bytes())
                self.assertEqual(plan["server"]["schema_id"], seal_manifest(server)["schema_id"])
                active = (Path(directory) / "current").resolve()
                next(field for field in server["fields"] if field["path"] == excluded_path)["unit"] += "_rolling"
                (deployed / "schema_contract.json").write_text(json.dumps(seal_manifest(server)))
                remaining_reads = 2
                failed = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual((Path(directory) / "current").resolve(), active)
            finally:
                authority.shutdown()
                authority.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
