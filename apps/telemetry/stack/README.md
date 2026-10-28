# Telemetry server stack

This directory contains the server-side ingestion path, Kafka bridge, and
derived-data processors. The complete image and target model is documented in
the [Bazel build guide](../BAZEL_BUILD.md).

`server_devtool.sh` is the deploy-box interface. Its core is `kafka`, `ingest`,
and `field_enricher`; those Compose projects also start the pinned brokers,
database, and Grafana images. Commands are detached by default:

```bash
./server_devtool.sh build          # Bazel build/load core, then Compose up
./server_devtool.sh build ingest   # rebuild/load only the ingest project
./server_devtool.sh up             # reuse already loaded images
./server_devtool.sh enable gg_plot # build/load and start one processor
./server_devtool.sh logs
```

For Bazel-managed components, `build` invokes each component's `_load` target
and then `docker compose up --no-build`. Logsync and the PM2 viewer retain their
existing non-Bazel workflows. The Linux devtool is authoritative for the new
image path; the Windows script has not yet been ported to the Bazel loader.
Before creating ingest containers (`up`, `create`, `run`, or `watch`), the devtool resolves
`//apps/telemetry:current_schema_sql` and mounts that generated Orion DDL into
Postgres. A `lib/spec` edit therefore reaches a newly initialized server
database without rewriting `orion_db_init.sql` in the source tree.
For these commands the devtool invokes Bazel even when the DDL output already exists, so Bazel
checks spec dependencies instead of reusing a file merely because it is
present. Compose receives the selected path explicitly after `sudo` as well
as without sudo. An explicit `ORION_DB_INIT_SQL` override must be an existing
absolute file; missing overrides fail rather than falling back.

The shared `orion_schema.sh` path setup is also used by the integration runner.
Teardown, logs, configuration inspection, and start/restart of existing
containers only supply the selected absolute path for Compose interpolation;
they neither build SQL nor require its output to exist. Stopping services
therefore works while the spec is invalid or Bazel is unavailable. No dummy SQL
or checked-in fallback is mounted. The Windows tool applies the same path and
startup checks, but still requires images loaded through the Bazel workflow.

Direct Compose use must also set `ORION_DB_INIT_SQL`. The mount is read-only,
does not create missing host paths and has no checked-in Orion SQL fallback.
Existing database volumes are not migrated by rebuilding this initialization
file. Integration tests do not create missing tables to conceal a failed init.
See the [integration runner notes](tests/README.md) before using its
volume-removing cleanup.

Starting/rebuilding/reloading the viewer also builds the shared schema bundle
and exports `ORION_PROTO_PATH`. Node/PM2 remains outside Bazel, but the viewer's
Orion protobuf and optional Prisma definitions are Bazel outputs.

Disk preflight uses Docker's data root on Linux. On macOS, that path exists
inside Docker Desktop's VM, so the devtool reports free space on the host
filesystem backing Docker Desktop instead. It also checks the daemon before
running preflight; start Docker Desktop when the daemon-unavailable message is
shown.

Docker-managed volumes are the default, which works on Linux, macOS, and CI.
The production server opts into bind-backed storage explicitly:

```bash
export TELEMETRY_STORAGE_ROOT=/mnt/server_ssd
./server_devtool.sh up
```

This preserves the deploy box's existing `app_data/telemetry_db`, `kafka-data`,
and `logsync` paths below that root. Set `TELEMETRY_DB_DIR`, `KAFKA_DATA_DIR`,
or `LOGSYNC_DATA_DIR` to override an individual location. Storage settings
affect creation of a missing volume; the devtool never replaces an existing
Docker volume implicitly.

All Python services use the shared `requirements.txt` and committed
`requirements_lock.txt`. Do not add a new per-service lock. Add a runtime pin
to the shared input and run `bazel run //apps/telemetry/stack:requirements.update`.

Integration tests under `tests/` assume the core is already running. They are
manual/local targets because they require Docker and credentials. The data-flow
test generates an Orion protobuf packet through the Paho test generator and
requires that exact packet in raw Kafka, decoded Grafana Kafka, and Postgres;
successful publishing alone is not considered a pass.
