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

All Python services use the shared `requirements.txt` and committed
`requirements_lock.txt`. Do not add a new per-service lock. Add a runtime pin
to the shared input and run `bazel run //apps/telemetry/stack:requirements.update`.

Integration tests under `tests/` assume the core is already running. They are
manual/local targets because they require Docker and credentials.
