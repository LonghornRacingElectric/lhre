# GG plot processor

The optional GG plot processor decodes raw sensor packets, filters lateral and
longitudinal acceleration, and publishes points for the live GG visualization.

Targets follow the standard `gg_plot_binary`, `_image`, `_load`, `_push`, and
`_smoke_test` contract. It consumes the same selected schema bundle as ingest,
so a schema-changing board branch must use a matching server image until
version negotiation exists.

Raw telemetry decoding supports only explicit Orion and Artemis car headers.
Unknown headers are rejected rather than decoded as the default car.

Artemis input requires `payload_format: artemis_updates_v1`. The shared decode
helper applies only observed fields to a timestamped live-state cache; older
observations cannot replace newer values. Orion decoding remains independent.

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../../README.md).
