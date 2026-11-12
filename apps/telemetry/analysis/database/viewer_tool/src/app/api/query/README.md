# Artemis QUERY

`POST /api/query` proxies the shared Artemis gateway in the Node runtime.
A viewer session or query API token is required. The server attaches its
`QUERY_API_TOKEN` to `QUERY_GATEWAY_URL`; browser code sees no database or
service credentials. Signal/session discovery and SSE windows are sibling
routes under this directory. Other car names return `unsupported_car`.

See [query semantics and configuration](../../../lib/query/README.md) for the
request shape, export formats, aggregate functions and pagination guarantees.
