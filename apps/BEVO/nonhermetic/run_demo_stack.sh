#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
PYTHON_BIN="${BEVO_PYTHON_BIN:-python3}"
DURATION="${1:-300}"
if [[ $# -gt 1 || ! "$DURATION" =~ ^[0-9]+$ ]] || (( DURATION <= 0 )); then
  echo "Usage: bash $0 [positive duration in seconds, default 300]" >&2
  exit 1
fi
if [[ "$(uname -s)" != Linux || "$(uname -m)" != aarch64 ]]; then
  echo "Run this demo inside Linux ARM64, with that environment joined to the Tailnet." >&2
  exit 1
fi
: "${BEVO_SERVER_URL:?Set the HTTPS schema authority profile.json URL}"
: "${PUBLISHD_MQTT_HOST:?Set the server Tailnet address}"
export BEVO_CORE_ROOT="${BEVO_CORE_ROOT:-$HOME/.local/share/bevo-demo/core}"
export LOGGERD_LOG_DIR="${LOGGERD_LOG_DIR:-$HOME/.local/share/bevo-demo/logs}"
export CAND_CAN_INTERFACE_0="${CAND_CAN_INTERFACE_0:-vcan0}"
export CAND_CAN_INTERFACE_1="${CAND_CAN_INTERFACE_1:-vcan1}"
export CAND_NMEA_LISTEN_ADDR="${CAND_NMEA_LISTEN_ADDR:-127.0.0.1:2000}"
UPDATER="${BEVO_UPDATER:-$BEVO_ROOT/bevo_updater.pyz}"
if [[ -f "$BEVO_CORE_ROOT/override.json" ]]; then
  echo "Demo installation has an admin override; clear it or choose a separate BEVO_CORE_ROOT." >&2
  exit 1
fi
for iface in "$CAND_CAN_INTERFACE_0" "$CAND_CAN_INTERFACE_1"; do
  ip -details link show "$iface" | grep -qw vcan || {
    echo "Create and bring up virtual CAN interface $iface first (see the demo runbook)." >&2
    exit 1
  }
done
for path in /tmp/BEVO_cand.sock /tmp/BEVO_cand_publishd.sock /tmp/BEVO_cand_loggerd.sock; do
  if [[ -e "$path" ]]; then
    echo "Existing BEVO socket $path; stop the other stack before running the demo." >&2
    exit 1
  fi
done

"$PYTHON_BIN" "$UPDATER" startup --root "$BEVO_CORE_ROOT" \
  --server-url "$BEVO_SERVER_URL" --network-wait "${BEVO_NETWORK_WAIT_S:-60}"
export BEVO_CORE_RELEASE="$(cd "$BEVO_CORE_ROOT/current" && pwd -P)"
echo "Running activation: $BEVO_CORE_RELEASE"
echo "Local CSV directory: $LOGGERD_LOG_DIR"

cleanup() {
  trap - EXIT INT TERM
  kill ${SENDER_PID:-} ${STACK_PID:-} 2>/dev/null || true
  wait ${SENDER_PID:-} ${STACK_PID:-} 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

DASHD_ENABLED=0 LOGGERD_ENABLED=1 "$SCRIPT_ROOT/run_real_stack.sh" &
STACK_PID=$!
for (( attempt=0; attempt<100; attempt++ )); do
  kill -0 "$STACK_PID" 2>/dev/null || { wait "$STACK_PID"; exit 1; }
  [[ ! -S /tmp/BEVO_cand_publishd.sock || ! -S /tmp/BEVO_cand_loggerd.sock ]] || break
  sleep .1
done
if [[ ! -S /tmp/BEVO_cand_publishd.sock || ! -S /tmp/BEVO_cand_loggerd.sock ]]; then
  echo "cand did not open its update sockets within 10 seconds." >&2
  exit 1
fi
"$PYTHON_BIN" "$BEVO_ROOT/cand/mock_can.py" --can-json "$BEVO_CORE_RELEASE/can.json" \
  --transport socketcan --critical-interface "$CAND_CAN_INTERFACE_0" \
  --data-interface "$CAND_CAN_INTERFACE_1" --duration "$DURATION" &
SENDER_PID=$!
while kill -0 "$SENDER_PID" 2>/dev/null; do
  kill -0 "$STACK_PID" 2>/dev/null || { wait "$STACK_PID"; exit 1; }
  sleep 1
done
wait "$SENDER_PID"
