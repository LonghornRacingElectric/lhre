# Artemis Grafana data source

The backend calls only the [query gateway](../gateway/README.md). It owns no Kafka
consumer, SQL connection or result cache. Grafana QueryData handles history and
alerts; Grafana Live carries gateway SSE windows. The editor provides generated
signal search, aggregate selection, bucket width, aliases and template variables.
Alerts use historical queries with Live disabled.
Add separate query rows for minimum and maximum on one panel.

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
API. Live uses a stable wide frame and replaces duplicate bucket timestamps as
the final bucket changes. The supported deployment is Grafana 11.3.0.
