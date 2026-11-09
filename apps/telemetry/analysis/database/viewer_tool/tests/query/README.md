# QUERY tests

```bash
npm run test:query
```

Unit and HTTP checks cover Artemis-only validation, session/time cursors, catalog
pinning, byte limits, CSV, pool mapping and mismatched sample rates. Uplink rates
must not redirect a field into a different native-frequency pool. Raw-only
requests reject downsampling explicitly.

For database verification, build `//lib/codegen/telemetry:server_schema`, create a
fresh disposable database whose name starts with `telemetry_query_test`, and
initialize it with the generated `artemis_db_init.sql`. From this directory run:

```bash
ARTEMIS_QUERY_TEST_DATABASE_URL=postgresql://localhost:5432/telemetry_query_test_artemis npm run test:query
```

The integration suite is skipped without the URL. It checks the database name
and uses Prisma for fixture writes into actual generated Timescale tables;
queries use the production read-only adapter and generated Artemis catalog.
Never point it at a recording database. Fixture rows are removed afterwards.

Checks cover unequal rates without multiplied rows, half-open ranges,
zero/false values, precise coordinates, empty versus NULL arrays, same-time
session ties, continuation between fields, multiple database batches and full
HTTP pagination without any packet table or packet identifier.
