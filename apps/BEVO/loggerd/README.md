# loggerd

Logs every cand IPC snapshot to CSV, independently of the uplink. Override
the output directory with `LOGGERD_LOG_DIR`.

Bazel generates and compiles the CSV column names and array lengths from
the shared textproto schema manifest, including GPS and external fields.
Columns retain their natural numeric ordering. New groups/fields do not
need a hand-written root-message template or hardcoded array sizes here.

There is no runtime `can.json` dependency or `LOGGERD_CAN_JSON_PATH` override.
The serializer and compiled column layout rebuild together from `lib/spec`.
The log stream, queue, file format and flush cadence remain unchanged.

Build with `bazel build //apps/BEVO:pi_core_binaries`; run the existing
column-layout checks with `bazel test //apps/BEVO/loggerd:loggerd_test`.
