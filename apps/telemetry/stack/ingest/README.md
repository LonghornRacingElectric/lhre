# Ingest

Forwards Artemis `TelemetryBatch` payloads from MQTT `artemis/data` or
`artemis/data/<schema_id>` through the gRPC bridge to Kafka. Other telemetry
topics are rejected. The bridge request
sets `car_type: Artemis` and `payload_format: artemis_updates_v1`.

The MQTT callback queues raw bytes without blocking on network I/O. A single
worker forwards them in order and retries failed or uncertain sends until Kafka
acknowledges the record. The queue holds 30,000 batches; a full queue logs and
drops new input. MQTT subscriptions are restored after reconnecting.

The shared pool writer assigns ordinary contiguous batches to a recording in
one lookup and flush. Discontinuous batches and late data spanning multiple
recordings retain the historical merge path.
Pool upserts use SQLAlchemy's bulk execution within the existing transaction;
replays remain idempotent and conflicting values roll back the batch.

Ingest does not allocate packet IDs, write CSVs, open database sessions or apply
DDL. GPS travels in the native Artemis updates. There is no separate GPS JSON
input or packet-ID welcome exchange.

```bash
bazel build //apps/telemetry/stack/ingest:ingest_binary
bazel build //apps/telemetry/stack/ingest:ingest_image
bazel run //apps/telemetry/stack/ingest:ingest_load
bazel test //apps/telemetry/stack/ingest:ingest_smoke_test
```

`ingest_binary` uses `:mqtt_transport`, containing the MQTT/gRPC transport and
generated bridge bindings. `:ingest_lib` also exposes the shared decoding and
storage helpers used by consumers. These helpers are separate from the active
ingest executable.

The image packages the generated Artemis contract and descriptor under
`/schema/` for the [schema authority](../../schema_registry/README.md). The
bridge uses matching generated Artemis bindings. Network defaults come from
`net_configs.json`; `AWS_MQTT_IP` overrides the broker target.

The [Kafka bridge](../kafka/README.md) acknowledges successful raw publication.
The [database writer](../processors/db_writer/README.md) owns subsequent storage.
Database/Grafana provisioning is described in the [stack guide](../README.md).

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../README.md).
## Query access

Compose runs the [Artemis gateway](../kafka/gateway/README.md) alongside the
existing writer. Set `QUERY_API_TOKEN` in the root environment and the viewer's
server environment. Artemis Grafana uses UID `artemis` with the gateway
datasource and generated signal builder; convert any old SQL panel targets.
The query port is published only on loopback. Existing Postgres access remains
available for metadata and legacy clients; restrict it after those clients have
completed their cutover. The `analysis` role
receives read grants on fresh database initialization. Existing installations
must apply the generated schema and provision equivalent grants before rollout.

The query ring and its baseline start with conservative 64 MiB bootstrap
budgets. Sealed L1/L2 caches default off; configure measured budgets through
`QUERY_L1_BYTES` and `QUERY_L2_BYTES`. The cache volume is disposable.

## Decode router

Every `sensor_data` record carries a `schema_fingerprint` header: the
`schema_id` of the wire contract its payload was encoded with
(`//lib/codegen/telemetry:artemis_schema_contract`). The bridge stamps its own
deployed `schema_id` (from `/schema/schema_contract.json` in its image) unless
the publisher named one by sending to `artemis/data/<schema_id>`.
Before decoding, the bridge sends foreign-schema bytes to
`sensor_data_quarantine` with a `quarantine_reason: schema_mismatch` header.
Malformed deployed-schema batches go there with `invalid_payload` instead.
Kafka retains the original bytes and headers for inspection and later replay;
the RPC succeeds only after Kafka acknowledges them. This lets subsequent
valid batches continue through the existing MQTT worker.
Only valid deployed-schema batches reach `sensor_data` and the best-effort
`grafana_data_artemis` feed. The database writer, processors and gateway ring
continue consuming `sensor_data` with the deployed decoder.

`decode_router.route()` decodes Artemis records only with the deployed schema.
The [decoder table](../../schema_registry/README.md#decoder-table) distinguishes
unknown and unapproved fingerprints. Approval alone does not provide a decoder:
an approved non-deployed entry raises `decoder_unavailable`. Other rejection
reasons are `unknown_schema`, `unapproved_bundle` and `invalid_payload`.
Records with no fingerprint from older bridges retain the deployed-schema
assumption. Per-bundle decoders and automatic replay are not implemented.
BEVO still needs to send its fingerprint explicitly; an unlabelled publisher
asserts that its bytes match the deployed schema.

```bash
# inspect accepted and quarantined records on the running stack (read-only)
bazel run --config=local //apps/telemetry/stack/ingest:decode_router_demo -- --from-kafka localhost:29092
# publish one batch per case through MQTT -> ingest -> bridge
bazel run --config=local //apps/telemetry/stack/ingest:decode_router_demo -- --send localhost:1883
# no stack: route the same cases plus malformed records in-process
bazel run --config=local //apps/telemetry/stack/ingest:decode_router_demo -- --samples

bazel test --config=local //apps/telemetry/stack/ingest:decode_router_test
bazel test --config=local //apps/telemetry/stack/ingest:decode_router_integration_test
```
