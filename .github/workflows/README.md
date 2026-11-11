# GitHub Actions

Workflow behavior, BEVO's manual GHCR publication button/command, schema
change gating and required package permissions are documented in the
[CI runbook](../../docs/ci.md).

Executable builds and release packaging use Bazel. PR and push checks validate
without publishing. **Run workflow → publish** explicitly enables publication
after verification. Only a manual run on the configured deployment branch can
promote the discovery channel.

Telemetry builds `//apps/telemetry:new_car_schema_bundle` and supplies the
absolute `ARTEMIS_DB_INIT_SQL` output path to Compose for service startup
and cleanup. The path is also written to the integration tests' `.env`.
The stack initializes Artemis and shared telemetry metadata; CI does not
require an Orion database.
