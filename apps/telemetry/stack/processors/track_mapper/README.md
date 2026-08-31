# Track mapper

The optional track mapper consumes raw Kafka sensor packets, decodes GPS data,
filters jitter, and publishes a per-car stream of track points.

Targets follow the standard `track_mapper_binary`, `_image`, `_load`, `_push`,
and `_smoke_test` contract. The image receives the selected ingest schema and
network configuration through Bazel runfiles; Compose no longer supplies the
protobuf directory as the source of the executable image.
