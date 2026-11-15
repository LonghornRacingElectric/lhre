# loggerd

CSV logger. Subscribes to cand's IPC snapshot stream and appends one row
per snapshot to a timestamped CSV in `logs/` (repo-relative under
`bazel run`; override with `LOGGERD_CAN_JSON_PATH` / see `main.rs`).

Column order is derived from the protobuf schema plus `can.json` (repeated
fields like `pack.cells_v[N]` are sized from the schema, sorted numerically)
— `:loggerd_test` locks that contract in, and runs on the host with no
hardware.

Bazel supplies the shared textproto-generated protobuf and CAN JSON for the
executable and its tests. `//apps/BEVO:pi_core_binaries` cross-compiles the Pi
logger alongside `cand` and `publishd`.

The generated CAN JSON is required at startup. Missing or invalid JSON stops
`loggerd` with an error before it creates a log; there are no built-in schema
sizes to substitute for a missing artifact. Set `LOGGERD_CAN_JSON_PATH` to the
generated file's deployed location.
