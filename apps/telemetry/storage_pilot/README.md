# Telemetry storage pilot

A car-free experiment: deterministic signals → batched database writes →
Grafana time-series graphs. It tests the proposed observation schema and compares
PostgreSQL with TimescaleDB. It does not change the production ingestion path.
The [decision memo](decision-memo.md) records the proposal and adoption gates.

## What it demonstrates

One simulated car emits motor speed at **100 Hz**, ambient temperature at
**1 Hz**, and pack voltage at **10 Hz**. Rates are independent and configurable.
One second produces **111 observations**, not 100 copies of all three fields.
Unchanged speed values have new timestamps and sample IDs and are stored again.

Each observation has a session UUID, schema ID, signal ID, per-signal sample ID,
UTC observation time, quality, and an integer or floating-point value. Names,
units and types are versioned metadata. Fault events have a separate table;
this sender emits no faults. `pilot.measurements` represents one car; this is
not a multi-car routing implementation.

The unique key includes the time partition column. Replaying the same identity
and timestamp inserts nothing. A sample's observation timestamp is immutable:
changing it is not a supported correction/replay operation. The signal metadata
and column checks enforce the value's type. Value queries and plots omit
invalid-quality rows; the raw inspection table still shows their quality.

## Start the demonstration

Docker with Compose v2 and Bazel are required. Images are Linux/AMD64, matching
the telemetry stack's current image contract. Commands run from the repo root:

```bash
bazel run --config=local //apps/telemetry/storage_pilot:load_images
docker compose -f apps/telemetry/storage_pilot/docker-compose.yml up -d
docker compose -f apps/telemetry/storage_pilot/docker-compose.yml --profile demo run --rm sender send --seconds 60 --realtime
```

