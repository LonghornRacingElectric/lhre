#!/usr/bin/env bash
# Regenerate the runtime CAN decode map for the non-Bazel (Cargo) flow.
#
# Bazel builds never need this — //apps/BEVO/schema:can_json is generated on
# every build. This exists for Cargo checkouts and deployed trees, whose
# runbooks read nonhermetic/assets/can.json at runtime. The output is
# gitignored: it is a build product, never a source.
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
SCHEMA_DIR="$BEVO_ROOT/schema"
ASSETS_DIR="$SCRIPT_ROOT/assets"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found" >&2
  exit 1
fi

mkdir -p "$ASSETS_DIR"
python3 "$SCHEMA_DIR/generate_can_json.py" \
  "$SCHEMA_DIR/can_packets.csv" \
  "$SCHEMA_DIR/can_bitfields.csv" \
  "$ASSETS_DIR/can.json"
echo "Wrote $ASSETS_DIR/can.json"
