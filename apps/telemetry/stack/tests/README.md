# Telemetry stack tests

The normal Bazel suite runs protobuf tests without Docker. Connectivity and
data-flow tests are `manual`, local targets: they require loaded telemetry
images, Docker, the server credentials and a running core stack. See the
[stack guide](../README.md) for startup.

```bash
bazel test --config=local //apps/telemetry/stack/tests:integration_tests
```

The lifecycle runner is separate:

```bash
bazel test --config=local //apps/telemetry/stack/tests:stack_integration_test
```

**Use an isolated test stack.** This runner starts services and its exit trap
calls `down --volumes`; it is not a production health check.

Bazel supplies `current_schema_sql` and `new_car_schema_sql` as a data dependency. The runner uses its
absolute runfiles path without invoking nested Bazel builds. When run directly
from a checkout, it builds the SQL target before starting any services. An
explicit existing absolute `ORION_DB_INIT_SQL` or `ARTEMIS_DB_INIT_SQL` selects an admin-provided schema.
Cleanup only resolves the path for Compose interpolation: missing build outputs
or a failed schema build cannot block teardown, and Compose failures are reported.

The Docker-free `//apps/telemetry/stack:server_devtool_test` exercises schema
refresh, sudo forwarding, startup failures and the runner's cleanup path with
fake Bazel/Docker commands. It does not start services or delete real volumes.

Protobuf persistence is MQTT → bridge → `sensor_data` → core `db_writer` →
Postgres. `data_flow_test` requires the writer service from ingest's Compose
project. Raw test producers must include the `car_type` header; malformed records
block the persistence group intentionally. The
[writer guide](../processors/db_writer/README.md) describes batching.


## Artemis frequency-pool correctness

The opt-in `//apps/telemetry/stack/ingest:pool_storage_test` uses
`POOL_TEST_DATABASE_URL`, pointing to an initialized **disposable** Timescale
database. It rolls back its test transactions and covers raw timestamps,
zero/false values through the existing Kafka writer, replay, conflicts, late
session merging, bucket joins, peaks and additive schema application.
The viewer's `tests/storage/artemisStorage.test.ts` similarly opts in with
`ARTEMIS_STORAGE_TEST=1` and `ARTEMIS_DATABASE_URL`. It inserts/deletes temporary
fixture rows; do not point it at a live recording database.

The ordinary `data_flow_test` verifies both paths: Orion packet/FK persistence
and Artemis timestamp-based raw pools, including duplicate delivery.

For an isolated stack, `MQTT_HOST`/`MQTT_PORT`, `POSTGRES_HOST`/`POSTGRES_PORT`
and `KAFKA_BROKERS` select published test ports. Database credentials come from
`POSTGRES_USER`/`POSTGRES_PASSWORD`; no production database is required.

## Artemis native BEVO integration

`//apps/telemetry/stack/tests:native_bevo_flow_test` starts the actual Rust UDP
acquisition, logger and publisher against the isolated stack configured by the
existing test environment variables. It sends 333 Hz pedal frames, 100 Hz torque
frames, one complete 33-frame cell scan and an incomplete next scan. Assertions
cover fresh-only pool writes, counts, receipt timestamps, missing neighbors,
shared-recording pool CSVs and graceful flush. Reserve local UDP 5005/5006 and
`/tmp/BEVO_cand*.sock`; run only on a development machine without BEVO daemons.
The test creates a temporary identity reconciliation plan for its compiled
Artemis schema. It never opens a physical CAN interface.

`data_flow_test` checks explicit Artemis batches and legacy Orion independently,
including replay through MQTT, Kafka and the existing writer. These integration
targets require a running disposable stack; unit tests alone do not prove it.
