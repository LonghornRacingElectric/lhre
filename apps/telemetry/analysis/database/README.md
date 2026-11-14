# Telemetry applications and demo sender

Artemis readers use the query gateway. MQTT remains the vehicle's write path:
the demo sender publishes native update batches to `artemis/data`, which ingest
forwards through Kafka to the existing database writer. It uses no database
credentials or packet identifier. Generated protobufs and storage pools come
from the textproto through Bazel.

```bash
bazel run //apps/telemetry/analysis:publish_demo -- --duration 60 --seed 42
```

Each generated frequency pool has its own schedule. External dynamics use 10 Hz
and board health uses 1 Hz in this synthetic sender; `--rate-scale` scales all
rates. `--mqtt-host` and `--mqtt-port` choose the broker. The JSON result includes
the historical time bounds and samples per pool. Values are deterministic for
the same seed and elapsed time; timestamps use the current clock. This tests
transport and application behavior, not CAN acquisition or vehicle physics.

`paho_testing.py` retains `DataTester`: `get_desc`, `get_proto_desc`,
`get_random_data`, `create_row`, `load_csv`, `load_mapping`,
`create_row_from_csv`, `create_proto_message` and `send_proto_rows`.
`get_desc` describes generated Artemis frequency-pool columns; `get_proto_desc`
describes wire groups. Both accept group or pool selection and column removal.
`schema_source="orm"` selects the generated storage column types without a DB
connection. `packet`/`start_packet_id` arguments retained by the Python helpers
are local fixture counters, not transport identity.

The CLI has one `main()` and defaults to the existing row-based workflow:
500,000 rows, the coherent `viewer` profile, and 0.02 seconds between rows.
`generic` keeps random values and defaults to 0.001 seconds. `--seed` controls
random generation. Use `--tables` for subsystem groups or generated pool names.

```bash
bazel run //apps/telemetry/analysis:publish_demo -- --rows 100 --delay 0.02 --profile viewer --schema-source proto --tables controls pack
```

`--car Artemis` remains accepted; the publisher only sends Artemis telemetry.
`create_proto_message` now returns native fresh-update batches, grouped by pool,
with a shared receipt timestamp for a generated row. It never queries a DB to
allocate packet IDs. `--duration` explicitly selects independent native-rate
scheduling through `native_demo.py`, using the same generator profiles.

CSV replay consumes [Artemis logger](../../../BEVO/loggerd/README.md) output:
one `artemis_<recordingStartMs>_<pool>.csv` file or a directory of pool files.
Directory input merges rows by exact integer `sample_time_ns`. Headers include
`time` in milliseconds, `sample_time_ns`, `updated_fields` as a JSON list of
qualified field names, scalar columns such as `controls.apps1_travel`, and
zero-based array columns such as `pack.cells_v[0]`. No seconds-to-milliseconds
conversion or former-car field aliases are applied.

The update mask determines freshness: unobserved blanks stay absent, zero and
false stay values, and a masked array with all slots blank is a fresh empty
array. Indexed values preserve their positions; an interior blank is rejected.
An optional `--mapping` JSON object maps qualified Artemis names to input column
names or lists of columns. Exact timestamps are retained on replay; cycling the
input repeats those same observation identities.

```bash
bazel run //apps/telemetry/analysis:publish_demo -- --csv /absolute/path/to/logger-recording --rows 100 --delay 0.02
```

`--rows`, `--delay`, `--schema-source`, `--tables`, `--csv` and `--mapping` use the
row-based path. `--duration` uses native pool rates and is a separate mode.
`bazel test //apps/telemetry/analysis:paho_testing_test` verifies profile/schema
selection, pacing, logger CSV replay and mappings.

With the stack running:

```bash
export QUERY_GATEWAY_URL=http://127.0.0.1:8090
export QUERY_API_TOKEN=<server-token>
bazel run //apps/telemetry/analysis:validate_live_viewer -- --duration 5
```

The validator publishes data and checks every observation for three live/history
channels. It reports whether history is provisional; a ring-backed response
alone does not prove persistence. The Docker test separately verifies the row
count in every generated pool. The validator raises on gateway gaps or errors.
The isolated Docker test needs no production credentials:

```bash
bash apps/telemetry/stack/tests/run_query_gateway_test.sh
```

It checks MQTT → Kafka → Timescale → gateway → Grafana, including the provisioned
[example dashboard](dashboards/artemis/README.md). The runner removes its test
containers and volumes on exit. For interactive use, `--demo` keeps a synthetic
sender running for 60 seconds; open `http://localhost:33000/d/artemis-demo`
(admin / gateway-test-password) while it runs.

The [viewer](viewer_tool/README.md) defaults to Artemis and reads the same API.
Event/track configuration writes retain their existing control interfaces.
