# Query verification

`npm run test:query` runs `//apps/telemetry/stack/kafka:query_gateway_test`.
Query behavior belongs to that service; the viewer no longer contains a second
SQL reader or cursor implementation. See the
[gateway guide](../../../../../stack/kafka/gateway/README.md) and
[Docker integration runner](../../../../../stack/tests/README.md).
