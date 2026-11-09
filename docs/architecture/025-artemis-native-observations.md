# ADR-025: Carry fresh Artemis observations end to end

- **Status:** Accepted; extends [ADR-021](021-artemis-storage-scope.md) and [ADR-024](024-artemis-query-gateway.md)
- **Date:** 2026-11

## Context

Frequency pools cannot recover native samples from a periodically repeated
full-state snapshot. Such snapshots also turn stale slow signals into new
observations. Artemis BEVO and its server need one explicit presence contract;
Orion remains a separate legacy server integration.

## Decision

Keep CAN reading and decoding. After decoding, emit touched fields with their
BEVO receipt time in Unix nanoseconds. Complete repeated-field fragments within
one nominal period before emitting an array. Keep the periodic dashboard cache
on its own socket. Independent bounded logger/publisher streams consume fresh
updates; no per-frequency timers or new database writer service are required.

Artemis protobuf batches carry typed values plus numeric updated-field paths.
The MQTT-to-RPC bridge explicitly marks `artemis_updates_v1` in RPC and Kafka
headers. Artemis root field 2 and `packet_id` are reserved. The release updater
requires projection version 2 and projects presence together with values.

The existing Kafka database writer stores only observed fields. Raw keys are
`(session_start_ms, time, sample_time_ns)`; the millisecond time remains the
Timescale partition/bucket key. Disjoint duplicate updates merge; conflicting
values roll back. Bucket `last` orders by the fine timestamp and skips NULL.
Raw gateway pagination includes the fine timestamp as a decimal string.

Local logging uses one CSV per frequency pool with a shared recording start,
explicit presence and fine timestamps. Existing logsync transfers/previews
remain CSV based. Neither a journal format nor automatic historical import is
introduced.

## Alternatives considered

Per-frequency cached snapshots would still stamp stale values as fresh.
Per-field timer threads add scheduling work without improving CAN acquisition.
A second database writer would duplicate the Kafka consumer's responsibilities.
A protobuf log journal would require a new logsync conversion and preview path.

## Consequences

Deploy matching Artemis producers, ingest/bridge and writer against a fresh
Artemis schema. There is no old-format BEVO fallback or automatic storage
migration. Millisecond-only CSV consumers must account for presence and pool
files. Local buffering and bounded pre-Kafka queues can lose data under power
loss or sustained outages; the demo does not promise lossless acquisition.
