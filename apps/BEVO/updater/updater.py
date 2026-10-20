"""Select and install prebuilt core releases; never process telemetry."""

import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import urllib.parse

from apps.BEVO.updater.oci import Registry, fetch, verify
from lib.telemetry.contract import Manifest, release_compatible


METADATA_FILES = {"pi_core_manifest.json", "schema_contract.json", "schema_descriptor.pb", "can.json"}
EXECUTABLES = {"cand", "loggerd", "publishd"}
MAX_RELEASE_BYTES = 128 * 1024 * 1024


def unpack(data, allowed, limit):
    output = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("unsafe release archive path")
            name = str(path)
            if member.isdir() and name in (".", "bin"):
                continue
            if not member.isfile() or name not in allowed or name in output:
                raise ValueError("unexpected release archive entry")
            total += member.size
            if total > limit:
                raise ValueError("expanded release exceeds size limit")
            output[name] = archive.extractfile(member).read()
    if output.keys() != allowed:
        raise ValueError("incomplete release archive")
    return output


def release_schema(files):
    release = json.loads(files["pi_core_manifest.json"])
    if release.get("format_version") != 1 or release.get("platform") != "linux/arm64" or set(release["executables"]) != EXECUTABLES:
        raise ValueError("unsupported release metadata")
    for name, key in (("schema_contract.json", "schema_contract_sha256"), ("schema_descriptor.pb", "schema_descriptor_sha256"), ("can.json", "can_json_sha256")):
        verify(files[name], "sha256:" + release[key])
    schema = Manifest(files["schema_contract.json"])
    if release["schema_id"] != schema.schema_id:
        raise ValueError("release schema identity mismatch")
    return release, schema


def validate_binaries(files, metadata):
    for name in EXECUTABLES:
        data = files["bin/" + name]
        verify(data, "sha256:" + metadata["executables"][name]["sha256"])
        if len(data) < 20 or data[:6] != b"\x7fELF\x02\x01" or int.from_bytes(data[18:20], "little") != 183:
            raise ValueError("core executable is not an AArch64 ELF")


class Updater:
    def __init__(self, root, registry):
        self.root = Path(root).resolve()
        self.registry = registry

    def installed(self):
        current = self.root / "current"
        if not current.exists():
            return None
        files = {name: (current / name).read_bytes() for name in METADATA_FILES}
        metadata, schema = release_schema(files)
        binaries = {"bin/" + name: (current / "bin" / name).read_bytes() for name in EXECUTABLES}
        validate_binaries(binaries, metadata)
        return schema

    def choose(self, profile, upgrade=False):
        if profile.get("format_version") != 1:
            raise ValueError("unsupported server profile")
        server = Manifest(profile["schema"])
        installed = self.installed()
        if installed and release_compatible(installed, server) and not upgrade:
            return {"action": "keep", "schema_id": installed.schema_id, "server_schema_id": server.schema_id}
        for reference in profile["releases"]:
            manifest = self.registry.manifest(reference)
            files = unpack(self.registry.blob(manifest["layers"][0], 2 * 1024 * 1024), METADATA_FILES, 2 * 1024 * 1024)
            metadata, schema = release_schema(files)
            if release_compatible(schema, server):
                return {"action": "install", "reference": reference, "schema_id": schema.schema_id,
                        "server_schema_id": server.schema_id, "files": files, "metadata": metadata, "image": manifest}
        raise ValueError("no registry release is compatible with the authoritative server schema")

    def install(self, selection):
        binaries = unpack(self.registry.blob(selection["image"]["layers"][1], MAX_RELEASE_BYTES), {"bin/" + name for name in EXECUTABLES}, MAX_RELEASE_BYTES)
        validate_binaries(binaries, selection["metadata"])
        return self.activate(dict(selection["files"], **binaries))

    def activate(self, files):
        metadata, _ = release_schema(files)
        validate_binaries(files, metadata)
        identifier = hashlib.sha256(files["pi_core_manifest.json"]).hexdigest()
        releases = self.root / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        destination = releases / identifier
        previous = (self.root / "current").resolve() if (self.root / "current").exists() else None
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=releases) as temporary:
            stage = Path(temporary) / "release"
            (stage / "bin").mkdir(parents=True)
            for name, data in files.items():
                path = stage / name
                with path.open("wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                path.chmod(0o755 if name.startswith("bin/") else 0o644)
            if destination.exists():
                for name, data in files.items():
                    if (destination / name).read_bytes() != data:
                        raise ValueError("existing release was modified")
            else:
                stage.rename(destination)
        link = self.root / ".current-next"
        link.unlink(missing_ok=True)
        link.symlink_to(Path("releases") / identifier)
        os.replace(link, self.root / "current")
        for directory in releases.iterdir():
            if re.fullmatch(r"[a-f0-9]{64}", directory.name) and directory.is_dir() and not directory.is_symlink() and directory not in (destination, previous):
                shutil.rmtree(directory)
        return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "update", "override", "clear-override"))
    parser.add_argument("--root", type=Path, default=Path("/opt/bevo/core"))
    parser.add_argument("--server-url")
    parser.add_argument("--repository", default="ghcr.io/longhornracingelectric/bevo-core")
    parser.add_argument("--release", type=Path, help="admin override directory containing a complete release")
    parser.add_argument("--upgrade", action="store_true", help="select the first compatible release even if the installed release suffices")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        override = args.root / "override.json"
        updater = Updater(args.root, Registry(args.repository, os.getenv("BEVO_REGISTRY_USER", ""), os.getenv("BEVO_REGISTRY_TOKEN", "")))
        if args.command == "clear-override":
            override.unlink(missing_ok=True)
            print(json.dumps({"action": "override-cleared"}))
            return
        if args.command == "override":
            if args.release is None:
                parser.error("override requires --release")
            names = METADATA_FILES | {"bin/" + name for name in EXECUTABLES}
            files = {name: (args.release / name).read_bytes() for name in names}
            release_schema(files)
            validate_binaries(files, json.loads(files["pi_core_manifest.json"]))
            temporary = override.with_suffix(".tmp")
            temporary.write_text(json.dumps({"source": str(args.release.resolve()), "schema_id": Manifest(files["schema_contract.json"]).schema_id,
                                             "release_id": hashlib.sha256(files["pi_core_manifest.json"]).hexdigest()}))
            os.replace(temporary, override)
            destination = updater.activate(files)
            print(json.dumps({"action": "admin-override", "release": str(destination), "restart_required": True}))
            return
        if override.exists():
            pinned = json.loads(override.read_text())
            if updater.installed() is None or (updater.root / "current").resolve().name != pinned["release_id"]:
                raise ValueError("admin override is pending or damaged; reapply it or explicitly clear it")
            print(json.dumps({"action": "admin-override-active", **pinned}))
            return
        if not args.server_url:
            parser.error("check/update requires --server-url")
        server_token = os.getenv("BEVO_SERVER_TOKEN")
        server_headers = {"Authorization": "Bearer " + server_token} if server_token else {}
        profile = json.loads(fetch(args.server_url, 2 * 1024 * 1024, server_headers))
        descriptor_url = urllib.parse.urljoin(args.server_url, "schema_descriptor.pb")
        verify(fetch(descriptor_url, 2 * 1024 * 1024, server_headers), "sha256:" + profile["schema_descriptor_sha256"])
        selection = updater.choose(profile, args.upgrade)
        if args.command == "update" and selection["action"] == "install":
            selection["release"] = str(updater.install(selection))
            selection["restart_required"] = True
        print(json.dumps({key: value for key, value in selection.items() if key not in ("files", "metadata", "image")}))


if __name__ == "__main__":
    main()
