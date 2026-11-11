# Car status processor

This optional processor classifies raw frames into high-level car states,
publishes transitions and heartbeats, and can persist state segments. The pure
state-machine test is `:classifier_test`.

Container targets follow the standard `car_status_binary`, `_image`, `_load`,
`_push`, and `_smoke_test` contract. The image contains the generated schema
runfiles and shared network configuration. Start it explicitly with
`./apps/telemetry/stack/server_devtool.sh enable car_status`.

Raw telemetry decoding supports only explicit Orion and Artemis car headers.
Unknown headers are rejected rather than decoded as the default car.

Artemis input requires `payload_format: artemis_updates_v1`. The shared decode
helper applies only observed fields to a timestamped live-state cache; older
observations cannot replace newer values. Orion decoding remains independent.

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../../README.md).
