# Query live windows

`GET /api/query/stream?query=<encoded JSON>` proxies the gateway SSE response.
Events replace bounded windows and expose coverage gaps. Disconnecting closes
the upstream request. See the [query guide](../../../../lib/query/README.md).
