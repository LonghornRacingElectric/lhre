# TimescaleDB evaluation — proposed pilot

**Status:** Pilot proposed; production migration and scope remain undecided.

**Goal:** Decide whether TimescaleDB improves telemetry storage enough to adopt,
and which workloads should use it. The [pilot](README.md) starts with simulated
signals → database → graphs. Real vehicle integration and Kafka recovery are
later work.

## Separate the decisions

| Proposed change | Purpose |
| --- | --- |
| Narrow measurement schema | One row per actual observed sample |
| TimescaleDB extension | Time chunks, columnstore, summaries and retention |
| Kafka-backed persistence | Buffered delivery and replay into storage |

TimescaleDB extends PostgreSQL. Benchmark the existing snapshot schema on
PostgreSQL, the narrow schema on PostgreSQL, and that same narrow schema on
TimescaleDB. Schema improvements are not evidence of extension benefits.

## Candidate design, not ratified

One narrow mixed-rate hypertable per car, with observation time, signal ID,
typed value, quality, session ID, schema ID, and stable sample ID. Versioned
metadata supplies names, units and interpretation. Fault transitions remain
separate records. An unchanged value can be a new observation; value comparison
cannot identify samples. Cached values must not become artificial samples.

The initial index is `(signal_id, ts DESC)`. Latest-value lookup uses that
index; aggregate `last()` answers a different question. Continuous aggregates
can supply common plotting resolutions. Sample averages, final observations,
and midpoint-nearest observations are different operations. Combine sample
means with counts/sums; midpoint-nearest generally requires source samples.

BEVO currently timestamps snapshots containing accumulated values. Expanding
those snapshots cannot recover original observation times. Vehicle integration
requires per-signal timestamps and identities, or explicit updated-signal lists
with equivalent information.

## Extension features and operational work

| Feature | Pilot question | Cost |
| --- | --- | --- |
| Hypertables | Does time chunking help this workload? | Chunk sizing, extension management |
| Columnstore | What storage/query changes occur on older chunks? | Layout tuning, conversion work |
| Continuous aggregates | Which resolutions help and how late are results? | Storage, refresh cost, late data |
| Retention | Can expired raw data be removed safely? | Coordination with summaries/archives |

Pin a PostgreSQL-compatible extension version and use that version's supported
APIs. Narrow rows add per-observation overhead; storage savings are unproven.
Refreshing a time interval after its raw samples are deleted can remove its
aggregate results. Define raw retention, refresh windows, and archives together.
The initial pilot does not delete raw data automatically.

## Persistence and recovery gates

Today's path writes MQTT telemetry to PostgreSQL and separately forwards toward
Kafka. A future path is MQTT → Kafka → batching storage consumer → database.
Retention, producer delivery, retries and consumer offsets determine whether
that path survives an outage; Kafka alone does not establish recovery.

Commit database writes durably before committing Kafka offsets. Replay can
repeat records, so writes must be idempotent and preserve source timestamps.
Hypertable unique constraints must include every partitioning column. The
current MQTT SQL worker uses `synchronous_commit = off`; copying that setting
could let offsets advance before database writes survive a crash.

The initial sender uses a direct durable batching path. It does not implement
or claim Kafka outage recovery. Production approval requires a successful trial
migration, backup/restore verification, and recovery timing against agreed
limits.

## Evidence and decision

Measure actual observations/s, bytes/day, insertion throughput, query p95/p99,
table/index/WAL sizes, summary storage and lag, late-data behavior, replay
correctness, and migration/restore time. Record hardware, versions, resources,
durability, dataset and query definitions. Graphs alone are not a benchmark.

Operational review must cover upgrades, policy monitoring, backup compatibility,
migration space, query changes, and ownership. Set success thresholds before
testing; do not infer approval from a successful visualization demo.

| Decision item | State |
| --- | --- |
| Representative benchmark results | TODO |
| Recommendation and adoption scope | TODO |
| Cost and operations assessment | TODO |
| Pilot owner | TODO |
| Migration/restore and Kafka recovery exercises | Later gate, not demonstrated by this sender |

## Starting points for later integration

- [Ingest](../stack/ingest/README.md): database deployment, existing SQL writer,
  schema initialization. `partition_manager.py` records activity intervals; it
  does not create physical partitions or Timescale chunks.
- [Kafka processor template](../stack/processors/kafka_base/README.md): starting
  pattern for a future persistence consumer.
- [BEVO schema](../../BEVO/schema/README.md): upstream signal timing/identity.
- [Viewer](../analysis/database/viewer_tool/README.md): historical query changes.

Primary references: [hypertables](https://www.tigerdata.com/docs/reference/timescaledb/hypertables/create_hypertable),
[columnstore conversion](https://www.tigerdata.com/docs/reference/timescaledb/hypercore/convert_to_columnstore),
[aggregate refresh and retention](https://www.tigerdata.com/docs/build/continuous-aggregates/refresh-policies),
and [Grafana PostgreSQL queries](https://grafana.com/docs/grafana/latest/datasources/postgres/query-editor/).
