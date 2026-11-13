# Telemetry stack tests

Connectivity and data-flow tests are `manual`, local targets: they require loaded telemetry
images, Docker, the server credentials and a running core stack. See the
[stack guide](../README.md) for startup.
CI provisions Artemis and shared metadata; the integration suite requires no
Orion database and publishes only Artemis telemetry to Kafka.

```bash
bazel test --config=local //apps/telemetry/stack/tests:integration_tests
```

The lifecycle runner is separate:

```bash
bazel test --config=local //apps/telemetry/stack/tests:stack_integration_test
```

**Use an isolated test stack.** This runner starts services and its exit trap
calls `down --volumes`; it is not a production health check.

Bazel supplies `current_schema_sql` and `new_car_schema_sql` as a data dependency. The runner uses its
absolute runfiles path without invoking nested Bazel builds. When run directly
from a checkout, it builds the SQL target before starting any services. An
explicit existing absolute `ORION_DB_INIT_SQL` or `ARTEMIS_DB_INIT_SQL` selects an admin-provided schema.
Cleanup only resolves the path for Compose interpolation: missing build outputs
or a failed schema build cannot block teardown, and Compose failures are reported.

The Docker-free `//apps/telemetry/stack:server_devtool_test` exercises schema
refresh, sudo forwarding, startup failures and the runner's cleanup path with
fake Bazel/Docker commands. It does not start services or delete real volumes.

Protobuf persistence is MQTT → bridge → `sensor_data` → core `db_writer` →
Postgres. `native_bevo_flow_test` requires the writer service from ingest's Compose
project. Native test producers include `car_type` and `payload_format` headers; malformed records
block the persistence group intentionally. The
[writer guide](../processors/db_writer/README.md) describes batching.


## Artemis frequency-pool correctness

The opt-in `//apps/telemetry/stack/ingest:pool_storage_test` uses
`POOL_TEST_DATABASE_URL`, pointing to an initialized **disposable** Timescale
database. It rolls back its test transactions and covers raw timestamps,
zero/false values through the existing Kafka writer, replay, conflicts, late
session merging, bucket joins, peaks and additive schema application.
Viewer reads now use the gateway; the isolated query integration below verifies
its database and live paths.

The ordinary `data_flow_test` verifies Artemis MQTT forwarding to raw and live
Kafka topics, including rejection of the former-car topic.

For an isolated stack, `MQTT_HOST`/`MQTT_PORT`, `POSTGRES_HOST`/`POSTGRES_PORT`
and `KAFKA_BROKERS` select published test ports. Database credentials come from
`POSTGRES_USER`/`POSTGRES_PASSWORD`; no production database is required.

## Artemis native BEVO integration

`//apps/telemetry/stack/tests:native_bevo_flow_test` starts the actual Rust UDP
acquisition, logger and publisher against the isolated stack configured by the
existing test environment variables. It sends 333 Hz pedal frames, 100 Hz torque
frames, one complete 33-frame cell scan and an incomplete next scan. Assertions
cover fresh-only pool writes, counts, receipt timestamps, missing neighbors,
shared-recording pool CSVs and graceful flush. Reserve local UDP 5005/5006 and
`/tmp/BEVO_cand*.sock`; run only on a development machine without BEVO daemons.
The test creates a temporary identity reconciliation plan for its compiled
Artemis schema. It never opens a physical CAN interface.

`data_flow_test` checks explicit Artemis batches through MQTT and Kafka,
including rejection of the former-car topic. These integration
targets require a running disposable stack; unit tests alone do not prove it.
## Query gateway integration

Run `bash apps/telemetry/stack/tests/run_query_gateway_test.sh` from the repo
root. Set `QUERY_TEST_LOCAL=1` to build locally on macOS. The runner builds and
loads the pinned images, starts an isolated `query-gateway-test` Compose project,
runs `:query_gateway_integration_test`, and removes only that project's
containers and volumes. Docker test results are never reused by this runner.
No secrets or hardware are needed. Ports 35432, 39092,
38090 and 33000 must be free. The failure log is
`/tmp/lhre-query-gateway-test.log`.

