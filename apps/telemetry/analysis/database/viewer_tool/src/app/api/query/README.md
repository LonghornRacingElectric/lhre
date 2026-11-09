# Artemis QUERY

`POST /api/query` exposes the Artemis history service in the Node runtime.
The route connects the generated frequency-pool reader, published Artemis
catalog and signed session/time cursors. Other car names return `unsupported_car`.
It uses the existing `/api/*` middleware exclusion; API-key authentication is
outside this v0 endpoint. Database connection URLs remain server-only.

See [query semantics and configuration](../../../lib/query/README.md) for the
request shape, JSON/CSV formats, raw-only scope and pagination guarantees.
