# Query gateway executable

`//apps/telemetry/stack/kafka:query_gateway_binary` wires the generated Artemis
contracts, read-only Postgres connections, shared Kafka consumer and cache worker.
The cache worker seals database revisions without depending on Kafka head
offsets or opening a second Kafka client.
See [configuration and API](../../gateway/README.md). `--help` lists the settings
without opening database or Kafka connections.
