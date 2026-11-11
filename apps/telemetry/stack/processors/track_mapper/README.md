# Track mapper

The optional track mapper consumes raw Kafka sensor packets, decodes GPS data,
filters jitter, and publishes a per-car stream of track points.

Targets follow the standard `track_mapper_binary`, `_image`, `_load`, `_push`,
and `_smoke_test` contract. The image receives the selected ingest schema and
network configuration through Bazel runfiles; Compose no longer supplies the
protobuf directory as the source of the executable image.

Raw telemetry decoding supports only explicit Orion and Artemis car headers.
Unknown headers are rejected rather than decoded as the default car.

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../../README.md).
