# Artemis demo dashboard

The `artemis-demo` dashboard uses the provisioned `artemis` gateway datasource.
It compares live replacement windows with historical queries for pedals, torque,
pack voltage/current and temperatures. Units come from deployed signal discovery.
Arrays require an explicit projection before they can be charted.

Compose provisions this directory automatically. Start the isolated stack with
`bash apps/telemetry/stack/tests/run_query_gateway_test.sh`, or publish into the normal
stack with `bazel run //apps/telemetry/analysis:publish_demo -- --duration 60`.
Open `/d/artemis-demo` in Grafana and select the sender's time range for history.
Live status and the effective bucket are available in Query inspector frame
metadata. The sender is synthetic; these plots do not validate vehicle physics.

## Board health and faults

`board_health.json` graphs each board's `board_status.<board>_last_seen_s`
against board_health's 0.5 s threshold; each line is named by its query
(`byFrameRefID`) so live frames keep one legend entry per board. `faults.json`
shows the `diagnostics_high` pedal and STOMP flags and the inverter fault words
as OK/FAULT timelines; boolean channels only support `raw` and `last`, and the
pedal panel uses one `raw` query so its rows keep a fixed order. Both carry the
`pdr_demo` tag, which their **Demos** header link lists. Drive them with the
[recorded replay](../../../README.md#board-health-and-fault-demo).
