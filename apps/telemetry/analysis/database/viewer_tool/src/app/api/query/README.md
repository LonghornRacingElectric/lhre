# QUERY

`POST /api/query` exposes the historical query service in the Node runtime.
It uses the existing `/api/*` middleware exclusion; API-key authentication is
not part of this v0 endpoint. PostgreSQL connection URLs remain server-only.

See [query semantics and configuration](../../../lib/query/README.md) for the
request shape, JSON/CSV formats, downsampling, and cursor guarantees. The route
only wires the service to the PostgreSQL source and published catalog reader.
