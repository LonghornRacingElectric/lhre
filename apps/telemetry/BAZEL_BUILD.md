# Telemetry Bazel build

Bazel is the source of truth for every container used by the telemetry core
and its optional processors. Docker Compose does not build these images. It
starts tags that an explicit Bazel load target has placed in the local Docker
daemon.

## Image sets

The core is the minimum path that can receive, transport, enrich, persist, and
display telemetry:

| Component | Role | Image source |
| --- | --- | --- |
| Mosquitto | MQTT entry point | pinned upstream |
| PostgreSQL | durable telemetry storage | pinned upstream |
| Kafka | live event transport | pinned upstream |
| Grafana | dashboards | pinned upstream |
| ingest | MQTT decode, database write, Kafka forward | this repo |
| kafka_bridge | gRPC-to-Kafka bridge | this repo |
| field_enricher | configured derived fields | this repo |

The optional set contains `gps_classifier`, `lap_timer`, `track_mapper`,
`kafka_test`, `gg_plot`, and `car_status`. They consume or derive telemetry;
none is required to preserve the raw stream. `kafka_base` remains a developer
template and is deliberately not a runnable stack image.

The viewer, logsync worker, and Grafana plugin build are outside this port.
Their existing npm, PM2, or legacy Docker workflows remain in place.

## Main commands

```bash
# Produce OCI layouts under bazel-bin. Docker is not touched.
bazel build //apps/telemetry:core_images
bazel build //apps/telemetry:optional_images
bazel build //apps/telemetry:all_images

# Build and load tags into the local Docker daemon.
bazel run //apps/telemetry:load_core_images
bazel run //apps/telemetry:load_optional_images
bazel run //apps/telemetry:load_all_images

# Load the core and start its Compose projects detached.
bazel run //apps/telemetry:core_up

# Docker-backed validation of the loaded image and expected executable.
bazel test //apps/telemetry:core_smoke_tests
bazel test //apps/telemetry:optional_smoke_tests
```

`build` outputs an OCI layout in `bazel-bin`; it does not write an image archive
into the source tree. `load` is the operation that calls Docker. Compose uses
the resulting `lhre/telemetry-*:...` tags and always starts with
`--no-build`, preventing a Dockerfile from silently replacing a Bazel image.

`//apps/telemetry:telemetry_images` and
`//apps/telemetry:telemetry_tarballs` are compatibility aliases for one
migration cycle. New automation uses `all_images` and `load_all_images`.

## Per-service contract

Every repo-owned runnable service exposes the same labels in its package:

| Suffix | Meaning |
| --- | --- |
| `<service>_binary` | fast host binary for development and tests |
| `<service>_image` | reproducible Linux/AMD64 OCI layout |
| `<service>_load` | load the image into the local Docker daemon |
| `<service>_push` | push the image to its fixed GHCR repository |
| `<service>_smoke_test` | load it and verify its executable in Docker |

For example:

```bash
bazel build //apps/telemetry/stack/ingest:ingest_binary
bazel build //apps/telemetry/stack/ingest:ingest_image
bazel run //apps/telemetry/stack/ingest:ingest_load
bazel test //apps/telemetry/stack/ingest:ingest_smoke_test
bazel run //apps/telemetry/stack/ingest:ingest_push -- --tag my-branch
```

The image macro transitions only the packaged binary to
`//platforms:linux_amd64`. A macOS developer can therefore build the same
container closure as CI while a direct `<service>_binary` build remains native
to the host. Repo-owned images bundle the executable's complete Bazel runfiles,
including native Linux Python wheels. The base image, Go toolchain, Python
runtime, and upstream infrastructure manifests are pinned in `MODULE.bazel`.

Pinned upstream infrastructure exposes `_image`, `_load`, and `_smoke_test`.
It has no `_binary` or `_push` because this repo neither builds nor publishes
that software.

## Server workflow

[`stack/server_devtool.sh`](stack/README.md) maps every Bazel-managed component
to its `_load` target. `build`, `rebuild`, and `enable` run that target before
starting Compose; `up` only reuses already loaded tags. This keeps rebuilds
per-image while retaining a single core-stack command.

Image variables such as `TELEMETRY_INGEST_IMAGE` and
`TELEMETRY_KAFKA_IMAGE` can override the local tag in Compose. That is useful
for a branch image or a registry digest without editing YAML.

## Schema boundary and branch testing

Services consume `//apps/telemetry:current_schema_bundle`, not the physical
BEVO generator path. Today that target selects the checked-in BEVO protobuf
schema and Bazel-generated CAN JSON. It is only a dependency seam: this change does
not implement a registry, schema negotiation, or multi-version decoding.

A future registry can replace the selection behind this target and generate
board-specific libraries without changing every service BUILD file. Deployed
messages still need an explicit schema identity before a main-branch server can
safely decode traffic from a board branch. Until that protocol is designed,
test a schema-changing board branch with a matching branch of the server/image;
branching only the firmware is safe when the wire schema is unchanged.

## Dependencies and CI

Python stack dependencies are declared once in `stack/requirements.txt` and
locked in `stack/requirements_lock.txt`. Update them with:

```bash
bazel run //apps/telemetry/stack:requirements.update
```

The Kafka bridge keeps `go.mod` and `go.sum` for Go tooling; rules_go and
Gazelle use those files for the Bazel graph. The telemetry workflow builds
`all_images`, runs `core_smoke_tests`, then starts the core with Compose
`--no-build` before integration tests. Docker-backed tests are tagged `manual`
and `local`, so normal remote `bazel test //...` stays hermetic.
