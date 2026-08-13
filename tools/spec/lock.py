"""Regenerates lib/spec/wire.lock from the spec.

Run with `bazel run //tools/spec:lock` after any change that alters the
off-vehicle wire contract (new telemetry binding, changed scale, changed
quantity on an array block). //lib/spec:wire_lock_test fails until the
committed file matches, so the diff always lands in review.
"""

import os
import pathlib
import sys

from lib.spec.ir import loader, wire


def main():
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace:
        print("run via: bazel run //tools/spec:lock", file=sys.stderr)
        return 1
    root = pathlib.Path(workspace)
    spec_dir = root / "lib" / "spec"
    paths = sorted(spec_dir.rglob("*.textproto"))
    spec = loader.load([str(p) for p in paths])
    lock_path = spec_dir / "wire.lock"
    contents = wire.manifest(spec)
    if lock_path.exists() and lock_path.read_text(encoding="utf-8") == contents:
        print("wire.lock already up to date")
        return 0
    lock_path.write_text(contents, encoding="utf-8")
    print(f"wrote {lock_path.relative_to(root)} — review the diff before committing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
