#!/usr/bin/env bash
# Deploy the BEVO bundle to the Pi over SSH — no repo checkout, no toolchain,
# no cargo on the Pi; the bundle carries static binaries.
#
#   bazel run //apps/BEVO:deploy -- [user@host] [dest-dir]
#
# Defaults: lhre@bevo.local, /opt/bevo. Restarts bevo_telemetry.service if
# it's installed; first-time service setup is in apps/BEVO/README.md.
set -euo pipefail

HOST="${1:-lhre@bevo.local}"
DEST="${2:-/opt/bevo}"

RUNFILES_ROOT="${RUNFILES_DIR:-$0.runfiles}"
BUNDLE="$RUNFILES_ROOT/_main/apps/BEVO/pi_bundle.tar.gz"

if [[ ! -f "$BUNDLE" ]]; then
  echo "Bundle not found at $BUNDLE (run via 'bazel run //apps/BEVO:deploy')" >&2
  exit 1
fi

echo "Deploying $(du -h "$BUNDLE" | cut -f1 | tr -d ' ') bundle to $HOST:$DEST"
# shellcheck disable=SC2029  # DEST expands client-side by design
ssh "$HOST" "mkdir -p '$DEST'"
ssh "$HOST" "tar xzf - -C '$DEST'" < "$BUNDLE"

if ssh "$HOST" "systemctl is-enabled bevo_telemetry.service" >/dev/null 2>&1; then
  echo "Restarting bevo_telemetry.service"
  ssh "$HOST" "sudo systemctl restart bevo_telemetry.service"
else
  echo "bevo_telemetry.service not installed; to run manually:"
  echo "  ssh $HOST $DEST/nonhermetic/run_real_stack.sh"
  echo "Service setup: apps/BEVO/README.md → 'Deploying to the Pi'."
fi
echo "Done."
