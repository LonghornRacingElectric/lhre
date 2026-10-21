import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from apps.BEVO.updater.oci import Registry, SafeRedirect, verify
from apps.BEVO.updater.updater import EXECUTABLES, METADATA_FILES, Updater, main, unpack
from lib.telemetry.contract import canonical_json
from lib.telemetry.testing import fixture_schema


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def release(schema, marker=b"release"):
    executable = b"\x7fELF\x02\x01" + bytes(12) + b"\xb7\x00" + marker
    files = {"bin/" + name: executable + name.encode() for name in EXECUTABLES}
    files.update({"schema_contract.json": canonical_json(schema.manifest).encode(), "schema_descriptor.pb": schema.descriptor_set, "can.json": b"[]"})
    metadata = {
        "format_version": 1, "platform": "linux/arm64", "schema_id": schema.schema_id,
        "executables": {name: {"sha256": digest(files["bin/" + name])} for name in EXECUTABLES},
        "schema_contract_sha256": digest(files["schema_contract.json"]),
        "schema_descriptor_sha256": digest(files["schema_descriptor.pb"]), "can_json_sha256": digest(files["can.json"]),
    }
    files["pi_core_manifest.json"] = canonical_json(metadata).encode()
    return files


class FakeRegistry:
    def __init__(self, versions):
        self.versions = versions
        self.requested = []

    def manifest(self, reference):
        self.requested.append(reference)
        self.files = self.versions[reference]
        return {"layers": [0, 1]}

    def blob(self, layer, limit):
        return archive({name: data for name, data in self.files.items() if (name in METADATA_FILES) == (layer == 0)})


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.schema = fixture_schema()

    def test_current_compatible_release_is_retained_without_registry_download(self):
        registry = FakeRegistry({})
        updater = Updater(self.root, registry)
        updater.activate(release(self.schema))
        profile = {"format_version": 1, "schema": fixture_schema(extra=True).manifest, "releases": []}
        self.assertEqual(updater.choose(profile)["action"], "keep")
        self.assertEqual(registry.requested, [])

    def test_selects_compatible_prebuilt_release_and_activates_all_core_files_together(self):
        registry = FakeRegistry({"bad": release(fixture_schema(value_type="string")), "good": release(self.schema)})
        updater = Updater(self.root, registry)
        profile = {"format_version": 1, "schema": self.schema.manifest, "releases": ["bad", "good"]}
        selected = updater.choose(profile)
        self.assertEqual(selected["reference"], "good")
        destination = updater.install(selected)
        self.assertEqual((self.root / "current").resolve(), destination)
        self.assertEqual(updater.installed().schema_id, self.schema.schema_id)
        self.assertTrue((destination / "bin/cand").stat().st_mode & 0o111)

    def test_incompatible_registry_does_not_replace_installed_release(self):
        updater = Updater(self.root, FakeRegistry({"bad": release(fixture_schema(value_type="string"))}))
        original = updater.activate(release(self.schema))
        profile = {"format_version": 1, "schema": fixture_schema(unit="mV").manifest, "releases": ["bad"]}
        with self.assertRaises(ValueError):
            updater.choose(profile)
        self.assertEqual((self.root / "current").resolve(), original)

    def test_tampered_binary_never_activates(self):
        updater = Updater(self.root, FakeRegistry({}))
        original = updater.activate(release(self.schema))
        tampered = release(self.schema, b"next")
        tampered["bin/cand"] += b"tampered"
        with self.assertRaises(ValueError):
            updater.activate(tampered)
        self.assertEqual((self.root / "current").resolve(), original)

    def test_interrupted_executable_download_preserves_the_active_release(self):
        registry = FakeRegistry({"next": release(self.schema, b"next")})
        updater = Updater(self.root, registry)
        original = updater.activate(release(self.schema))
        selection = updater.choose({"format_version": 1, "schema": self.schema.manifest, "releases": ["next"]}, upgrade=True)
        with patch.object(registry, "blob", side_effect=urllib.error.URLError("download interrupted")):
            with self.assertRaises(urllib.error.URLError):
                updater.install(selection)
        self.assert_active_release_unchanged(updater, original)
        self.assertEqual(set((self.root / "releases").iterdir()), {original})

    def test_staging_write_failure_preserves_the_active_release_and_cleans_staging(self):
        updater = Updater(self.root, FakeRegistry({}))
        original = updater.activate(release(self.schema))
        with patch("apps.BEVO.updater.updater.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                updater.activate(release(self.schema, b"next"))
        self.assert_active_release_unchanged(updater, original)
        self.assertEqual(set((self.root / "releases").iterdir()), {original})

    def test_failed_activation_preserves_the_active_release_and_can_be_retried(self):
        updater = Updater(self.root, FakeRegistry({}))
        original = updater.activate(release(self.schema))
        with patch("apps.BEVO.updater.updater.os.replace", side_effect=PermissionError("activation denied")):
            with self.assertRaises(PermissionError):
                updater.activate(release(self.schema, b"next"))
        self.assert_active_release_unchanged(updater, original)
        self.assertFalse(any(path.name.startswith(".staging-") for path in (self.root / "releases").iterdir()))
        destination = updater.activate(release(self.schema, b"next"))
        self.assertEqual((self.root / "current").resolve(), destination)
        self.assertNotEqual(destination, original)
        for name in EXECUTABLES:
            self.assertEqual((self.root / "current" / "bin" / name).read_bytes(), release(self.schema, b"next")["bin/" + name])

    def assert_active_release_unchanged(self, updater, original):
        self.assertEqual((self.root / "current").resolve(), original)
        self.assertEqual(updater.installed().schema_id, self.schema.schema_id)
        for name in EXECUTABLES:
            self.assertEqual((self.root / "current" / "bin" / name).read_bytes(), release(self.schema)["bin/" + name])

    def test_storage_retains_only_current_and_previous_release(self):
        updater = Updater(self.root, FakeRegistry({}))
        paths = [updater.activate(release(self.schema, str(version).encode())) for version in range(4)]
        self.assertEqual(set((self.root / "releases").iterdir()), set(paths[-2:]))

    def test_admin_override_is_persistent_and_never_contacts_server(self):
        source = self.root / "manual"
        (source / "bin").mkdir(parents=True)
        for name, data in release(self.schema).items():
            (source / name).write_bytes(data)
        with patch("sys.argv", ["updater", "override", "--root", str(self.root), "--release", str(source)]), patch("sys.stdout", new_callable=io.StringIO):
            main()
        with patch("sys.argv", ["updater", "update", "--root", str(self.root)]), patch("apps.BEVO.updater.updater.fetch") as reader, patch("sys.stdout", new_callable=io.StringIO) as output:
            main()
            reader.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["action"], "admin-override-active")
        with patch("sys.argv", ["updater", "clear-override", "--root", str(self.root)]), patch("sys.stdout", new_callable=io.StringIO):
            main()
        self.assertFalse((self.root / "override.json").exists())

    def test_archive_traversal_and_missing_files_are_rejected(self):
        for files in ({"../outside": b"bad"}, {"can.json": b"[]"}):
            with self.assertRaises(ValueError):
                unpack(archive(files), METADATA_FILES, 1024)

    def test_oci_requires_pinned_repository_and_valid_digests(self):
        registry = Registry("ghcr.io/org/bevo", reader=lambda *args: self.fail("must not download"))
        for reference in ("ghcr.io/org/bevo:latest", "ghcr.io/other/bevo@sha256:" + "a" * 64):
            with self.assertRaises(ValueError):
                registry.manifest(reference)
        with self.assertRaises(ValueError):
            verify(b"tampered", "sha256:" + "a" * 64)

    def test_oci_manifest_and_layers_are_verified_before_use(self):
        config = canonical_json({"os": "linux", "architecture": "arm64"}).encode()
        image = canonical_json({"schemaVersion": 2, "config": {"digest": "sha256:" + digest(config), "size": len(config)}, "layers": [0, 1]}).encode()
        registry = Registry("ghcr.io/org/bevo", reader=lambda url, *args: image if "/manifests/" in url else config)
        self.assertEqual(len(registry.manifest("ghcr.io/org/bevo@sha256:" + digest(image))["layers"]), 2)

    def test_redirects_do_not_leak_registry_credentials(self):
        request = urllib.request.Request("https://ghcr.io/blob", headers={"Authorization": "Bearer secret"})
        redirected = SafeRedirect().redirect_request(request, None, 302, "", {}, "https://cdn.example/blob")
        self.assertFalse(redirected.has_header("Authorization"))
        with self.assertRaises(ValueError):
            SafeRedirect().redirect_request(request, None, 302, "", {}, "http://cdn.example/blob")


if __name__ == "__main__":
    unittest.main()
