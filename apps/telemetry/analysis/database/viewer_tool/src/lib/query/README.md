# Historical telemetry queries

`POST /api/query` validates names against the published car catalog and reads
raw observations through a `TelemetrySource`. `service.ts` owns validation,
catalog selection, pagination, and response limits. `http.ts` handles JSON/CSV.
`postgres.ts` builds parameterized SELECTs for catalog-approved group fields.
It uses the existing MoTeC `getSettings(car)` and `ReadOnlyDatabase` helpers,
sharing their per-car configuration, connection pools, read-only transactions,
and 30-second statement timeout. SQL selection and pagination live in QUERY;
the existing MoTeC code is unchanged.

Zod validates requests/cursors and ISO timestamps. `negotiator`, `raw-body`, and
`csv-stringify` handle HTTP format selection, bounded bodies, and CSV output.
A future Kafka source implements the same `read(plan, after, limit)` interface;
HTTP and pagination do not depend on PostgreSQL.

## Connections

QUERY uses the same environment variables as the existing MoTeC/history routes:

```env
ORION_DB_HOST=localhost
ORION_DB_PORT=5432
ORION_DB_NAME=orion
ORION_DB_USER=analysis
ORION_DB_PASSWORD=replace-me
QUERY_CURSOR_SECRET=replace-with-a-random-secret-shared-by-all-viewer-processes
```

This packet-based adapter supports Orion. Artemis's separate Timescale database
continues to use its existing time-bucket interfaces. A query for Artemis with a
published catalog returns 503 `unsupported_storage` before opening a database;
without a catalog, discovery reports the missing catalog first. An Artemis
query adapter and cross-layout reconciliation are deferred. Retired cars have
no database configuration. `ORION_DB_CONNECT_TIMEOUT` defaults to 5 seconds;
the shared pool allows four connections per database identity.

Group tables must join to `packet` through `packet_id`, with one row per
packet. Fields are selected from the published catalog, with validated and
quoted identifiers; values and cursor bounds use SQL parameters. Missing
tables/columns return 503. No generated Prisma client is needed for QUERY.

Each page checks a 30-second budget between packet batches; an in-flight
statement has the shared helper's own 30-second timeout. This is not a strict
30-second deadline for the entire HTTP request.

Set the same `QUERY_CURSOR_SECRET` on all deployed viewer processes. Without
one, a process-local random key is used, so tokens expire on restart or fail
when sent to a different process. No credentials are returned or logged.

## Request and response

```json
{
  "car": "orion",
  "signals": ["pack.hv_pack_v", "dynamics.gps"],
  "t0": "2026-11-08T12:00:00Z",
  "t1": "2026-11-08T12:01:00Z",
  "rate": null,
  "agg": "raw",
  "page_token": null,
  "page_size": 1000
}
```

Only raw reads are supported. Omit `rate` and `agg`, or use `rate: null` and
`agg: "raw"`. Requests for a rate or mean/min/max return 400
`unsupported_downsampling`; they never silently return raw data instead.
Time buckets, continuous aggregates, and Kafka routing for this endpoint are
future work; the separate Artemis bucket read interfaces remain available.

Time ranges are half-open (`t0 <= timestamp < t1`) with `t0 < t1`; timestamps
require a timezone and at most millisecond precision. Select 1–50 unique,
qualified signal names. Unknown cars/signals return 404; invalid requests
return 400. The request body is limited to 256 KiB.

JSON is the default (`Accept: application/json`):

```json
{
  "schema_version": "<catalog-content-hash>",
  "data": [
    {"ts": "2026-11-08T12:00:00.000Z", "signal": "pack.hv_pack_v", "value": 420}
  ],
  "next_page_token": null
}
```

Booleans and arrays are preserved. Null/missing scalar values and absent groups
are omitted; empty arrays are returned. PostgreSQL bigint/numeric values remain
decimal strings to preserve precision; byte values use base64. Nonfinite floating values use string labels.
Data is stored snapshot history, not reconstructed acquisition timestamps.

`Accept: text/csv` returns `ts,signal,value` with CSV quoting and CRLF endings;
array cells contain JSON. CSV continuation is in `X-Next-Page-Token`, absent at
the end. Both formats carry `X-Schema-Version`. Unsupported formats return 406;
errors use `{error: {code, message}}`. Responses are not cached. The v0 route
uses the existing `/api/*` middleware exclusion; API-key auth is future work.

## Pagination and limits

Results are ordered by timestamp, numeric packet ID, then qualified signal
name. SQL reads packets in time/ID order; the adapter expands selected
fields into observations. It includes the cursor's packet again when needed,
then skips already-delivered signals, so a page can end within a packet.
Batch continuation uses strict time/ID predicates, never OFFSET.

Tokens are HMAC signed, expire after 24 hours, and bind the normalized request
and catalog version. Keep the same filters and page size. Activating a new
catalog cannot change the selected definitions during pagination. Changing
JSON/CSV format is allowed. This does not stitch historical database layouts.

Default page size is 1000, requested sizes are capped at 10000, and each source
read returns at most 1001 observations including a look-ahead. Page data is
bounded using the complete serialized JSON response, including the actual
cursor and metadata, with a 2 MiB limit. No fixed metadata allowance is used;
short pages can still have a continuation token. CSV uses the same page of rows.
Individual values over 64 KiB return 413. Values are checked after PostgreSQL returns
them; there is no SQL-side size projection. Packets are fetched in batches of
32. A page may scan at most 10000 packets to find requested observations; an
excessively sparse request returns 413 `query_scan_limit` so callers can narrow
the range. Database unavailability and timeouts return 503.

Pagination is not an immutable database snapshot across HTTP requests. Late
inserts before the cursor, backfills, updates, and deletions can affect later
pages. Use completed historical ranges for reproducible reads.

See the [query test guide](../../../tests/query/README.md) for verification.
