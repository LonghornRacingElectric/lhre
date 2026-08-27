# Telemetry System - Bazel Build Guide

This document describes the Bazel build setup for the telemetry system, including Docker container builds, testing, and deployment.

## Architecture Overview

The telemetry system consists of the following components on the `telemetry_network` Docker network:

### Infrastructure Services (External Images)
- **Mosquitto** (MQTT Broker): Receives telemetry data from the car
- **PostgreSQL**: Stores processed telemetry data (Nightwatch & Angelique databases)
- **Kafka**: Message queue for processor communication
- **Grafana**: Visualization dashboard

### Custom Services (Built with Bazel)
- **Ingest Service** (`//apps/telemetry/stack/ingest`): 
  - Subscribes to MQTT topics
  - Decodes protobuf/pickle/base64 payloads
  - Writes to PostgreSQL
  - Forwards to Kafka `sensor_data` topic

- **GPS Classifier** (`//apps/telemetry/stack/processors/gps_classifier`):
  - Consumes from MQTT
  - Classifies driving patterns (turns, acceleration)
  - Writes classifications to PostgreSQL

- **Lap Timer** (`//apps/telemetry/stack/processors/lap_timer`):
  - Tracks lap times using GPS gate detection
  - Writes lap data to PostgreSQL

- **Kafka Base** (`//apps/telemetry/stack/processors/kafka_base`):
  - Base Kafka consumer template
  - Demonstrates Kafka → processing flow

### Non-Docker Components
- **Analysis Library** (`//apps/telemetry/analysis`):
  - Database utilities (`sql_utils/`)
  - Data visualization tools
  - Testing utilities (`paho_testing.py`)

## Quick Start

### Build All Docker Images
```bash
bazel build //apps/telemetry:telemetry_images
```

### Build and Load Images to Local Docker
```bash
# Build tarball and load to Docker
bazel run //apps/telemetry/stack/ingest:ingest_tarball
bazel run //apps/telemetry/stack/processors/gps_classifier:gps_classifier_tarball
bazel run //apps/telemetry/stack/processors/lap_timer:lap_timer_tarball
bazel run //apps/telemetry/stack/processors/kafka_base:kafka_base_tarball
```

### Run Unit Tests (No Docker Required)
```bash
bazel test //apps/telemetry:unit_tests
```

### Run Integration Tests (Requires Docker Containers Running)
```bash
# First, start the Docker stack
cd telemetry/stack/ingest && docker-compose up -d
cd telemetry/stack/kafka && docker-compose up -d

# Then run integration tests
bazel test //apps/telemetry:integration_tests --test_tag_filters=integration
```

### Run Full Integration Test with Docker Lifecycle
```bash
bazel test //apps/telemetry:full_integration_tests --test_tag_filters=manual
```

## Target Reference

### Build Targets

| Target | Description |
|--------|-------------|
| `//apps/telemetry:telemetry_images` | All Docker images |
| `//apps/telemetry:telemetry_tarballs` | All Docker tarballs for local loading |
| `//apps/telemetry:telemetry_all` | Everything (Docker + non-Docker) |
| `//apps/telemetry:telemetry_lib` | All Python libraries |
| `//apps/telemetry:config_files` | Configuration files |
| `//apps/telemetry:compose_files` | All docker-compose files |

### Individual Service Targets

| Service | Image Target | Tarball Target | Push Target |
|---------|--------------|----------------|-------------|
| Ingest | `//apps/telemetry/stack/ingest:ingest_image` | `:ingest_tarball` | `:ingest_push` |
| GPS Classifier | `//apps/telemetry/stack/processors/gps_classifier:gps_classifier_image` | `:gps_classifier_tarball` | `:gps_classifier_push` |
| Lap Timer | `//apps/telemetry/stack/processors/lap_timer:lap_timer_image` | `:lap_timer_tarball` | `:lap_timer_push` |
| Kafka Base | `//apps/telemetry/stack/processors/kafka_base:kafka_base_image` | `:kafka_base_tarball` | `:kafka_base_push` |

### Test Targets

| Target | Description | Docker Required |
|--------|-------------|-----------------|
| `//apps/telemetry:unit_tests` | Unit tests (protobuf, analysis) | No |
| `//apps/telemetry:integration_tests` | Connectivity & data flow tests | Yes |
| `//apps/telemetry:full_integration_tests` | Full stack with Docker lifecycle | Yes (managed) |
| `//apps/telemetry:telemetry_tests` | All tests | Yes |

