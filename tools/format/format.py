"""Formats (or checks) all tracked C/C++ sources with the hermetic clang-format.

Usage:
  bazel run //tools/format          # rewrite files in place
  bazel run //tools/format:check    # fail if anything needs reformatting (CI)
"""

import argparse
import fnmatch
import os
import subprocess
import sys

from python.runfiles import runfiles

EXTENSIONS = ("*.c", "*.h", "*.cc", "*.cpp", "*.hpp")

# Generated code we don't own; CubeMX rewrites these on regen.
EXCLUDES = ("boards/*/*/Core/*",)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clang-format", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    clang_format = runfiles.Create().Rlocation(args.clang_format)

    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace:
        sys.exit("error: run via `bazel run //tools/format`, not directly")
    os.chdir(workspace)

    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *EXTENSIONS],
        check=True,
        capture_output=True,
    ).stdout
    files = [
        f.decode()
        for f in out.split(b"\0")
        if f and not any(fnmatch.fnmatch(f.decode(), pat) for pat in EXCLUDES)
    ]
    if not files:
        print("No C/C++ files to format.")
        return

    mode = ["--dry-run", "--Werror"] if args.check else ["-i"]
    failed = False
    # Chunk to stay under Windows' command-line length limit.
    for i in range(0, len(files), 100):
        result = subprocess.run([clang_format, *mode, *files[i : i + 100]])
        failed = failed or result.returncode != 0

    if failed:
        sys.exit(
            "\nSome files need formatting; run: bazel run //tools/format"
            if args.check
            else 1
        )
    print(
        f"All {len(files)} files correctly formatted."
        if args.check
        else f"Formatted {len(files)} files."
    )


if __name__ == "__main__":
    main()
