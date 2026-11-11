"""Package and validate generated Artemis deployment metadata."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import re

from apps.BEVO.updater.package import archive
from apps.BEVO.updater.updater import release_schema, METADATA_FILES
from lib.telemetry.contract import Schema, canonical_json


ASSETS = METADATA_FILES | {"storage_contract.json", "storage.sql", "wire.lock"}
FILES = ASSETS | {"release.json"}


def validate(files):
    if set(files) != FILES:
        raise ValueError("incomplete server release bundle")
    release = json.loads(files["release.json"])
    if release.get("format_version") != 1 or set(release["files"]) != ASSETS:
        raise ValueError("unsupported release bundle")
    if not re.fullmatch(r"ghcr.io/longhornracingelectric/bevo-core@sha256:[a-f0-9]{64}", release["bevo_release"]):
        raise ValueError("bundle must pin the approved BEVO repository")
    for name, digest in release["files"].items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError("release bundle checksum differs: " + name)
    core, _ = release_schema(files)
    schema = Schema(files["schema_contract.json"], files["schema_descriptor.pb"])
    if schema.manifest["root"] != "artemis.ArtemisSensorData" or core.get("wire_format") != "artemis_updates_v1":
        raise ValueError("release is not native Artemis")
    storage = json.loads(files["storage_contract.json"])
    identity = dict(storage)
    storage_id = identity.pop("storage_id")
    if storage.get("car") != "Artemis" or storage_id != hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
        raise ValueError("invalid storage identity")
    if release["schema_id"] != schema.schema_id or release["storage_id"] != storage_id or release["can_json_sha256"] != core["can_json_sha256"]:
        raise ValueError("bundle identities disagree")
    return release, schema, storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--asset", action="append", required=True)
    args = parser.parse_args()
    paths = {name: Path(path) for name, path in (asset.split("=", 1) for asset in args.asset)}
    files = {name: path.read_bytes() for name, path in paths.items()}
    core, schema = release_schema(files)
    digest = json.loads((args.image / "index.json").read_bytes())["manifests"][0]["digest"]
    release = {
        "format_version": 1, "revision": args.revision, "branch": args.branch,
        "bevo_release": "ghcr.io/longhornracingelectric/bevo-core@" + digest,
        "schema_id": schema.schema_id, "storage_id": json.loads(files["storage_contract.json"])["storage_id"],
        "can_json_sha256": core["can_json_sha256"],
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    files["release.json"] = canonical_json(release).encode()
    validate(files)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "release.json"
        path.write_bytes(files["release.json"])
        archive(args.out, dict(paths, **{"release.json": path}))


if __name__ == "__main__":
    main()
