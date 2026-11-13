# Query verification

`npm run test:query` runs `//apps/telemetry/stack/kafka:query_gateway_test`.
Query behavior belongs to that service; the viewer no longer contains a second
SQL reader or cursor implementation. See the
[gateway guide](../../../../../stack/kafka/gateway/README.md) and
[Docker integration runner](../../../../../stack/tests/README.md).

`npx vitest run --config vitest.config.mjs tests/query` checks the viewer's
bucket adapter across request boundaries, including partial buckets and peaks.
It also verifies native SSE freshness updates and gap propagation without
duplicating unchanged samples from replacement windows.
Replay verification preserves a raw peak and sparse fields while excluding
other nanosecond samples and recording sessions within the same millisecond.
Application checks exercise snake_case widget inputs, GPS speed units and missing
power values, authenticated deployed discovery, and Grafana editor selection of
scalar/Boolean channels. The editor test executes the shipped SystemJS module.
