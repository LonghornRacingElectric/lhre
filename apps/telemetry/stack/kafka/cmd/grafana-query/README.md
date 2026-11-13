# Grafana backend executable

`//apps/telemetry/stack/kafka:grafana_query_binary` starts the Artemis datasource
through Grafana's backend plugin protocol. Grafana launches it; do not run it as
a separate server. See [plugin configuration](../../grafana/README.md).
