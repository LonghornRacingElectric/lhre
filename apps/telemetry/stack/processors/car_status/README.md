# Car status processor

This optional processor classifies raw frames into high-level car states,
publishes transitions and heartbeats, and can persist state segments. The pure
state-machine test is `:classifier_test`.

Container targets follow the standard `car_status_binary`, `_image`, `_load`,
`_push`, and `_smoke_test` contract. The image contains the generated schema
runfiles and shared network configuration. Start it explicitly with
`./apps/telemetry/stack/server_devtool.sh enable car_status`.
