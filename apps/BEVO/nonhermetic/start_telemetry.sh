#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
REPO_ROOT="$(cd "$BEVO_ROOT/../.." && pwd)"

PYTHON_BIN="${BEVO_PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/venv/bin/python"; do
    if [[ -x "$candidate" ]]; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
CELL_SCRIPT="${BEVO_CELL_SCRIPT:-$BEVO_ROOT/cell.py}"
LOCUS_POWER_SCRIPT="${BEVO_LOCUS_POWER_SCRIPT:-$BEVO_ROOT/locus_power.sh}"
CAN_IFACE_0="${CAND_CAN_INTERFACE_0:-can0}"
CAN_IFACE_1="${CAND_CAN_INTERFACE_1:-can1}"

CAN_BITRATE="${BEVO_CAN_BITRATE:-1000000}"
OPENVPN_CONFIG="${BEVO_OPENVPN_CONFIG:-/etc/openvpn/client/client.ovpn}"
OPENVPN_CREDS="${BEVO_OPENVPN_CREDS:-$BEVO_ROOT/vpn_creds.txt}"

echo "Starting BEVO telemetry system..."
echo "Repo root: $REPO_ROOT"
echo "BEVO root: $BEVO_ROOT"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing python executable at $PYTHON_BIN" >&2
  echo "Set BEVO_PYTHON_BIN or create the BEVO virtual environment first." >&2
  exit 1
fi

if [[ ! -f "$CELL_SCRIPT" ]]; then
  echo "Missing cellular control script: $CELL_SCRIPT" >&2
  exit 1
fi

if [[ -x "$LOCUS_POWER_SCRIPT" ]]; then
  echo "Enabling Locus Lock power rail (GPIO14)..."
  if ! "$LOCUS_POWER_SCRIPT" on; then
    echo "WARNING: locus_power.sh failed; Locus Lock GPS will not power on." >&2
  fi
fi

echo "Turning on cellular module..."
if ! "$PYTHON_BIN" "$CELL_SCRIPT" on; then
  echo "WARNING: cell.py failed; continuing without cellular (dash + CAN still work)." >&2
fi

for CAN_IFACE in "$CAN_IFACE_0" "$CAN_IFACE_1"; do
  echo "Configuring CAN interface $CAN_IFACE @ $CAN_BITRATE bps..."
  if ip link show "$CAN_IFACE" >/dev/null 2>&1; then
    ip link set "$CAN_IFACE" down >/dev/null 2>&1 || true
  fi
  ip link set "$CAN_IFACE" up type can bitrate "$CAN_BITRATE"
done

# echo "Starting VPN connection..."
# openvpn --config "$OPENVPN_CONFIG" --auth-user-pass "$OPENVPN_CREDS" --daemon
# sleep 5

UPDATER="${BEVO_UPDATER:-$BEVO_ROOT/bevo_updater.pyz}"
if [[ -z "${BEVO_CORE_RELEASE:-}" ]]; then
  export BEVO_CORE_ROOT="${BEVO_CORE_ROOT:-/opt/bevo/core}"
  "$PYTHON_BIN" "$UPDATER" startup --root "$BEVO_CORE_ROOT" \
    --server-url "${BEVO_SERVER_URL:-}" --network-wait "${BEVO_NETWORK_WAIT_S:-60}"
fi

echo "Starting BEVO real CAN stack..."
LOGGERD_ENABLED=1 "$SCRIPT_ROOT/run_real_stack.sh" &
STACK_PID=$!
START_TIME=$(date +%s)
LAST_SCHEMA_CHECK=$START_TIME
SCHEMA_CHECK_PID=""
trap 'kill "$STACK_PID" ${SCHEMA_CHECK_PID:+"$SCHEMA_CHECK_PID"} 2>/dev/null || true' EXIT INT TERM

# Monitor disk space and stop if low
while kill -0 $STACK_PID 2>/dev/null; do
  CURRENT_TIME=$(date +%s)
  ELAPSED=$((CURRENT_TIME - START_TIME))
  if [[ -z "${BEVO_CORE_RELEASE:-}" && -n "${BEVO_SERVER_URL:-}" ]] && (( CURRENT_TIME - LAST_SCHEMA_CHECK >= 300 )); then
    if [[ -z "$SCHEMA_CHECK_PID" ]] || ! kill -0 "$SCHEMA_CHECK_PID" 2>/dev/null; then
      "$PYTHON_BIN" "$UPDATER" check --root "$BEVO_CORE_ROOT" --server-url "$BEVO_SERVER_URL" &
      SCHEMA_CHECK_PID=$!
      LAST_SCHEMA_CHECK=$CURRENT_TIME
    fi
  fi
  if (( ELAPSED > 3600 )); then
    echo "Runtime exceeded 1 hour. Stopping telemetry."
    kill $STACK_PID
    sudo systemctl stop bevo_telemetry.service
    exit 0
  fi
  FREE_SPACE_MB=$(df / | awk 'NR==2 {print $4 / 1024}')
  if (( $(echo "$FREE_SPACE_MB < 1024" | bc -l) )); then
    echo "Disk space low (${FREE_SPACE_MB} MB free). Stopping telemetry."
    kill $STACK_PID
    sudo systemctl stop bevo_telemetry.service
    exit 1
  fi
  sleep 60
done