Open [the pilot dashboard](http://localhost:3301/d/telemetry-storage-pilot).
Select PostgreSQL or TimescaleDB and the newest session. The sender writes the
same observations to both narrow databases and prints its session ID and final
verified counts. After 60 seconds expect speed **6,000**, temperature **60**,
and voltage **600** observations in each database. Choose a time range covering
the complete session to see those totals.

Ranges up to five minutes plot actual observations. Longer ranges use
one-second summaries, combining **sums and counts**, not averages of averages.
The PostgreSQL summary is computed on demand; TimescaleDB uses a continuous
aggregate with a 30-second refresh policy and real-time aggregation for recent
data. Summary points are timestamped at bucket starts, so arbitrary range edges
can differ from an exact raw-sample selection. These are sample means, not
time-weighted means, last values, or midpoint-nearest observations.

The pilot uses its own network, volumes and ports, without reading the normal
telemetry `.env`. Database ports are 55432 (PostgreSQL), 55433 (TimescaleDB),
and 55434 (snapshot baseline), all bound to localhost. Grafana uses a read-only
database role and localhost port 3301. Its anonymous UI is a viewer. Fixed
`pilot_local` / `pilot_readonly` passwords belong only to these local test
instances. No database data is deleted automatically.

Stop the demonstration with `docker compose -f
apps/telemetry/storage_pilot/docker-compose.yml down`; volumes are preserved.

## Repeatable evaluation

Three isolated instances run the same generated source timeline:

| Case | Storage | Purpose |
| --- | --- | --- |
| Snapshot PostgreSQL | Existing Orion `packet`, `controls`, `thermal`, and `pack` tables | Current schema baseline |
| Narrow PostgreSQL | One row per actual observation | Measure the schema change |
| Narrow TimescaleDB | Identical narrow schema, time chunks, continuous aggregate | Measure extension features separately |

The snapshot case loads the repository's existing Orion DDL and populates only
`controls.motor_speed`, `thermal.ambient_temp`, and `pack.hv_pack_v`. It emits
cached snapshots at the fastest configured signal rate. This is a controlled
three-signal workload, not a full-car production benchmark or the current
uplink rate. Slow values are deliberately repeated in that baseline and its
averages have snapshot semantics. Its cached field values must never be counted
as independently observed samples. Arbitrary source rates need not align to
snapshot ticks, another reason snapshots do not preserve observation timing.

Both database images contain PostgreSQL **17.7**. The TimescaleDB image is
**2.24.0-pg17**; both are pinned by digest in `MODULE.bazel`. They have matching
Compose CPU/memory limits, shared buffers, connection limits, and durability
settings. `fsync`, `full_page_writes`, and `synchronous_commit` must be on.
The ordinary PostgreSQL cases do not preload the Timescale extension.

The benchmark refuses nonempty measurement/packet tables. Start a fresh Compose
project, after stopping the demo to release its ports:

```bash
docker compose -f apps/telemetry/storage_pilot/docker-compose.yml down
docker compose -p lhre-storage-benchmark-1 -f apps/telemetry/storage_pilot/docker-compose.yml up -d
docker compose -p lhre-storage-benchmark-1 -f apps/telemetry/storage_pilot/docker-compose.yml --profile benchmark run --rm -T benchmark benchmark --seconds 300 --repeats 100 --columnstore > /tmp/telemetry-pilot-results.json
```

Use a new project suffix for another fresh run. Stopping a project does not
empty its volumes. Automatic aggregate refresh is disabled for the benchmark;
the explicit refresh is timed separately. No retention policy runs. Optional
`--columnstore` converts Timescale chunks only after the three base cases have
been measured, then reports sizes and query times again.

The JSON report records:

- Dataset duration, rates, seed, start time, session and schema identities.
- Ingest wall time and time spent adapting/batching/writing/committing records.
  Snapshot records and SQL rows are labeled separately from observations.
- Raw-range, latest-value and summary query p95/p99 client round-trip latency,
  including fetched results. Three warmup iterations are excluded; query windows,
  signals and order are repeatable. Record the number of repetitions when
  interpreting percentiles, especially p99.
- Raw table, index, total raw, summary and total database sizes. Hypertable
  sizes include chunks, not just the parent table. Database size includes
  catalogs and metadata; raw sizes do not include every operational cost.
- WAL bytes generated during ingest, including any instance background activity.
  This is WAL generated, not retained WAL-directory size.
- Aggregate refresh time, exact per-signal counts/IDs, and an idempotent replay
  check. Raw storage growth extrapolated to a day is labeled a **projection**.

Use a quiet machine, identical resources, fresh data and several runs before
drawing conclusions. Record the hardware and Docker/host environment alongside
the JSON. Charts are correctness/inspection aids; they do not establish speed
or storage savings. No benchmark result or adoption recommendation is checked
in as if it had already been measured.

## Host CLI and tests

The CLI also runs outside Docker. Override `PILOT_POSTGRES_DSN`,
`PILOT_TIMESCALE_DSN`, and `PILOT_SNAPSHOT_DSN` for different test endpoints.
It never reads the production telemetry `.env`.

```bash
# Inspect generated identities, values and expected counts without a database.
bazel run --config=local //apps/telemetry/storage_pilot:pilot_binary -- preview --seconds 2

# Populate both running narrow databases without real-time pacing.
bazel run --config=local //apps/telemetry/storage_pilot:pilot_binary -- send --seconds 60

# Service-free workload tests.
bazel test --config=local //apps/telemetry/storage_pilot:workload_test
bazel test --config=local //apps/telemetry/storage_pilot:report_test

# Real database integration tests; require both isolated narrow instances.
bazel test --config=local //apps/telemetry/storage_pilot:storage_integration_test --test_env=PILOT_POSTGRES_DSN='host=127.0.0.1 port=55432 dbname=pilot user=pilot password=pilot_local' --test_env=PILOT_TIMESCALE_DSN='host=127.0.0.1 port=55433 dbname=pilot user=pilot password=pilot_local' --test_output=errors
```

`send` and `benchmark` accept `--speed-hz`, `--temperature-hz`, `--voltage-hz`,
`--seconds`, `--seed`, `--start`, and `--batch-size`. Rates are integers from 1
to 1,000 Hz. Sessions use half-open time intervals; a sample exactly at the end
belongs to the next interval. `--start` must include a timezone. Direct sends
without `--realtime` default to the preceding interval so the data is visible
in the dashboard immediately. `send --target postgres|timescale|both` selects
the destination. `benchmark` also accepts `--repeats` and `--columnstore`.

The integration target verifies real inserts, duplicate replay, retained
unchanged values, type constraints, invalid quality, late data followed by
aggregate refresh, fractional session boundaries, weighted summaries and
separate fault storage. It adds uniquely named test
sessions and leaves them for inspection; run benchmarks in a fresh project.

## Files and next steps

`workload.py` owns sample generation. `storage.py` owns SQL and transaction
boundaries. `pilot.py` supplies the sender and benchmark commands. The SQL files
define metadata, typed observations, and backend-specific summaries.
`dashboard.json` and the Grafana provisioning YAML define the visualizer.
`BUILD.bazel` exposes binaries, tests, OCI images, loaders and smoke targets.

Real BEVO integration, Kafka persistence/outage recovery, a production migration,
backup/restore timing, observed aggregate lag, and retention/archive policy
tests remain later gates. BEVO snapshots cannot supply missing per-signal
observation timing. Automatic raw retention must be coordinated with aggregate
refresh windows before it is enabled; see the decision memo.
