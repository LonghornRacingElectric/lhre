# Schema source

BEVO has no private CAN schema. The authoritative textproto files live under
[`lib/spec`](../../../lib/spec/README.md), and Bazel generates the shared
protobuf and runtime CAN JSON under
[`lib/codegen/telemetry`](../../../lib/codegen/telemetry/README.md).

The former `can_packets.csv`, `can_bitfields.csv`, checked-in protobuf, and
Python generators were removed. They duplicated the deployed contract and
could drift from the telemetry server.

For a vehicle schema change, edit `lib/spec`, run `bazel run //tools/spec:lock`,
and let Bazel regenerate BEVO and server artifacts together. The core build is
`bazel build //apps/BEVO:pi_core_binaries`; Bazel supplies the generated proto
and CAN JSON directly to the Rust generation action.
