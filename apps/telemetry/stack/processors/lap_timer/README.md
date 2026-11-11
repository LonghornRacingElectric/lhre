# Lap timer

The optional lap timer consumes classified GPS events, detects finish-line
crossings, and publishes lap updates. Its outbound web request means a running
container may require external network access.

Targets follow the standard `lap_timer_binary`, `_image`, `_load`, `_push`, and
`_smoke_test` contract. The legacy `lap_timer_tarball` label aliases `_load`.
Compose maps `host.docker.internal` on Linux as well as Docker Desktop so local
callback configuration behaves consistently.

This optional processor currently selects Orion for its database work. It does
not implement Artemis frequency-pool queries; enabling it does not add a second
telemetry writer.

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../../README.md).
