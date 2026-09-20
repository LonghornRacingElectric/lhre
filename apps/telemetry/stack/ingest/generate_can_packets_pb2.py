#!/usr/bin/env python3
"""Generates can_packets_pb2.py using protoc in a cross-platform way."""

import os
import shutil
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "Usage: generate_can_packets_pb2.py <protoc_path> <proto_src> <rule_dir> <out_file>",
            file=sys.stderr,
        )
        return 1

    protoc_path = sys.argv[1]
    proto_src = sys.argv[2]
    rule_dir = sys.argv[3]
    out_file = sys.argv[4]

    out_dir = os.path.dirname(out_file)
    os.makedirs(out_dir, exist_ok=True)

    dest_proto = os.path.join(rule_dir, "can_packets.proto")
    shutil.copyfile(proto_src, dest_proto)

    cmd = [
        protoc_path,
        f"--proto_path={rule_dir}",
        f"--python_out={out_dir}",
        dest_proto,
    ]
    res = subprocess.run(cmd)
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
