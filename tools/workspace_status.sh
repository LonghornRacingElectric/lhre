#!/usr/bin/env bash
# Bazel workspace status script (see --workspace_status_command in .bazelrc).
# Emits STABLE_* keys into bazel-out/stable-status.txt, which
# //tools/firmware:build_info turns into a generated C++ header for build
# provenance stamping. Keep the emitted keys in sync with
# tools/workspace_status.bat (the Windows variant) and
# tools/firmware/gen_build_info.py (the consumer).
set -euo pipefail

if git rev-parse --git-dir >/dev/null 2>&1; then
  echo "STABLE_GIT_SHA $(git rev-parse HEAD)"
  echo "STABLE_GIT_DESCRIBE $(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)"
  # Dirty = uncommitted changes to tracked files. Untracked files don't count.
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "STABLE_GIT_DIRTY 1"
  else
    echo "STABLE_GIT_DIRTY 0"
  fi
else
  # Not a git checkout (e.g. a source tarball) — stamp placeholders rather
  # than failing the whole build.
  echo "STABLE_GIT_SHA unknown"
  echo "STABLE_GIT_DESCRIBE unknown"
  echo "STABLE_GIT_DIRTY 0"
fi
