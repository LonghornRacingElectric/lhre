# Artemis query gateway

One Go service reads Artemis telemetry for Grafana, the viewer and scripts.
The generated storage contract selects frequency pools; no packet joins or
hand-maintained signal lists are used. Postgres is authoritative. Kafka supplies
provisional live observations, not an alternative historical database.

## Build and run

```bash
bazel build //apps/telemetry/stack/kafka:query_gateway_binary
bazel test //apps/telemetry/stack/kafka:query_gateway_test
bazel run //apps/telemetry/stack/kafka:query_gateway_load
```

The [ingest Compose stack](../../ingest/README.md) runs the loaded image beside
the existing database writer. Apply `//apps/telemetry:schema_apply -- --apply`
to an existing Artemis database before starting the new writer and gateway.
Schema changes are additive: recording revisions and Kafka persistence offsets.
The gateway never applies DDL or writes telemetry.

Required settings are `QUERY_API_TOKEN`, `ARTEMIS_DATABASE_URL`, and nonzero
`QUERY_RING_BYTES` / `QUERY_BASELINE_BYTES`. `TELEMETRY_DATABASE_URL` enables
drive-day selection. Connection settings also accept `ARTEMIS_DB_*` and
`TELEMETRY_DB_*`. Use the `analysis` role: initialization grants SELECT on
existing and future public tables. Existing installations must provision that
read role and grants too. Keep `QUERY_CURSOR_SECRET` stable across restarts.

`GET /healthz` is unauthenticated. All other routes require
`Authorization: Bearer <QUERY_API_TOKEN>`. Compose publishes the query API
on loopback; expose it through the authenticated viewer or a TLS
reverse proxy. Existing database access remains for metadata and legacy clients;
restrict that port after their cutover.

## Query interface

`GET /v1/signals` returns generated signals, units, pools and aggregate
capabilities. `GET /v1/sessions` lists recording bounds. `POST /v1/query`
(also `/api/query`) accepts:

```json
{
  "car": "artemis",
  "drive_day": 12,
  "t0": "2026-11-11T12:10:00Z",
  "t1": "2026-11-11T12:15:00Z",
  "signals": ["controls.apps1_travel", "pack.hv_pack_v"],
  "agg": "max",
  "bucket_ms": 1000,
  "max_points": 1000,
  "page_size": 1000
}
```

Omit `drive_day` for absolute time bounds. With a drive day, bounds are optional
and are intersected with its recorded window. Select up to 50 explicit signals,
`signals: ["*"]`, or generated `pools` names. Pool selection can span all groups.
Ranges are half-open. Raw reads preserve nanosecond sample identity and separate
pool observations. Buckets use the Unix epoch origin and keep sessions separate.
`mean`, `min`, `max` apply to numeric scalars; `last` also handles arrays and
booleans. Missing values remain missing; gaps are not filled forward.

The effective bucket is at least the requested width, the slowest selected native
period and the width required by `max_points`. Automatic widths above one second
round up to whole seconds to use sealed overviews. `rate` is an alternative to
`bucket_ms`. Raw queries omit both and use pagination. Responses include the
effective `bucket_ms`, `schema_version`, source names and a provisional flag.

JSON rows contain `ts`, decimal-string `sample_time_ns` and
`session_start_ms`, `signal`, and `value`. Big integers retain their exact string
representation. Session `0` denotes an unpersisted observation without a known
recording assignment. `Accept: text/csv` preserves the existing four-column
export. `Accept: application/vnd.apache.arrow.stream` returns long-form IPC with
typed timestamps/identities, signal names and lossless `value_json` cells.

Follow `next_page_token` with the same request. CSV/IPC expose it through
`X-Next-Page-Token`. Tokens bind the request and catalog and expire after 24 hours.
Gateway cursor version 4 replaces the old viewer cursor; restart a paged read
after upgrading. Pagination does not freeze subsequent late database writes.

Requests are limited to 256 KiB, 10,000 rows per page, 2 MiB per JSON page,
64 KiB per observation and a 30-second query deadline. Large raw scans fail
explicitly; use a smaller range or sealed bucketed history.

## Live coverage and source order