The test publishes native Artemis updates into real Kafka, pauses the real
database writer, checks that the API serves pending samples from its ring,
resumes persistence without duplicate observations, follows raw pagination,
and executes a maximum query through Grafana's actual backend datasource.
It also restarts the gateway over retained JSON feed messages and verifies that
new live events are served without replay. An undecodable native record reports
a coverage gap; later records remain consumed, and the real writer recovers
after explicit fixture quarantine. Historical sealing proceeds despite that
unprocessed Kafka tail.
Historical paging checks same-sample fields and continuation into later samples.
It also verifies that a recording change rejects an older continuation with
`409 query_changed`. SSE returns a complete replacement window even with a
one-row public page size and includes consumer freshness metadata.
Eight distinct cold requests verify progress when all four database connections
are occupied.
CI runs this independently of the secret-dependent broader integration suite.

## Trackside workflow benchmark

Manual runs produce machine-specific reports outside the checkout.

```bash
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/run_query_gateway_test.sh \
  --benchmark --minutes 10 --live-seconds 60 --clients 3 \
  --report /tmp/query-gateway-trackside.json
```

The manual `:query_gateway_benchmark` uses the same isolated services and cleanup.
It bulk-loads two synthetic recordings at every generated pool's native rate,
separated by a six-minute pit stop. External GPS uses 10 Hz and board health
1 Hz as explicit fixture assumptions. All generated fields are populated;
arrays have three elements. The signal pattern has known zeroes, peaks and
averages; it models query volume, not vehicle dynamics or worst-case cell arrays.

The workload discovers signals and recordings, selects a drive day, requests
multi-pool overviews, zooms, all-pool snapshots and paged raw exports, and queries
Grafana's actual datasource. It checks session separation, empty pit intervals,
exact raw identities and independent peak/mean results. Three historical readers
run alongside native-rate Kafka traffic and an SSE dashboard. Pausing the real
writer verifies live delivery and duplicate-free catch-up after it resumes.

History is a direct SQLAlchemy fixture load; live data uses the real Kafka
consumer and database writer. The benchmark ages only the fixture's idle-write
metadata to exercise the normal seal worker without waiting five minutes.
It measures unsealed and sealed reads with 256 MiB L1 and 1 GiB L2, separately
from shipped cache defaults. First-read timings mean empty derived caches;
Postgres and OS page caches remain warm after fixture loading.

The JSON report records dataset size, schema, Docker capacity, per-operation
latencies/sample counts/pages/sources, seal size/time, concurrent-read latency,
live freshness, sampled persistence lag, writer catch-up and sampled container memory. This is a local
synthetic acceptance run; repeat on the server with recorded traffic before
claiming production capacity. It is deliberately excluded from presubmit.

## Query design comparison

Compare identical workloads on the same machine; keep the resulting measurements
local rather than treating them as deployment-wide performance guarantees.

```bash
QUERY_TEST_LOCAL=1 bash apps/telemetry/stack/tests/run_query_gateway_test.sh \
  --compare --minutes 10 --repeats 20 --clients 3 \
  --report /tmp/query-gateway-comparison.json
```

The manual `:query_gateway_comparison` compares identical results on one fixture:
direct Timescale through SQLAlchemy, gateway with both caches disabled, L1 only,
Arrow only, and L1 plus Arrow. The direct client selects only requested columns
and computes the same session-separated buckets. It includes native PostgreSQL
transport and conversion to the same observation shape; gateway measurements
include HTTP and JSON decoding. This measures client-visible implementation
cost, not just SQL execution time or a comparison with a different car schema.

Each case records its first read, primes three requests, then measures the same
20 warmed requests and checks every result against the direct-query digest.
The workload includes overviews, short zooms, raw reads, all-pool snapshots and
a three-client mixed-query burst. Direct queries run before and after the cache
variants to expose host drift. Postgres/OS pages remain warm for every variant;
the comparison does not claim disk-cold performance. No live traffic runs during
this comparison, so historical speedup is isolated from ingest load.

Only the gateway is recreated between variants. Its memory resets while a
disposable named volume preserves sealed Arrow files. Environment overrides
change benchmark cache sizes; production defaults remain unchanged. Database
read counts cover telemetry pool fetches, not the gateway's metadata checks.
Reports contain individual timings, p50/p95, exact-result checks and speedup
ratios against both direct Timescale and the uncached gateway.

Add `--grafana-only` to compare Grafana's built-in Postgres datasource against
the combined-cache gateway datasource. Requests alternate between them after
priming; plotted timestamps, names and values must match. The datasource and
generated SQL exist only in the isolated benchmark stack. This variant uses
one sequential client; `--clients` applies to the backend burst comparison.
