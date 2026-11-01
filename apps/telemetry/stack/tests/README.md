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

Bazel supplies `current_schema_sql` as a data dependency. The runner uses its
absolute runfiles path without invoking nested Bazel builds. When run directly
from a checkout, it builds the SQL target before starting any services. An
explicit existing absolute `ORION_DB_INIT_SQL` selects an admin-provided schema.
Cleanup only resolves the path for Compose interpolation: missing build outputs
or a failed schema build cannot block teardown, and Compose failures are reported.

The Docker-free `//apps/telemetry/stack:server_devtool_test` exercises schema
refresh, sudo forwarding, startup failures and the runner's cleanup path with
fake Bazel/Docker commands. It does not start services or delete real volumes.

Protobuf persistence is MQTT → bridge → `sensor_data` → core `db_writer` →
Postgres. `data_flow_test` requires the writer service from ingest's Compose
project. Raw test producers must include the `car_type` header; malformed records
block the persistence group intentionally. Separate disposable-database rollback
and replay tests live with the [writer](../processors/db_writer/README.md).
