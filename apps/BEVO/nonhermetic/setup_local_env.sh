#!/usr/bin/env bash
# One-time setup for the non-Bazel (Cargo) flow: generate the runtime CAN
# decode map, then build release binaries with Cargo. Needs python3 and a
# Rust toolchain; build.rs additionally needs `protoc` (PROTOC env or PATH).
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BEVO_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"

"$SCRIPT_ROOT/sync_assets.sh"

echo "Building BEVO local binaries with Cargo"
cargo build --release --manifest-path "$BEVO_ROOT/Cargo.toml"

echo "Done. Local nonhermetic BEVO build is ready."
