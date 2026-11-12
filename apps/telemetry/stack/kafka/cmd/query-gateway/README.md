# Query gateway executable

`//apps/telemetry/stack/kafka:query_gateway_binary` wires the generated Artemis
contracts, read-only Postgres connections, shared Kafka consumer and cache worker.
See [configuration and API](../../gateway/README.md). `--help` lists the settings
without opening database or Kafka connections.
