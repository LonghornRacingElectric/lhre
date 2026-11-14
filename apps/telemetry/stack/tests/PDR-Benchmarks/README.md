# PDR benchmarks

Manual measurements of sustained MQTT ingestion and historical reads. The
[existing integration suite](../README.md) remains the correctness check for
the production stack. This suite records observed outcomes, including drops,
backlog and process exits; it does not assume either pipeline wins.

## Run

From the repository root with Docker running:

```bash
# Six real-time trials: each pipeline at 10, 100 and 333 Hz for 30 minutes.
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/PDR-Benchmarks/run.sh ingest \
  --legacy-root /Users/dumast/LHR/lhre-2026 --report /tmp/pdr-ingest.json

# Native-rate 24-hour history, random and long-range queries, 50 readers.
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/PDR-Benchmarks/run.sh reads \
  --report /tmp/pdr-reads.json

# Separate Kafka-to-query/database freshness probes.
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/PDR-Benchmarks/run.sh recent \
  --windows 10 --clients 1 --aggregates max --report /tmp/pdr-freshness.json

bazel run --config=local //apps/telemetry/stack/tests:pdr_report -- \
  /tmp/pdr-ingest.json /tmp/pdr-reads.json /tmp/pdr-freshness.json \
  --output /tmp/PDR-results.md --graphs-dir /tmp/pdr-graphs
```

To run all three sequentially in a durable job, set `QUERY_TEST_LOCAL=1` and
launch `run_all.py --legacy-root <checkout> --output-dir <new report directory>`
with Python. Its `status.json` records the PID, current phase, logs and exit
codes. It runs reports/graphs between phases even when a measurement fails.
Ingestion alone takes at least three hours. Loading, sealing and querying the
large history can take additional hours. Keep the computer and Docker running.
The optional `--history-hours` argument selects a different read-fixture span.

A fresh isolated Compose project is required. All database and Arrow volumes
are deleted on exit; reports and logs survive. Ports 35432, 35433, 39092, 38090
and 31883 must be free. The runner refuses an existing `pdr-benchmarks` project.
Do not run concurrently with the older query harness, which shares some ports.
A unique `/tmp/lhre-pdr-<mode>-<timestamp>-<pid>.log` captures container logs.
`PDR_LOG_FILE` overrides it. `PDR_STATE_DIR` selects queue-telemetry files;
otherwise the runner creates a unique temporary directory and preserves it.
Docker images remain loaded; no ordinary telemetry project is selected.

## Ingestion

Compare these actual code paths, one at a time:

- Legacy: MQTT subscriber with internal database workers and a separate Kafka
  publication path, from the supplied `lhre-2026` checkout.
- Current: MQTT subscriber, Go bridge, Kafka, separate database writer.

Defaults are **1,800 seconds at 10, 100 and 333 updates/s**, one controls-pool
update per MQTT message at QoS 1. Native-mixed and multiplier/capacity trials
are removed. Both encodings carry the same logical fields and values. The
rate is the input rate of this pool, not the combined whole-car rate.

The new image `//apps/telemetry/stack/tests:pdr_probe_load` runs the actual
current ingest entry point through a benchmark-only observer. The same observer
wraps the original legacy entry point. It counts callback receipts,
disconnections and failed nonblocking queue puts, and publishes queue depth,
capacity and process peak RSS once per second. Original queue capacities and
worker/batching behavior are retained. The legacy checkout is read-only.
Original source hashes, image identities and any explicitly enabled baseline
adaptations are retained in the JSON metadata.

The host observer seeks forward from the last sample's millisecond/identity
so it does not repeatedly scan the entire growing trial. It measures newly
visible stored rows, verifies their values,
and tracks identities pending persistence. Ten-second latency summaries are
assigned by **send time**; late commits update the original send interval.
Intervals with no observed commits have null percentiles, never zero latency.
The complete trial also has a bounded reservoir latency summary. Individual
pending entries retain identity/index/time rather than full field dictionaries.

Reports include per-second backlog and persisted counts, queue telemetry,
publisher acknowledgements, available MQTT `$SYS` diagnostics, scheduling lag,
process exits and OOM state. Broker diagnostics remain absent when unavailable;
broker stored-message counts are not interpreted as a per-client queue size.
The private publisher outgoing count is diagnostic instrumentation, not a public
paho API contract. The Python queue samples are distinct from broker buffers.

An `.active.json` sidecar is checkpointed every ten seconds while a trial runs.
The main report is checkpointed after each completed case. Drain observation
continues after publishing stops (60-second timeout by default). A process exit,
value mismatch or publisher failure is retained as an error. Queue drops remain
visible while publishing continues. The one-million pending-observation safety
limit exceeds an entire 30-minute 333 Hz trial and prevents unbounded host RAM
use on longer custom trials. The producer records scheduling lag so a slow
load generator cannot be mistaken for a pipeline limit.

