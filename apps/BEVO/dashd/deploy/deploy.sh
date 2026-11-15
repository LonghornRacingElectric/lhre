#!/usr/bin/env bash
# Update + redeploy the dash on the Pi.
#
# What it does, in order:
#   1. git pull on the repo
#   2. npm run build in BEVO/dashd/frontend (refreshes the static bundle)
#   3. Restart bevo_dash_serve.service and bevo_telemetry.service
#
# Run this from the Pi after committing/pushing changes from your dev machine.
# Idempotent — safe to re-run.
#
# Build and transfer the core daemons with //apps/BEVO:pi_core_binaries before
# updating this frontend. This script only updates the kiosk assets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BEVO_ROOT="$(cd "$DASHD_DIR/.." && pwd)"
REPO_ROOT="$(cd "$BEVO_ROOT/../.." && pwd)"
FRONTEND_DIR="$DASHD_DIR/frontend"

echo "[1/3] git pull"
cd "$REPO_ROOT"
git pull --ff-only

echo "[2/3] npm run build (frontend)"
cd "$FRONTEND_DIR"
if [[ ! -d node_modules ]]; then
    echo "  node_modules missing — running 'npm install' first"
    npm install
fi
npm run build

echo "[3/3] systemctl restart"
sudo systemctl restart bevo_dash_serve.service || true
if systemctl is-enabled --quiet bevo_telemetry.service 2>/dev/null; then
    sudo systemctl restart bevo_telemetry.service
fi

echo
echo "Done. To pick up new frontend bits in the running kiosk:"
echo "  - Click the dash and press Ctrl+R, or"
echo "  - pkill chromium  &&  $SCRIPT_DIR/launch_kiosk.sh"
