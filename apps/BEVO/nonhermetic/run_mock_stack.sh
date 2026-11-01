#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
BIN_DIR="${BEVO_BIN_DIR:-$BEVO_ROOT/bin}"

cleanup() {
  kill "${MOCK_PID:-}" "${MOCK_MAIN_PID:-}" "${DEBUGD_PID:-}" >/dev/null 2>&1 || true
  rm -f /tmp/BEVO_publishd_ready /tmp/BEVO_cand.sock /tmp/BEVO_cand_publishd.sock
}
trap cleanup EXIT INT TERM

for bin in "$BIN_DIR/mock_main" "$BIN_DIR/debugd"; do
  if [[ ! -x "$bin" ]]; then
    echo "Missing binary: $bin" >&2
    echo "Install the mock-stack executables in $BIN_DIR first." >&2
    exit 1
  fi
done

python3 "$BEVO_ROOT/cand/mock_can.py" &
MOCK_PID=$!

"$BIN_DIR/debugd" &
DEBUGD_PID=$!

CAND_MOCK_PUBLISH_TARGETS=cand "$BIN_DIR/mock_main" &
MOCK_MAIN_PID=$!

wait "$DEBUGD_PID"
