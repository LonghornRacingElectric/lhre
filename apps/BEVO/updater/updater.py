"""Select and install prebuilt core releases; never process telemetry."""

import argparse
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import time
import sys
import math
import signal
from contextlib import contextmanager


@contextmanager
def startup_deadline(seconds):
    def expired(*_):
        raise TimeoutError("startup synchronization deadline exceeded")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(.001, seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
import urllib.parse

from apps.BEVO.updater.oci import Registry, fetch, verify
from lib.telemetry.contract import Manifest, canonical_json, content_id, release_plan


METADATA_FILES = {"pi_core_manifest.json", "schema_contract.json", "schema_descriptor.pb", "can.json"}
EXECUTABLES = {"cand", "loggerd", "publishd"}
PLAN_FILE = "reconciliation.json"
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


def checked_plan(files, metadata, producer):
    plan = json.loads(files[PLAN_FILE])
    expected = release_plan(metadata, producer, Manifest(plan["server"]))
    if plan != expected:
        raise ValueError("reconciliation plan does not belong to this release")
    return plan


def identity_release(files):
    metadata, producer = release_schema(files)
    plan = release_plan(metadata, producer, producer)
    return dict(files, **{PLAN_FILE: canonical_json(plan).encode()})


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
        if (current / PLAN_FILE).exists():
            files[PLAN_FILE] = (current / PLAN_FILE).read_bytes()
            checked_plan(files, metadata, schema)
        return schema

    def choose(self, profile, upgrade=False):
        if profile.get("format_version") != 1:
            raise ValueError("unsupported server profile")
        server = Manifest(profile["schema"])
        installed = self.installed()
        prefer_deployed = profile.get("selection_policy") == "prefer_deployed"
        existing = None
        installed_metadata = None
        if installed:
            current = self.root / "current"
            files = {name: (current / name).read_bytes() for name in METADATA_FILES}
            metadata, _ = release_schema(files)
            installed_metadata = metadata
            try:
                plan = release_plan(metadata, installed, server)
            except ValueError:
                pass
            else:
                selection = self.selection("keep", files, metadata, plan)
                if not (current / PLAN_FILE).exists() or json.loads((current / PLAN_FILE).read_bytes()) != plan:
                    selection["action"] = "reconcile"
                    selection["files"].update({"bin/" + name: (current / "bin" / name).read_bytes() for name in EXECUTABLES})
                existing = selection
        if existing and not upgrade and not prefer_deployed:
            return existing
        if existing and prefer_deployed and profile.get("preferred_release_id") == content_id(installed_metadata) and installed.schema_id == server.schema_id and installed_metadata["can_json_sha256"] == profile["can_json_sha256"]:
            return existing
        for reference in profile["releases"]:
            manifest = self.registry.manifest(reference)
            files = unpack(self.registry.blob(manifest["layers"][0], 2 * 1024 * 1024), METADATA_FILES, 2 * 1024 * 1024)
            metadata, schema = release_schema(files)
            if prefer_deployed and (schema.schema_id != server.schema_id or metadata["can_json_sha256"] != profile["can_json_sha256"]):
                continue
            try:
                plan = release_plan(metadata, schema, server)
            except ValueError:
                continue
            if existing and metadata == installed_metadata:
                return existing
            return dict(self.selection("install", files, metadata, plan), reference=reference, image=manifest)
        if prefer_deployed and existing:
            return dict(existing, warning="no matching approved build; using installed intersection")
        raise ValueError("no approved GHCR release supports a usable intersection with the deployed server schema")

    def selection(self, action, files, metadata, plan):
        return {
            "action": action, "schema_id": plan["producer_schema_id"],
            "server_schema_id": plan["server"]["schema_id"],
            "projection_id": plan["plan_id"], "excluded": plan["excluded"],
            "files": dict(files, **{PLAN_FILE: canonical_json(plan).encode()}), "metadata": metadata,
        }

    def install(self, selection, confirm=None):
        if selection["action"] == "reconcile":
            if confirm is not None:
                confirm()
            return self.activate(selection["files"])
        binaries = unpack(self.registry.blob(selection["image"]["layers"][1], MAX_RELEASE_BYTES), {"bin/" + name for name in EXECUTABLES}, MAX_RELEASE_BYTES)
        validate_binaries(binaries, selection["metadata"])
        if confirm is not None:
            confirm()
        return self.activate(dict(selection["files"], **binaries))

    def activate(self, files):
        if files.keys() != METADATA_FILES | {"bin/" + name for name in EXECUTABLES} | {PLAN_FILE}:
            raise ValueError("incomplete or unexpected managed release files")
        metadata, producer = release_schema(files)
        validate_binaries(files, metadata)
        plan = checked_plan(files, metadata, producer)
        identifier = content_id({"release_id": content_id(metadata), "projection_id": plan["plan_id"]})
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


def synchronize(updater, args):
    server_token = os.getenv("BEVO_SERVER_TOKEN")
    server_headers = {"Authorization": "Bearer " + server_token} if server_token else {}
    if not args.server_url:
        raise ValueError("server URL is not configured")
    deadline = time.monotonic() + args.network_wait
    while True:
        try:
            profile = json.loads(fetch(args.server_url, 2 * 1024 * 1024, server_headers,
                                       timeout=min(5, max(0.1, deadline - time.monotonic()))) if args.command == "startup"
                                 else fetch(args.server_url, 2 * 1024 * 1024, server_headers))
            break
        except OSError:
            if args.command != "startup" or time.monotonic() >= deadline:
                raise
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    descriptor_url = urllib.parse.urljoin(args.server_url, "schema_descriptor.pb")
    verify(fetch(descriptor_url, 2 * 1024 * 1024, server_headers), "sha256:" + profile["schema_descriptor_sha256"])
    selection = updater.choose(profile, args.upgrade)
    def confirm_server():
        if json.loads(fetch(args.server_url, 2 * 1024 * 1024, server_headers)) != profile:
            raise ValueError("server schema/deployment or approved releases changed during update; retry")
    if args.command in ("update", "startup") and selection["action"] in ("install", "reconcile"):
        selection["release"] = str(updater.install(selection, confirm=confirm_server))
        selection["restart_required"] = True
    else:
        confirm_server()
    return {key: value for key, value in selection.items() if key not in ("files", "metadata", "image")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "update", "startup", "override", "clear-override"))
    parser.add_argument("--root", type=Path, default=Path("/opt/bevo/core"))
    parser.add_argument("--server-url")
    parser.add_argument("--repository", default="ghcr.io/longhornracingelectric/bevo-core")
    parser.add_argument("--release", type=Path, help="admin override directory containing a complete release")
    parser.add_argument("--upgrade", action="store_true", help="select the first compatible release even if the installed release suffices")
    parser.add_argument("--network-wait", type=float, default=60, help="startup connectivity wait in seconds")
    args = parser.parse_args()
    if not math.isfinite(args.network_wait) or args.network_wait < 0:
        parser.error("network wait must be finite and nonnegative")
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
            files = identity_release(files)
            plan = checked_plan(files, json.loads(files["pi_core_manifest.json"]), Manifest(files["schema_contract.json"]))
            temporary = override.with_suffix(".tmp")
            temporary.write_text(json.dumps({"source": str(args.release.resolve()), "schema_id": Manifest(files["schema_contract.json"]).schema_id,
                                             "release_id": content_id({"release_id": content_id(plan["metadata"]), "projection_id": plan["plan_id"]})}))
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
        try:
            if args.command == "startup":
                with startup_deadline(args.network_wait):
                    result = synchronize(updater, args)
            else:
                result = synchronize(updater, args)
            print(json.dumps(result))
        except Exception as error:
            if args.command != "startup":
                raise
            current = args.root / "current"
            if updater.installed() is None:
                raise ValueError("synchronization failed and no verified release is installed") from error
            files = {name: (current / name).read_bytes() for name in METADATA_FILES | {PLAN_FILE}}
            metadata, producer = release_schema(files)
            checked_plan(files, metadata, producer)
            print("Synchronization failed; continuing last verified activation: " + str(error), file=sys.stderr)
            print(json.dumps({"action": "offline-keep", "release": str(current.resolve()), "warning": str(error)}))


if __name__ == "__main__":
    main()
