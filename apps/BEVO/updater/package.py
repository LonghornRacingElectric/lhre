"""Bazel-only packaging of prebuilt releases and the standalone updater."""

import argparse
import gzip
import io
from pathlib import Path
import tarfile
import zipfile


def archive(path, files, executable=False):
    with path.open("wb") as output, gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            for name, source in sorted(files.items()):
                data = source.read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o755 if executable else 0o644
                tar.addfile(info, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    layers = commands.add_parser("layers")
    layers.add_argument("--manifest", type=Path, required=True)
    layers.add_argument("--contract", type=Path, required=True)
    layers.add_argument("--descriptor", type=Path, required=True)
    layers.add_argument("--can-json", type=Path, required=True)
    layers.add_argument("--metadata-out", type=Path, required=True)
    layers.add_argument("--binaries-out", type=Path, required=True)
    layers.add_argument("binaries", type=Path, nargs="+")
    updater = commands.add_parser("updater")
    updater.add_argument("--out", type=Path, required=True)
    updater.add_argument("--source", type=Path, required=True)
    updater.add_argument("--oci", type=Path, required=True)
    updater.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "layers":
        archive(args.metadata_out, {"pi_core_manifest.json": args.manifest, "schema_contract.json": args.contract,
                                  "schema_descriptor.pb": args.descriptor, "can.json": args.can_json})
        archive(args.binaries_out, {"bin/" + path.name: path for path in args.binaries}, executable=True)
    else:
        with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
            sources = {"apps/BEVO/updater/updater.py": args.source, "apps/BEVO/updater/oci.py": args.oci,
                       "lib/telemetry/contract.py": args.contract}
            contents = {name: path.read_bytes() for name, path in sources.items()}
            contents["__main__.py"] = b"from apps.BEVO.updater.updater import main\nmain()\n"
            for package in ("apps", "apps/BEVO", "apps/BEVO/updater", "lib", "lib/telemetry"):
                contents[package + "/__init__.py"] = b""
            for name, data in sorted(contents.items()):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive_file.writestr(info, data)


if __name__ == "__main__":
    main()
