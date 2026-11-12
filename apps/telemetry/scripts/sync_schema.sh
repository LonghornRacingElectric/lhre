#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-Orion}" != "Orion" ]]; then
  echo "The current-car schema is lib/spec; historical car schemas are not regenerated here." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
exec bazel build //lib/codegen/telemetry:server_schema
