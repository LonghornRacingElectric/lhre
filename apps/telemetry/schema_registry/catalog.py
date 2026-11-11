"""Verified metadata cache; downloading a candidate never activates it."""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import fcntl
from contextlib import contextmanager

from apps.BEVO.updater.oci import Registry
from apps.BEVO.updater.updater import unpack, release_schema
from apps.telemetry.schema_registry.bundle import FILES, validate
from lib.telemetry.contract import Manifest, canonical_json, content_id, release_plan


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(canonical_json(value).encode())
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)


class Catalog:
    def __init__(self, configuration, registry=None):
        self.config = configuration
        self.root = Path(configuration["cache"])
        self.repository = configuration.get("repository", "ghcr.io/longhornracingelectric/telemetry-release")
        self.registry = registry or Registry(self.repository, os.getenv("BEVO_REGISTRY_USER", ""), os.getenv("BEVO_REGISTRY_TOKEN", ""))

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def index(self):
        path = self.root / "index.json"
        return json.loads(path.read_bytes()) if path.exists() else {"releases": []}

    def directory(self, reference):
        if not re.fullmatch(re.escape(self.repository) + r"@sha256:[a-f0-9]{64}", reference):
            raise ValueError("catalog reference must pin its configured repository")
        return self.root / reference.rsplit(":", 1)[1]

    def read(self, reference):
        directory = self.directory(reference)
        files = {name: (directory / name).read_bytes() for name in FILES}
        release, schema, storage = validate(files)
        if release["branch"] != self.config.get("branch", "vms/telemetry-pdr"):
            raise ValueError("release was not published for the configured deployment branch")
        if not re.fullmatch(r"[a-f0-9]{40}", release["revision"]):
            raise ValueError("catalog requires published commit provenance")
        return files, release, schema, storage

    def sync(self, reference=None):
        reference, manifest = self.registry.document(reference or self.repository + ":deployment")
        if len(manifest.get("layers", [])) != 1:
            raise ValueError("expected a single metadata bundle layer")
        files = unpack(self.registry.blob(manifest["layers"][0], 8 * 1024 * 1024), FILES, 8 * 1024 * 1024)
        release, _, _ = validate(files)
        if release["branch"] != self.config.get("branch", "vms/telemetry-pdr") or not re.fullmatch(r"[a-f0-9]{40}", release["revision"]):
            raise ValueError("candidate lacks deployment-branch commit provenance")
        with self.locked():
            self.root.mkdir(parents=True, exist_ok=True)
            destination = self.directory(reference)
            with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                stage = Path(temporary) / "release"
                stage.mkdir()
                for name, value in files.items():
                    (stage / name).write_bytes(value)
                if not destination.exists():
                    stage.rename(destination)
            self.read(reference)
            index = self.index()
            candidates = [reference] + [ref for ref in index["releases"] if ref != reference]
            protected = [index.get(key) for key in ("active", "previous", "prepared") if index.get(key)]
            kept = list(dict.fromkeys(protected + candidates))[:5]
            index["releases"] = [ref for ref in candidates if ref in kept]
            atomic_json(self.root / "index.json", index)
            for path in self.root.iterdir():
                if path.is_dir() and re.fullmatch(r"[a-f0-9]{64}", path.name) and all(self.directory(ref) != path for ref in kept):
                    shutil.rmtree(path)
            return reference

    def active(self, reference):
        index = self.index()
        if index.get("active") != reference:
            index["previous"] = index.get("active")
            index["active"] = reference
            atomic_json(self.root / "index.json", index)

    def augment(self, profile):
        with self.locked():
            return self._augment(profile)

    def _augment(self, profile):
        exact, compatible = [], []
        server = Manifest(profile["schema"])
        for reference in self.index()["releases"]:
            files, release, schema, _ = self.read(reference)
            if release["can_json_sha256"] != profile["can_json_sha256"]:
                continue
            metadata, _ = release_schema(files)
            try:
                release_plan(metadata, schema, server)
            except ValueError:
                continue
            if release["schema_id"] == server.schema_id and release["storage_id"] == profile["storage_id"]:
                exact.append((reference, release["bevo_release"], content_id(metadata)))
            else:
                compatible.append(release["bevo_release"])
        if exact:
            self.active(exact[0][0])
        return dict(profile, releases=list(dict.fromkeys([ref for _, ref, _ in exact] + compatible)),
                    selection_policy="prefer_deployed", preferred_release_id=exact[0][2] if exact else None)
