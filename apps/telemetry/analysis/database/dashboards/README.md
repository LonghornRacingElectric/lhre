# Telemetry dashboards

Grafana provisions these JSON files into folders matching the directory tree.
The [Artemis setup guide](../../../stack/kafka/grafana/README.md) covers API
tokens, datasource provisioning, local URLs and connection checks.

`artemis/board_health.json` is the current gateway example: datasource UID
`artemis`, plugin `lhre-artemis-datasource`, and targets with generated
`signals`, `agg`, `bucket_ms` and `live` settings. Its stat tiles show the last
board age; the time-series panel keeps maximum ages within each bucket. It
requires actual `board_status` observations, such as the
[recorded replay](../../README.md#board-health-and-fault-demo). It shows communication
ages rather than interpreting the processor's board-state snapshots.

The `orion`, `angelique` and `nightwatch` folders retain legacy SQL and direct
Kafka dashboards. They can appear in a fresh Grafana even when their original
databases, topics or plugins are unavailable. They are not Artemis examples.
Changing a datasource UID or car name does not convert their query format or
signal schema. Use the Artemis builder to select generated channels when
creating new dashboards; history has Live disabled, and rolling windows have
Live enabled.

Grafana permits editing provisioned dashboards, but UI changes are not written
back here. Export the dashboard JSON and commit it to retain changes across
deployments.
