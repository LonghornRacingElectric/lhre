#!/usr/bin/env bash

set -euo pipefail

runfiles_root="${RUNFILES_DIR:-}"
if [[ ! -f "$runfiles_root/bazel_tools/tools/bash/runfiles/runfiles.bash" ]]; then
    if [[ -f "../bazel_tools/tools/bash/runfiles/runfiles.bash" ]]; then
        runfiles_root="$(cd .. && pwd)"
    elif [[ -f "bazel_tools/tools/bash/runfiles/runfiles.bash" ]]; then
        runfiles_root="$PWD"
    fi
fi

for loader in "$@"; do
    RUNFILES_DIR="$runfiles_root" "$loader"
done
