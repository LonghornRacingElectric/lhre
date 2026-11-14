# DISCOVER tests

`catalog.test.ts` exercises the historical HTTP handler against temporary catalogs:
car isolation, current/archive selection, rollback, missing versions, unsafe
car/hash paths, and corrupt or mismatched files.
Run `npx vitest run tests/signals/catalog.test.ts` from the viewer directory.
The Artemis gateway discovery path is checked in `tests/query/discovery.test.ts`.
