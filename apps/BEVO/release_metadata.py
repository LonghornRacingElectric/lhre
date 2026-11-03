"""Describe immutable Pi artifacts without putting build tooling on the Pi."""

import argparse
import hashlib
import json
from pathlib import Path

from lib.telemetry.contract import Schema


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata(binaries, can_json, contract, descriptor):
    names = {path.name for path in binaries}
    if names != {"cand", "loggerd", "publishd"} or len(binaries) != len(names):
        raise ValueError("release requires exactly cand, loggerd and publishd")
    schema = Schema(contract.read_bytes(), descriptor.read_bytes())
    return {
        "format_version": 1, "platform": "linux/arm64",
        "projection_version": 1,
        "schema_id": schema.schema_id,
        "executables": {path.name: {"sha256": digest(path)} for path in sorted(binaries)},
        "can_json_sha256": digest(can_json), "schema_contract_sha256": digest(contract),
        "schema_descriptor_sha256": digest(descriptor),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--can-json", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("binaries", type=Path, nargs="+")
    args = parser.parse_args()
    args.out.write_text(json.dumps(metadata(args.binaries, args.can_json, args.contract, args.descriptor), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
