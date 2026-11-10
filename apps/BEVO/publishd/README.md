# publishd

Publish Artemis fresh updates from cand to MQTT `artemis/data` using QoS 1.
`PUBLISHD_MQTT_HOST`, `PUBLISHD_MQTT_PORT` and `PUBLISHD_MQTT_CLIENT_ID` select
the broker/client identity. There is no packet-ID handshake, Orion fallback
or global `PUBLISHD_PUBLISH_HZ` snapshot throttle.

Default behavior forwards every received CAN observation. Explicit per-field
uplink limits from the spec coalesce only telemetry fields: retain the newest
pending value with its original receipt timestamp. Event and health fields
bypass that limit. One deadline/receive loop handles all rates; no per-rate
interrupt service or async runtime is added.

Updates batch for at most 5 ms, 64 updates or 64 KiB into `TelemetryBatch`
version 1. The transport contract is `artemis_updates_v1`. Each update carries
nanosecond receipt time, typed values and numeric updated-field paths. Explicit
presence preserves zero, false and empty arrays without replaying stale cache
values. Bounded native and MQTT queues isolate acquisition from network stalls;
overflow is counted and logged. This is best-effort before Kafka acceptance,
with local CSVs available separately.

`BEVO_CORE_RELEASE_ROOT` selects an [updater](../updater/README.md) release with
`projection_version: 2`, `wire_format: artemis_updates_v1` and a matching
`reconciliation.json`. Startup validates the release/schema identity. Generated
projection filters values and presence together; excluded fields never acquire
fresh timestamps in the database. Unsupported enum values omit the whole field,
including a repeated enum array, preserving its indices.

Missing/mismatched plans stop publishing. Cand, dashboard and loggerd remain
independent. Rebuild and deploy matched Artemis server images and BEVO releases;
there is no format guessing or unfiltered publisher mode.
