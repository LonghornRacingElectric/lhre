#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
BIN_DIR="${BEVO_BIN_DIR:-$BEVO_ROOT/bin}"
CORE_BIN_DIR="$BIN_DIR"
CORE_RELEASE="${BEVO_CORE_RELEASE:-}"
if [[ -z "$CORE_RELEASE" && -n "${BEVO_CORE_ROOT:-}" ]]; then
  CORE_RELEASE="$BEVO_CORE_ROOT/current"
fi
if [[ -n "$CORE_RELEASE" ]]; then
  CORE_RELEASE="$(cd "$CORE_RELEASE" && pwd -P)"
  CORE_BIN_DIR="$CORE_RELEASE/bin"
fi
CAN_IFACE_0="${CAND_CAN_INTERFACE_0:-can0}"
CAN_IFACE_1="${CAND_CAN_INTERFACE_1:-can1}"
LOGGERD_ENABLED="${LOGGERD_ENABLED:-1}"

export LOGGERD_LOG_DIR="${LOGGERD_LOG_DIR:-$BEVO_ROOT/loggerd/logs}"

cleanup() {
  kill "${CAND_PID:-}" "${DASHD_PID:-}" "${PUBLISHD_PID:-}" "${LOGGERD_PID:-}" >/dev/null 2>&1 || true
  rm -f /tmp/BEVO_publishd_ready /tmp/BEVO_cand.sock /tmp/BEVO_cand_publishd.sock
}
trap cleanup EXIT INT TERM

for bin in "$CORE_BIN_DIR/cand" "$BIN_DIR/dashd" "$CORE_BIN_DIR/loggerd" "$CORE_BIN_DIR/publishd"; do
  if [[ ! -x "$bin" ]]; then
    echo "Missing binary: $bin" >&2
    echo "Install the Bazel-built executables in $BIN_DIR first." >&2
    exit 1
  fi
done

PUBLISHD_REQUIRE_SERVER_PACKET_ID="${PUBLISHD_REQUIRE_SERVER_PACKET_ID:-1}" "$CORE_BIN_DIR/publishd" &
PUBLISHD_PID=$!

"$BIN_DIR/dashd" &
DASHD_PID=$!

if [[ "$LOGGERD_ENABLED" == "1" ]]; then
  "$CORE_BIN_DIR/loggerd" &
  LOGGERD_PID=$!
fi

CAND_USE_MOCK=0 \
CAND_CAN_INTERFACE_0="$CAN_IFACE_0" \
CAND_CAN_INTERFACE_1="$CAN_IFACE_1" \
"$CORE_BIN_DIR/cand" &
CAND_PID=$!

wait "$DASHD_PID"
