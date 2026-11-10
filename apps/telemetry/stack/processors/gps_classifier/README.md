# GPS classifier

This optional processor classifies driving behavior from GPS telemetry and
writes classifier results through the shared analysis/database layer.

Targets follow the standard `gps_classifier_binary`, `_image`, `_load`,
`_push`, and `_smoke_test` contract. The legacy `gps_classifier_tarball` label
aliases `_load` for one migration cycle. Start it with
`./apps/telemetry/stack/server_devtool.sh enable gps_classifier` after the core
is healthy.

This optional processor currently selects Orion for its database work. It does
not implement Artemis frequency-pool queries; enabling it does not add a second
telemetry writer.
