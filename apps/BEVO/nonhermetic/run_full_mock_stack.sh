#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
BIN_DIR="${BEVO_BIN_DIR:-$BEVO_ROOT/bin}"


# environment variables mirrored from run_real_stack.sh but for mock use
CAN_IFACE="${CAND_CAN_INTERFACE:-can0}"

cleanup() {
  kill "${MOCK_PID:-}" "${MOCK_MAIN_PID:-}" "${DEBUGD_PID:-}" "${DASHD_PID:-}" "${LOGGERD_PID:-}" "${PUBLISHD_PID:-}" >/dev/null 2>&1 || true
  rm -f /tmp/BEVO_cand.sock /tmp/BEVO_cand_publishd.sock /tmp/BEVO_cand_loggerd.sock
}
trap cleanup EXIT INT TERM

for bin in "$BIN_DIR/mock_main" "$BIN_DIR/dashd" "$BIN_DIR/loggerd" "$BIN_DIR/publishd" "$BIN_DIR/debugd"; do
  if [[ ! -x "$bin" ]]; then
    echo "Missing binary: $bin" >&2
    echo "Install the mock-stack executables in $BIN_DIR first." >&2
    exit 1
  fi
done

# start the mock generator like run_mock_stack
python3 "$BEVO_ROOT/cand/mock_can.py" &
MOCK_PID=$!

"$BIN_DIR/publishd" &
PUBLISHD_PID=$!

# launch the remaining daemons
"$BIN_DIR/debugd" &
DEBUGD_PID=$!

"$BIN_DIR/dashd" &
DASHD_PID=$!

"$BIN_DIR/loggerd" &
LOGGERD_PID=$!

# mock_main publishes directly to the cand IPC sockets
"$BIN_DIR/mock_main" &
MOCK_MAIN_PID=$!

# wait on dashd as the primary process
wait "$DASHD_PID"