## Data Flow Testing

The integration tests validate the following data flows:

1. **MQTT → Ingest → Database**
   - Protobuf serialized sensor data
   - Pickle serialized data (legacy)
   - Base64 encoded data (Angelique)

2. **MQTT → Ingest → Kafka**
   - `sensor_data` topic forwarding

3. **Kafka → Processor**
   - Consumer group message processing
   - Protobuf decoding in consumers

4. **Config Flow**
   - `config/flask` event start/stop
   - `config/test` processor configuration

## Adding New Tests

Tests are located in `telemetry/stack/tests/`. To add a new test:

1. Create a new Python test file (e.g., `test_my_feature.py`)
2. Add the test target to `telemetry/stack/tests/BUILD.bazel`:
   ```python
   py_test(
       name = "my_feature_test",
       srcs = ["test_my_feature.py"],
       deps = [
           ":test_utils",
           # Add dependencies
       ],
       tags = ["integration"],  # or ["unit"] for non-Docker tests
   )
   ```
3. Add to the appropriate test suite in `telemetry/BUILD.bazel`

## Adding New Processors

To add a new Kafka consumer processor:

1. Start from template: copy `telemetry/stack/processors/kafka_base/` to `telemetry/stack/processors/my_processor/`
2. Update Python files and Docker metadata for your processor
3. Create `BUILD.bazel` following the pattern in `gps_classifier/BUILD.bazel`
4. Add to the aggregate targets in:
   - `telemetry/stack/processors/BUILD.bazel`
   - `telemetry/stack/BUILD.bazel`
   - `telemetry/BUILD.bazel`

Template guidance and processor best practices are documented in:
`telemetry/stack/processors/kafka_base/README.md`

## Environment Configuration

The telemetry system uses environment variables for configuration:

- `IN_DOCKER`: Set to `1` when running in Docker container
- `POSTGRES_USER`, `POSTGRES_PASSWORD`: Database credentials
- `SERVER_TARGET`: Target server (LOCAL, SUBNET, EXTERNAL)
- `LOGLEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)

See `.env.example` for all available options.

## CI/CD Integration

For CI/CD pipelines:

```bash
# Build all images
bazel build //apps/telemetry:telemetry_images

# Run unit tests (fast, no Docker)
bazel test //apps/telemetry:unit_tests

# Push images to registry
bazel run //apps/telemetry/stack/ingest:ingest_push
bazel run //apps/telemetry/stack/processors/gps_classifier:gps_classifier_push
# etc.
```

## Troubleshooting

### "No module named 'paho.mqtt'"
Run `bazel sync` to ensure dependencies are downloaded.

### Docker network issues
Ensure the `telemetry_network` exists:
```bash
docker network create telemetry_network
```

### Container not starting
Check logs:
```bash
docker logs ingest
docker logs kafka
```

## In the lhre monorepo

This tree moved from `lhre-2026/telemtry` in August 2026 (ADR-005). What changed:

- Labels are `//apps/telemetry/...`. The pip hubs are `@telemetry_reqs` (analysis, per-OS locks) and `@telemetry_stack_reqs` (server stack); both serve the committed locks (compiled on 3.11) under the repo's 3.12 interpreter; re-lock on 3.12 when convenient.
- `protoc` for the generated `can_packets_pb2.py` comes from `//tools/protoc`, and the proto itself from `//apps/BEVO/schema`.
- Container images (`oci_image`) and the server-stack libraries are Linux only; `bazel build //...` on Windows skips them.
- Every test that needs Kafka, Postgres or MQTT is tagged `manual` and `local`, so presubmit's `bazel test //...` does not run it. They run through `.github/workflows/telemetry.yml` on every PR touching this tree (Docker Compose on the runner, credentials from repo secrets) or locally with `bazel test //apps/telemetry:integration_tests` after `stack/server_devtool.sh`. The `unit_tests` suite runs in presubmit.
- `compile_pip_requirements` targets are `manual` too; run `bazel run //apps/telemetry/analysis:telemetry_requirements.update` (and the stack equivalents) after editing a `requirements.txt`.
