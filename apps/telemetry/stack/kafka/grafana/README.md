# Artemis Grafana data source

The backend calls only the [query gateway](../gateway/README.md). It owns no Kafka
consumer, SQL connection or result cache. Grafana QueryData handles history and
alerts; Grafana Live carries gateway SSE windows. The editor provides generated
signal search, aggregate selection, bucket width, aliases and template variables.
Alerts use historical queries with Live disabled.
Add separate query rows for minimum and maximum on one panel.

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
`lhre-artemis-datasource` ID is permitted as an unsigned local plugin by Compose.
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
