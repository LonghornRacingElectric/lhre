# GitHub Actions

Workflow behavior, BEVO's manual GHCR publication button/command, schema
change gating and required package permissions are documented in the
[CI runbook](../../docs/ci.md).

Executable builds and release packaging use Bazel. PR checks do not publish;
the unified CI workflow's `publish` input explicitly enables its gated BEVO release job.

Telemetry builds `//apps/telemetry:new_car_schema_bundle` and supplies the
absolute `ARTEMIS_DB_INIT_SQL` output path to Compose for service startup
and cleanup. The path is also written to the integration tests' `.env`.
The stack initializes Artemis and shared telemetry metadata; CI does not
require an Orion database.
