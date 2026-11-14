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
