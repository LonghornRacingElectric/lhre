# BEVO 🗣️🗣️🗣️

Board Emitting Vehicle Outputs — the on-car telemetry computer (a Raspberry
Pi wired to the vehicle CAN buses). Decodes CAN traffic, shows it to the
driver, logs it, and uplinks it to the pit over MQTT.

Migrated from the `lhre-2026` repo; history before this move lives there.

## Daemons

Five Rust binaries that talk over Unix domain sockets (`/tmp/BEVO_*.sock`),
all fed by `cand`:

| Target | Role |
| ------ | ---- |
| [cand](cand/README.md) | Reads both CAN buses (SocketCAN), decodes frames per `can.json`, fans out protobuf snapshots over IPC. Linux-only for real CAN; elsewhere it compiles to a stub — use `mock_main`. |
| [dashd](dashd/README.md) | Driver display backend: MQTT + WebSocket server for the React kiosk frontend in `dashd/frontend/`. |
| [loggerd](loggerd/README.md) | Writes CSV logs (schema-derived columns) to `loggerd/logs/`. |
| [publishd](publishd/README.md) | Uplinks snapshots to the pit MQTT broker. |
| [debugd](debugd/README.md) | Dumps decoded snapshots to stdout; smoke-test tool. |

## Build and run

```bash
bazel build //apps/BEVO/...                          # everything
bazel test //apps/BEVO/...                           # loggerd schema tests
bazel run --config=local //apps/BEVO/cand:run_mock_can_stack   # dev stack, no hardware
bazel run --config=local //apps/BEVO/cand:run_can_stack        # real CAN (Linux/Pi)
```

Host binaries run via `bazel run --config=local` (the default config
cross-compiles for the remote Linux executors — fine for `build`/`test`,
wrong for `run`; see [build-system.md](https://github.com/LonghornRacingElectric/lhre/blob/main/build-system.md)).

`Cargo.toml`/`Cargo.lock` are still real: they drive Bazel's crate resolution
(crate_universe) *and* keep `cargo`/rust-analyzer working for quick local
iteration. After changing dependencies in `Cargo.toml`, repin with
`CARGO_BAZEL_REPIN=1 bazel build //apps/BEVO/...` and commit both lockfiles
(`Cargo.lock` and `MODULE.bazel.lock`).

On Windows every Rust target here is skipped (rules_rust expects the MSVC
triple, our hermetic clang is MinGW); `bazel build //...` still passes,
it just builds nothing from BEVO. Use Linux, macOS, or the Linux CI job.

CI explicitly builds `//apps/BEVO:pi_core_binaries`, which cross-compiles
`cand`, `loggerd`, and `publishd` for 64-bit Raspberry Pi OS. The accompanying
`//apps/BEVO:pi_core_binary_test` verifies that all three outputs are static
AArch64 ELF executables. This target intentionally produces executables only;
it does not build OCI images for the dashboard or server logsync, nor package
the dashboard frontend. The CI path invokes Bazel only; `rules_rust` compiles
the crates and their pinned dependencies without running `cargo build`.

BEVO's prost bindings and CAN decode JSON come from the same
`//lib/codegen/telemetry:can_packets_proto` and `:can_json` targets selected by
the telemetry server. Both sides therefore rebuild from `lib/spec` together;
there is no separately generated BEVO wire schema in the Bazel path.

## Deploying to the Pi

The repo never goes on the Pi — deploys ship binaries:

```bash
bazel run //apps/BEVO:deploy -- lhre@bevo.local /opt/bevo
```

That cross-compiles every daemon as a **static aarch64-musl ELF**
(`//platforms:pi` — no glibc version coupling with the Pi's OS image),
bundles them with `can.json` and the runbook scripts into
`pi_bundle.tar.gz` (~8 MB), untars it over SSH, and restarts
`bevo_telemetry.service` if it's installed. The bundled
`nonhermetic/run_real_stack.sh` finds the binaries in `bin/` automatically
(`BEVO_BIN_DIR` overrides).

One-time service install on the Pi:

```bash
sudo sh -c "sed 's|__BEVO_REPO__/apps/BEVO|/opt/bevo|g; s|__BEVO_REPO__|/opt/bevo|g' /opt/bevo/nonhermetic/bevo_telemetry.service > /etc/systemd/system/bevo_telemetry.service && systemctl daemon-reload && systemctl enable bevo_telemetry"
```

The kiosk frontend is a separate flow (`dashd/deploy/`, needs npm). If you
truly must build *on* the Pi, a sparse checkout keeps it small — BEVO's
Cargo build is self-contained:

```bash
git clone --filter=blob:none --sparse https://github.com/LonghornRacingElectric/lhre.git && cd lhre && git sparse-checkout set apps/BEVO && cd apps/BEVO && cargo build --release
```

## The CAN schema

[`lib/spec`](../../lib/spec/README.md) is the Bazel source of truth. It
generates `can.json`, the deployed snapshot proto, prost bindings, dispatch
code, and server database DDL; **no generated Bazel artifact is checked in**.
The files under [schema/](schema/README.md) remain a plain-Cargo fallback.

## Not built by Bazel (on purpose)

- `dashd/frontend/` — React kiosk UI, plain `npm` (see its README).
- `cell.py` — cellular-modem control, run with a venv + `requirements.txt`
  (Pi-only deps like `lgpio`).
- `nonhermetic/`, `dashd/deploy/` — Pi deployment: systemd units, kiosk
  setup, Cargo-based runbooks.