Charts overlay pipelines at each frequency:

1. Send-to-database visibility p50/p95 in ten-second send intervals.
2. Sent samples not yet observed in the database.
3. Observed persistence rate versus offered rate, with the drain boundary shown.
4. Actual ingest queues, with overflow and process/disconnect events marked.

A passing correctness result means all samples arrived correctly, not that the
writer kept up in real time. The existing sustained heuristic requires no
errors/loss/duplicates, producer lag below one second, and end-of-publication
backlog below 500 plus two seconds of offered updates. Charts retain the full
trajectory rather than relying on that single label.

This compares whole pipelines, including their schemas, protobufs, batching and
acknowledgements. Legacy batching is 40 row jobs/five idle seconds; current
batching is up to 500 packets/250 ms. Queue probing and DB polling add measurement
overhead to both paths. It does not attribute every difference solely to Kafka.

## Historical reads

The default fixture spans **24 hours** at the generated native frequencies.
Server-side `generate_series` inserts accelerate loading without waiting for
wall-clock time. The two databases receive identical timestamps, values and
recording identity. Scalars follow a known periodic pattern, booleans alternate,
and array fixtures have three elements.

By default only the five dashboard signals and their storage pools are
populated. Unselected fields remain NULL; the queried signals' native rates
and exact sample identities are preserved. `--fixture-fields all` populates
all fields of those pools. This gives a large
relevant working set without multiplying disk use for unrelated tables.
`--fixture-pools all --fixture-fields all` populates the complete catalog. The report lists populated
pools, row counts, sizes, native rates, versions and DB settings. Empty schema
tables remain present. `--history-hours 168` supports seven days on a suitably
sized host. There is no silent automatic expansion; check disk space first.
Loading stops if host or Docker-VM free space falls below 8 GiB. Arrow
preparation also stops below the Docker-space reserve. Host and Docker-VM disk
capacities can differ; clearing host files does not expand the VM disk limit.

Workloads:

- Three reproducibly random positions each for 10-second and 600-second windows:
  raw samples, means and maxima.
- One-hour windows at the beginning, middle and end: means and maxima.
- Six-hour and 24-hour chart-sized overviews: means and maxima.

Each case runs a first single request, 50 synchronized cold readers, 50 warm
readers of that same request, and another RAM-cold burst spread over different
windows. The full-history overview cannot have distinct windows in a one-day
fixture; that phase is explicitly not applicable. Unique-window counts are
reported. Seed 42 controls random positions by default.

Backends are indexed plain Postgres, Timescale hypertables, and the gateway with
historical caches off, 256 MiB RAM only, 16 GiB Arrow only, or both. Adjust
`--disk-cache-bytes` to the host. Required ring/baseline budgets stay at their
existing 64 MiB settings. RAM is reset independently before first/cold/distinct
phases. Disk variants wait for sealed Arrow files; preparation time is outside
read latency. Database and OS caches remain enabled, so cold means gateway RAM.

An independent arithmetic oracle verifies the generated raw identities and
aggregates. It does not prime a database reference query or skip the other
backends when plain Postgres times out. Raw results are completely consumed
with bounded-memory streaming and public gateway pagination. Numeric means are
compared after rounding to eight decimal places. Errors/timeouts remain failures.

Direct SQL and gateway query executions retain 30-second server deadlines.
Client latency includes fetching all pages, conversion and verification. Both
use four-connection database pools. Python/psycopg2 versus HTTP/JSON is part of
the measured client path; this is not a pure language-speed comparison.

Exact query `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans are captured outside
the timed trials for each direct backend. Plans and actual chunk counts show
whether Timescale avoids unrelated chunks; no speedup is assumed. The generated
schema retains daily chunks. A 24-hour fixture crosses UTC midnight and usually
has two chunks per populated pool, not hundreds. More days may be needed to
study extensive pruning. No compression policy or continuous aggregate is added.

## Development checks

```bash
bazel test --config=local //apps/telemetry/stack/tests:pdr_metrics_test \
  //apps/telemetry/stack/tests:pdr_reads_test
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/PDR-Benchmarks/run.sh ingest \
  --legacy-root /Users/dumast/LHR/lhre-2026 --durations 20 --rates 333 \
  --report /tmp/pdr-ingest-smoke.json
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/PDR-Benchmarks/run.sh reads \
  --history-hours .02 --windows 10 --random-windows 1 --clients 3 \
  --report /tmp/pdr-reads-smoke.json
```

All workload images run amd64, including plain Postgres. An ARM Mac emulates
these paths and is useful for harness validation; it does not establish server
capacity. Repeat on the intended server and compare equivalent results before
using the numbers as deployment claims.
