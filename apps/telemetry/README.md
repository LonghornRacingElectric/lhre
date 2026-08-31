# Telemetry

This tree contains the server ingestion stack, processors, analysis utilities,
and the Next.js viewer. Server containers are built with Bazel; the complete
target reference and the build/load distinction are in
[BAZEL_BUILD.md](BAZEL_BUILD.md).

## Running the server

Copy `.env.example` to `.env` and provide the database credentials. Docker,
Docker Compose v2, and Bazel are required.

```bash
# Build and load every core image, then start the core detached.
bazel run //apps/telemetry:core_up

# Or use the deploy-box interface for component-level control.
cd apps/telemetry/stack
./server_devtool.sh build
./server_devtool.sh status
./server_devtool.sh logs
```

`server_devtool.sh up` deliberately does not rebuild. Use `build <component>`
to rebuild one image or `enable <processor>` to build and start an optional
processor. Closing logs does not stop the detached containers; use
`server_devtool.sh stop` when the stack should come down. Data volumes are
preserved unless an explicit reset command is used.

The core is Kafka/bridge, ingest with Mosquitto/PostgreSQL/Grafana, and the
field enricher. Optional processors are GPS classifier, lap timer, track
mapper, Kafka test, GG plot, and car status.

## Viewer

The viewer remains an npm/PM2 application rather than a Bazel image:

```bash
cd analysis/database/viewer_tool
npm ci
npm run prisma-auth-generate
npm run prisma-angelique-generate
npm run prisma-telemetry-generate
npm run dev -- --hostname 0.0.0.0 --port 3001
```

For deployment, run `npm run build` and use the checked-in
`ecosystem.config.js` with PM2. Seed a new auth user with
`npm run prisma-auth-seed -- <username> <password>`.

## Tests

```bash
bazel test //apps/telemetry:unit_tests
bazel test //apps/telemetry:core_smoke_tests
bazel test //apps/telemetry:optional_smoke_tests
```

Integration tests need a running core and the credentials from `.env`:

```bash
bazel test //apps/telemetry:integration_tests --test_output=errors
```

Docker-backed targets are manual/local and are exercised by the telemetry CI
workflow. Normal repo presubmit remains service-free.