The first recent-window miss establishes a database baseline. The shared raw
Kafka consumer adds explicit fresh fields to that baseline. Subsequent requests
reuse it, including samples already persisted, while the Kafka coverage remains
contiguous. The existing writer records the next offset per partition in the
same transaction as its pool writes; Kafka group offsets are committed afterward.
This avoids gaps or duplicated samples as data crosses into Postgres.

External imports increment a separate revision, forcing a new baseline. Session
merges and storage-contract changes also invalidate their cache identities.
The gateway checks lightweight database metadata before trusting cached telemetry.
A database metadata outage therefore returns an unavailable result instead of
asserting that an unverified cache is current.

Lookup order is the recent baseline plus Kafka ring, L1 pool slabs, L2 Arrow
files, then Postgres for missing telemetry. Cold starts recover from persistence
offsets; the ring's own Kafka commits do not make its RAM durable. If a recent
request cannot establish complete coverage, it returns `tail_unavailable` until
the shared consumer catches up. The ring is bounded by time and accounted bytes;
it can discard pending samples under sustained pressure and reports coverage
gaps. It does not promise infinite buffering during a writer outage.

`GET /v1/stream?query=<URL-encoded query JSON>` uses SSE. `t1: "live"` and
`window_ms` select a moving window. Events replace that bounded window, including
its changing final bucket; clients must not append repeated snapshots as fresh
observations. Updates are coalesced at 250 ms. A truncated window is reported as
a gap, not silently presented as complete.

Existing JSON state feeds use `GET /v1/topics/<topic>`. The allowlist defaults to
`grafana_data_artemis`, `grafana_data_artemis_derived` and `car_status`; configure
`QUERY_LIVE_TOPICS` explicitly for other Artemis producers. These are best-effort
latest-state feeds. They have no implied historical representation. They share
the gateway Kafka consumer with native telemetry; clients open no consumers.

## Cache ownership and sizing

L1 stores uncompressed Arrow slabs per pool and minute, keyed by storage identity,
session and revision. Second-access admission and Ristretto's frequency/cost
policy keep one-off scans from displacing hot slabs. Simultaneous identical
misses share the fetch. `QUERY_PIN_DAYS` names UTC dates; `QUERY_PIN_BYTES` reserves
at most half of `QUERY_L1_BYTES` for hot entries from those days. Pinning is bounded
and does not preload an entire day.

The single background seal worker exports idle sessions after five minutes
without writes and after the writer reaches a captured Kafka backlog boundary.
It writes versioned Arrow files and manifests with checksums, source counts,
timestamp bounds, persistence offsets and a batch index. Raw batches cover one
minute; companion one-second overviews retain count/sum/min/max/last state.
Larger means weight sample counts; partial boundary seconds use raw data.

Files are closed and synced before atomic publication. Revisions are checked
again before publication and on reads. Late writes invalidate the old revision;
readers can finish on an old mapping. Missing/corrupt files fall back to Postgres
and queue a reseal. Deleting the entire cache loses no authoritative data.
Disk eviction uses decayed access frequency per byte. Evicted files are rebuilt
when requested, not in a continuous eager rebuild loop.

Set `QUERY_CACHE_DIR` on local SSD and cap it with `QUERY_L2_BYTES`. IPC is
uncompressed and memory-mapped. The OS page cache still consumes RAM; decoded
query results, Go overhead and Postgres need headroom too. Ring byte accounting
includes an allowance for decoded Go values and must be checked against RSS.
There is no Redis dependency.

Compose's 64 MiB ring and baseline budgets are bootstrap settings, not measured
capacity claims. L1/L2 are off by default. Replay actual dashboard requests at
declared native rates; record latency, `/v1/stats` source reads, hit ratios,
RSS, disk use and ingest lag before increasing budgets. Benchmark uncompressed
IPC against LZ4 before changing the file format; compression requires decoded
buffers and changes memory accounting.

## Verification

The [isolated integration runner](../../tests/README.md) uses Docker Kafka,
Timescale and Grafana. It pauses the real writer, verifies pending Kafka samples,
resumes persistence and checks paging and Grafana aggregate queries. Unit tests
cover baseline invalidation, cross-pool buckets, Arrow recovery, weighted
overviews and partial query bounds. No hardware or firmware release is needed.
