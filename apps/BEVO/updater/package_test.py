from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from apps.BEVO.updater.oci import Registry
from apps.BEVO.updater.updater import EXECUTABLES, MAX_RELEASE_BYTES, METADATA_FILES, Updater, release_schema, unpack, validate_binaries


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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
