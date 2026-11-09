# QUERY tests

From the viewer directory:

```bash
npm run test:query
```

Unit/HTTP tests cover validation, signed cursors, catalog pinning, timestamp
ties, within-packet continuation, byte limits, CSV quoting, and parameterized PostgreSQL selection. Downsampling requests must fail explicitly; this version is raw-only.

For real database verification, create a fresh disposable PostgreSQL database whose
name starts with `telemetry_query_test`, then run:

```bash
QUERY_TEST_DATABASE_URL=postgresql://localhost:5432/telemetry_query_test \
  npm run test:query
```

The integration suite is skipped without that URL. It checks the database name
before creating minimal packet/group tables and inserting fixtures. Never point
it at a useful database. QUERY reads use the production `ReadOnlyDatabase`
helper. No Prisma client or schema generation is needed.

Checks cover half-open ranges, numeric packet ordering including IDs larger
than JavaScript's safe integer range, arrays/booleans, missing groups, pages
ending within one packet, and the HTTP response against actual read-only PostgreSQL queries.
