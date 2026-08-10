"""Build generated headers, then regenerate compile_commands.json.

Run via: bazel run //:refresh_ide

compile_commands.json points clangd at generated header trees under
bazel-out (libc++/__config_site from the hermetic LLVM toolchain, mingw
crt headers, firmware build_info, ...). The hedron extractor only
aqueries the action graph — it never builds — so on a fresh checkout
those directories don't exist and clangd fails with errors like
"Unknown type name 'uint32_t'". Worse, a plain `bazel build` isn't
enough: with the remote cache enabled, Bazel's default download mode
(--remote_download_toplevel, "build without the bytes") skips
materializing intermediate outputs on cache hits, leaving the header
directories as phantom metadata. --remote_download_all forces them onto
disk while keeping remote cache/exec speed.
"""

import os
import shutil
import subprocess
import sys


def main() -> int:
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace:
        print(
            "error: BUILD_WORKSPACE_DIRECTORY not set; "
            "run this with `bazel run //:refresh_ide`",
            file=sys.stderr,
        )
        return 1

    bazel = shutil.which("bazelisk") or shutil.which("bazel")
    if not bazel:
        print("error: bazel not found on PATH", file=sys.stderr)
        return 1

    steps = [
        [bazel, "build", "--remote_download_all", "//..."],
        [bazel, "run", "//:refresh_compile_commands"],
    ]
    for step in steps:
        print(f">>> {' '.join(step)}", flush=True)
        result = subprocess.run(step, cwd=workspace)
        if result.returncode != 0:
            return result.returncode

    print(">>> compile_commands.json refreshed; restart clangd to pick it up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
