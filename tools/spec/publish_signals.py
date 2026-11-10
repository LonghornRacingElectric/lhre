"""Publish immutable catalog versions and atomically select the current version."""

import argparse
import json
import os
from pathlib import Path
import tempfile

from runfiles import Runfiles

from lib.codegen.telemetry import catalog


def _bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _temporary(directory, data):
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".catalog-", delete=False) as stream:
        filename = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            filename.unlink(missing_ok=True)
            raise
    return filename


def publish(source, root):
    value = catalog.validate(json.loads(Path(source).read_text(encoding="utf-8")))
    directory = Path(root) / value["car"]
    directory.mkdir(parents=True, exist_ok=True)
    version = value["schema_version"]
    destination = directory / f"{version}.json"
    created = False
    if not destination.exists():
        temporary = _temporary(directory, _bytes(value))
        try:
            # Publish without replacing an existing version, including concurrent writers.
            try:
                os.link(temporary, destination)
                created = True
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    existing = catalog.validate(json.loads(destination.read_text(encoding="utf-8")))
    if existing != value:
        raise ValueError("existing immutable catalog differs; refusing to overwrite")
    pointer = directory / "current.json"
    selection = {"schema_version": version}
    try:
        unchanged = json.loads(pointer.read_text(encoding="utf-8")) == selection
    except (FileNotFoundError, json.JSONDecodeError):
        unchanged = False
    if not unchanged:
        temporary = _temporary(directory, _bytes(selection))
        try:
            os.replace(temporary, pointer)
        finally:
            temporary.unlink(missing_ok=True)
    return {"car": value["car"], "schema_version": version, "created": created, "activated": not unchanged}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="catalog to publish or reactivate; defaults to the current Bazel output")
    parser.add_argument("--source-runfile", help=argparse.SUPPRESS)
    parser.add_argument("--catalog-root", type=Path)
    args = parser.parse_args()
    source = args.source
    if source is None:
        source = Runfiles.Create().Rlocation(args.source_runfile) if args.source_runfile else None
    if not source:
        parser.error("provide --source or run //tools/spec:publish_signals")
    root = args.catalog_root or os.environ.get("SIGNAL_CATALOG_DIR")
    if root is None:
        workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        if not workspace:
            parser.error("provide --catalog-root outside bazel run")
        root = Path(workspace) / "apps/telemetry/catalogs"
    print(json.dumps(publish(source, root)))


if __name__ == "__main__":
    main()
