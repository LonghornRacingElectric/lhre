"""Rewrites every spec .textproto in canonical form, in place.

Run with `bazel run //tools/spec:fmt`. The same canonicalization is
enforced by //lib/spec:format_check, so hand edits and tool edits converge
to identical bytes.
"""

import os
import pathlib
import sys

from lib.spec.ir import canonical, loader


def main():
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace:
        print("run via: bazel run //tools/spec:fmt", file=sys.stderr)
        return 1
    spec_dir = pathlib.Path(workspace) / "lib" / "spec"
    changed = 0
    for path in sorted(spec_dir.rglob("*.textproto")):
        original = path.read_text(encoding="utf-8")
        rel = path.relative_to(workspace)
        formatted = canonical.canonicalize(loader.parse_file(original, filename=str(rel)))
        if formatted != original:
            path.write_text(formatted, encoding="utf-8")
            print(f"formatted {rel}")
            changed += 1
    print(f"{changed} file(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
