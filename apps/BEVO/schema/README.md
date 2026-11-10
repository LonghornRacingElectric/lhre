# Schema source

BEVO has no private CAN schema. The authoritative textproto files live under
[`lib/spec`](../../../lib/spec/README.md), and Bazel generates the shared
protobuf and build-time CAN JSON under
[`lib/codegen/telemetry`](../../../lib/codegen/telemetry/README.md).

The former `can_packets.csv`, `can_bitfields.csv`, checked-in protobuf, and
Python generators were removed. They duplicated the deployed contract and
could drift from the telemetry server.

For a vehicle schema change, edit `lib/spec`, run `bazel run //tools/spec:lock`,
and let Bazel regenerate BEVO and server artifacts together. The core build is
`bazel build //apps/BEVO:pi_core_binaries`; Bazel supplies the generated proto
and CAN JSON directly to the Rust generation action. That action emits typed
CAN configuration constructors and CSV column/array metadata compiled into
the executables. Daemons do not read a runtime JSON mapping or accept a
mutable schema-file override.
