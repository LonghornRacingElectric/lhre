# Historical telemetry queries

`POST /api/query` reads Artemis history from its generated frequency pools.
`service.ts` owns validation, catalog selection, pagination and response limits;
`artemis.ts` maps approved signals to physical pools using the generated storage
contract. Pool placement follows native CAN frequency, independent of uplink
limits. Orion remains a legacy reference with its existing telemetry readers;
this gateway rejects other car names before catalog or database access.

Raw reads preserve each pool's received timestamps. Each selected pool supplies
ordered observations, merged by timestamp, recording session, fine receipt timestamp and signal name.
There are no cross-pool raw joins and no packet table or packet identifier.
Existing bucketed Artemis interfaces continue to serve aligned analysis;
adding aggregation to this endpoint is separate from raw history retrieval.

The reader uses the existing `ReadOnlyDatabase` helper for read-only transactions,
connection pooling and the 30-second statement timeout. Identifiers are validated
against the generated contract and all bounds are parameters. This small SELECT
boundary preserves SQL NULL arrays: Prisma turns those into empty arrays, which
would incorrectly present an unrecorded sensor as a recorded empty sample.

Zod validates requests/cursors and ISO timestamps. `negotiator`, `raw-body`, and
`csv-stringify` handle HTTP format selection, bounded bodies, and CSV output.
A future Kafka source implements the same `read(plan, after, limit)` interface;
HTTP and pagination do not depend on PostgreSQL.

## Connections

QUERY uses the same environment variables as the existing MoTeC/history routes:

```env
ARTEMIS_DB_HOST=localhost
ARTEMIS_DB_PORT=5432
ARTEMIS_DB_NAME=artemis
ARTEMIS_DB_USER=analysis
ARTEMIS_DB_PASSWORD=replace-me
QUERY_CURSOR_SECRET=replace-with-a-random-secret-shared-by-all-viewer-processes
```

Set `ARTEMIS_DATABASE_URL` or the `ARTEMIS_DB_*` settings above. Existing shared
host/credential defaults still apply. A published Artemis catalog is required;
Orion catalogs are never substituted. Missing pools or columns return 503.
No database migration or cross-car reconciliation runs as part of a query.

Each page checks a 30-second budget between pool batches; an in-flight statement
has the shared helper's own 30-second timeout. This is not a strict 30-second
deadline for the entire HTTP request.

Set the same `QUERY_CURSOR_SECRET` on all deployed viewer processes. Without
one, a process-local random key is used, so tokens expire on restart or fail
when sent to a different process. No credentials are returned or logged.

## Request and response

```json
{
  "car": "artemis",
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
    {"ts": "2026-11-08T12:00:00.000Z", "sample_time_ns": "1794139200000000000", "signal": "pack.hv_pack_v", "value": 420}
  ],
  "next_page_token": null
}
```

Booleans and arrays are preserved. Null/missing scalar values and absent groups
are omitted; empty arrays are returned. PostgreSQL bigint/numeric values remain
decimal strings to preserve precision; byte values use base64. Nonfinite floating values use string labels.
Data is fresh observation history with the original BEVO receipt timestamp.
`sample_time_ns` is a decimal string; JavaScript numbers cannot represent it exactly.

`Accept: text/csv` returns `ts,sample_time_ns,signal,value` with CSV quoting and CRLF endings;
array cells contain JSON. CSV continuation is in `X-Next-Page-Token`, absent at
the end. Both formats carry `X-Schema-Version`. Unsupported formats return 406;
errors use `{error: {code, message}}`. Responses are not cached. The v0 route
uses the existing `/api/*` middleware exclusion; API-key auth is future work.

## Pagination and limits

Results are ordered by timestamp, numeric `session_start_ms`, numeric `sample_time_ns`, then qualified
signal name. Database batches use time/session/fine-timestamp predicates, never OFFSET. A page
can end between signals at the same session/time; continuation includes that
sample and skips only observations already delivered. The signed cursor carries
a session key and never a packet identifier. Cursor format 3 rejects older tokens lacking fine receipt identity.

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
them; there is no SQL-side size projection. Pool rows are fetched in batches of
32. A page may scan at most 10000 rows across the selected pools to find requested observations; an
excessively sparse request returns 413 `query_scan_limit` so callers can narrow
the range. Database unavailability and timeouts return 503.

Pagination is not an immutable database snapshot across HTTP requests. Late
inserts before the cursor, backfills, updates, and deletions can affect later
pages. Use completed historical ranges for reproducible reads.

See the [query test guide](../../../tests/query/README.md) for verification.
