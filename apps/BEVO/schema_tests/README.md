# Schema propagation regression

Build-only fixtures exercise a schema addition through the same telemetry
artifact rule and `sensor_proto_library` macro as the production builds.
The baseline has one existing measurement; a Bazel action appends a repeated
integer measurement and a bitfield flag, advancing the fixture's ID ledger.
Neither action edits `lib/spec` or its wire lock.

```bash
bazel test --config=local //apps/BEVO/schema_tests:schema_propagation_test
```

The test checks unchanged existing tags/types, additive compatibility, CAN
source/destination/bus details, SQL/ORM/Prisma fields and compiled logger
columns. A host Rust executable uses the generated typed tables and dispatch
to encode values, including zero/false; the server's generated descriptor
must decode them. The older descriptor must still decode the shared field.
No field/packet totals are pinned to the real car schema.

All fixture artifacts stay in Bazel outputs; packet files and updater tests
use temporary directories. No Git history, GitHub/GHCR credentials, Docker,
MQTT, Kafka, database service, running Pi or image pull is involved. There is
no nested Bazel invocation. Normal presubmit/postsubmit `bazel test //...`
includes this target on Linux/macOS; Windows skips the Rust-dependent target
like the production BEVO build. Building the Rust helper uses `rules_rust`,
not a Cargo command.

This is a dependency-wiring regression, not a database migration rehearsal
or proof of real GHCR authentication/Pi operation. The existing manual Paho
integration remains responsible for actual MQTT → Kafka → PostgreSQL delivery.
