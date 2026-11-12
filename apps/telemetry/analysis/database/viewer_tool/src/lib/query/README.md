# Artemis query clients

`gateway.ts` authenticates viewer sessions or script tokens and proxies the
shared Go gateway. `client.ts` supplies server-side paged reads for analysis and
exports. `live.ts` adapts gateway streams to existing viewer event shapes;
Artemis routes open no Kafka consumers. Logical dashboard topics use the
existing latest-state JSON feed, while native raw queries preserve fresh field
presence. These paths must not substitute one representation for the other.
Disconnecting a viewer stream immediately stops its heartbeat and upstream
readers.

Set `QUERY_GATEWAY_URL` (default `http://127.0.0.1:8090`) and `QUERY_API_TOKEN` in
the viewer server environment. The public query routes require a signed-in
viewer session or a matching bearer token. Internal analysis calls attach that
token on the server. Service credentials never reach browser bundles.

`POST /api/query` supports raw history, generated pool selection, drive-day/time
bounds, mean/min/max/last buckets, JSON/CSV/Arrow exports and signed continuation.
`GET /api/query/signals`, `/sessions` and `/stream` provide discovery and live
windows. The gateway owns request limits, cursors, source coverage and caches.
See its [API and configuration](../../../../../../stack/kafka/gateway/README.md).

`npm run test:query` runs the gateway's Bazel functionality tests. Docker-backed
verification also exercises the real Kafka/writer/Grafana path. There is one
query implementation; the viewer contains only its clients and adapters.
