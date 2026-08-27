# loggerd

CSV logger. Subscribes to cand's IPC snapshot stream and appends one row
per snapshot to a timestamped CSV in `logs/` (repo-relative under
`bazel run`; override with `LOGGERD_CAN_JSON_PATH` / see `main.rs`).

Column order is derived from the protobuf schema plus `can.json` (repeated
fields like `pack.cells_v[N]` are sized from the schema, sorted numerically)
— `:loggerd_test` locks that contract in, and runs on the host with no
hardware.
