# Artemis Grafana data source

## Setup

Run these commands from the repository root. Docker Desktop (or Docker Engine
with Compose v2) and Bazel must be available. Keep existing database credentials
when using existing volumes.

```bash
test -f .env || cp .env.example .env
openssl rand -hex 32
```

Put the generated value in the root `.env` as `QUERY_API_TOKEN`. Generate a
second value for `QUERY_CURSOR_SECRET`. The gateway, Grafana and viewer server
use the same API token; it is a deployment secret, not a Grafana login. Do not
commit `.env` or put the token in browser code. Merge missing settings into an
existing `.env`; copying the template over it would replace database passwords.

```dotenv
QUERY_API_TOKEN=<first generated value>
QUERY_CURSOR_SECRET=<second generated value>
QUERY_GATEWAY_URL=http://127.0.0.1:8090
QUERY_RING_BYTES=67108864
QUERY_BASELINE_BYTES=67108864
QUERY_L1_BYTES=0
QUERY_L2_BYTES=0
GRAFANA_ROOT_URL=http://localhost:3000/
GRAFANA_SERVE_FROM_SUB_PATH=false
```

Keep `ANALYSIS_PWD` consistent with the database's `analysis` role. Fresh volumes
create that role and its read grants during initialization. Existing volumes
need the [additive schema update](../gateway/README.md#build-and-run) and any
missing role/grants before gateway startup. Changing `.env` does not rotate an
existing database password. The default cache budgets are bootstrap settings;
enable sealed caches after measuring the workload.

```bash
./apps/telemetry/stack/server_devtool.sh build
./apps/telemetry/stack/server_devtool.sh status
```

The devtool passes the root `.env` to Compose for interpolation as well as into
services. No `source .env` is needed to launch the stack. For direct Compose
commands, pass `--env-file "$PWD/.env"` before `-f` and supply the generated
`ARTEMIS_DB_INIT_SQL` path described in the [stack guide](../../README.md).
`bazel run --config=local //apps/telemetry:core_up` also loads the gateway image
and starts the core through the devtool.

For an existing deployment with loaded images, recreate containers after
editing `.env`; a restart does not replace container environment variables:

```bash
./apps/telemetry/stack/server_devtool.sh up
```

## Verify API and Grafana access

Load the root dotenv into this shell for the following host-side requests:

```bash
set -a
source .env
set +a
curl --fail-with-body http://127.0.0.1:8090/healthz
curl --fail-with-body -H "Authorization: Bearer $QUERY_API_TOKEN" \
  http://127.0.0.1:8090/v1/signals
```

Open `http://localhost:3000/` and log in to Grafana. A fresh unconfigured
Grafana uses its initial `admin` login and asks for a new password; an existing
volume keeps its users. `GRAFANA_PWD` is a database-role password, not the
Grafana web login.

Under **Connections → Data sources → Artemis**, use **Save & test**. The
provisioned datasource has UID `artemis`, type `lhre-artemis-datasource`, gateway
URL `http://query_gateway:8090`, and the token from `.env` in its secure
`apiToken` setting. A healthy result says **Artemis gateway connected**. The
signal selector in a panel should then list the generated Artemis channels.

| Caller | Gateway URL |
| --- | --- |
| Grafana container | `http://query_gateway:8090` |
| Viewer or script running on the server host | `http://127.0.0.1:8090` |
| Remote script through an SSH tunnel | `http://127.0.0.1:18090` |

`localhost` inside the Grafana container refers to Grafana itself. The viewer's
`QUERY_GATEWAY_URL` does not change Grafana's separately provisioned URL.

For remote API access, keep the loopback binding and forward it:

```bash
ssh -N -L 18090:127.0.0.1:8090 user@telemetry-server
```

Send the same bearer token to the forwarded port. The [gateway reference](../gateway/README.md#query-interface)
documents `/v1/signals`, `/v1/sessions`, `/v1/query` and `/v1/stream`. For example,
a script can query a fixed historical range across two frequency pools:

```bash
curl --fail-with-body http://127.0.0.1:8090/v1/query \
  -H "Authorization: Bearer $QUERY_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"car":"artemis","signals":["controls.apps1_travel","pack.hv_pack_v"],"t0":"2026-11-14T12:00:00Z","t1":"2026-11-14T12:01:00Z","agg":"max","bucket_ms":1000}'
```

Choose bounds containing actual data. Grafana handles these requests through
its backend; dashboard users do not need the token or a Postgres connection.

## Dashboards and demo data

Use **Board Health - Artemis (Real-time)** in the `artemis` folder. It queries
`board_status.*_last_seen_s` through the gateway. Stat tiles show the latest age;
the plot preserves bucket maxima. Select a recent time window while publishing
live data. No data means there are no observations in the selected range; the
gateway does not synthesize readings merely because a dashboard is installed.

The [board-health replay](../../../analysis/README.md#board-health-demo) provides
continuous native Artemis demo data and an optional board dropout. The separate
`board_health` processor is needed for its state/transition Kafka topics, not
for this dashboard's direct age signals.

The [dashboard directory](../../../analysis/database/dashboards/README.md)
also contains legacy dashboards with SQL or direct Kafka targets. Their query
models and datasource IDs are not Artemis API examples. Selecting the Artemis
datasource alone does not translate them; create Artemis builder queries using
generated signal names.

## Production URL and troubleshooting

Behind the existing `/grafana/` reverse proxy, set:

```dotenv
GRAFANA_ROOT_URL=https://telemetry.lhre.org/grafana/
GRAFANA_SERVE_FROM_SUB_PATH=true
```

Preserve Grafana Live WebSocket forwarding at `/grafana/api/live/ws`. History
uses HTTP; a dashboard can load while live subscriptions fail if the proxy
does not forward WebSocket upgrades.

| Symptom | Check |
| --- | --- |
| `QUERY_API_TOKEN is required` in gateway logs | Set the token in the root `.env`, then recreate the ingest project. |
| Gateway rejects the connection/token or returns 401 | Grafana's secure token and the gateway token must match; recreate both after rotation. |
| Database password authentication failure | Match `ANALYSIS_PWD` to the existing read role and use the devtool's root dotenv loading. |
| Missing relation or permission denied | Apply the generated additive schema and read grants to the existing database. |
| Plugin not found | Rebuild with `server_devtool.sh build ingest`; the Bazel Grafana image includes the Artemis plugin. |
| Datasource not found, old SQL errors or `topicName` targets | Open the Artemis dashboard; legacy dashboards require their original services or converted queries. |
| Redirect to the production host during local use | Set the local `GRAFANA_ROOT_URL` and subpath settings above, then recreate Grafana. |
| History works but live panels fail | Check `/api/live/ws` in the browser and the reverse proxy's WebSocket support. |

```bash
./apps/telemetry/stack/server_devtool.sh logs ingest
```

Avoid posting rendered Compose configuration or environment dumps: they contain
credentials. Do not delete database or Grafana volumes to fix provisioning.

## Implementation

The backend calls only the [query gateway](../gateway/README.md). It owns no Kafka
consumer, SQL connection or result cache. Grafana QueryData handles history and
alerts; Grafana Live carries gateway SSE windows. The editor provides generated
signal search, aggregate selection, bucket width, aliases and template variables.
Alerts use historical queries with Live disabled.
Add separate query rows for minimum and maximum on one panel.
History and live reads resolve the same scalar signals and pool filters before
querying. A new query or subscription refreshes discovery; subsequent pages and
stream windows reuse the catalog while its schema version matches.

Filter signals by subsystem, then search names, units, rates and pools. The
editor lists scalar numeric and Boolean signals; selecting a Boolean changes
an incompatible aggregate to `last`. Backend validation also rejects arrays
and unsupported aggregates. Boolean values plot as 0/1. Auto bucket (`0`) follows
panel width and the slowest selected pool; the editor explains the minimum and
frame metadata reports the effective width. Explicit buckets remain subject to
the gateway's native-rate floor.

Field units come from gateway discovery, cached per datasource instance and
refreshed on schema changes or discovery. Unknown units use a custom suffix
without rescaling observations. Frame metadata includes `bucket_ms`,
`schema_version`, `provisional` and `live`; inspect it in Query inspector.
The provisioned [demo dashboard](../../../analysis/database/dashboards/artemis/README.md)
contains live and historical examples using the same signal selections.

`//apps/telemetry:grafana_load` embeds this plugin and its Linux Go executable in
the pinned Grafana image under `/usr/share/grafana/lhre-plugins`, outside the
persistent data volume. Compose selects that plugin directory. The checked-in
`module.js` is plain SystemJS source,
using Grafana's provided libraries; it needs no separate npm build. The
explicit one-argument datasource constructor is required because Grafana 11
uses constructor arity to distinguish React sources from legacy Angular ones.
The `lhre-artemis-datasource` ID is permitted as an unsigned local plugin by Compose.
Provision its URL as `http://query_gateway:8090` and store the gateway token in
`secureJsonData.apiToken`. Browser code never receives that token.

The Artemis datasource keeps UID `artemis`. Existing SQL panel targets must be
converted to builder queries; changing a datasource type does not translate SQL.
Use scalar numeric channels for graphs. Arrays remain available through the raw
API. Live uses a stable wide frame and Grafana's explicit `StreamingFrameAction.Replace`
buffer policy. Each event replaces the whole window, including corrected final
buckets, removed timestamps and empty windows. Frame metadata preserves gateway
freshness under `custom.live`; a delayed consumer keeps serving available data.
Historical pagination errors require restarting the query; partial results are
discarded. The supported deployment is Grafana 11.3.0.

`bazel test //apps/telemetry/stack/kafka:grafana_query_test` exercises actual SSE
window conversion, corrections, shrinking/empty windows and freshness metadata.
