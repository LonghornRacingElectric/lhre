# DISCOVER tests

`catalog.test.ts` exercises the HTTP handler against temporary catalogs:
car isolation, current/archive selection, rollback, missing versions, unsafe
car/hash paths, and corrupt or mismatched files.
Run `npx vitest run tests/signals/catalog.test.ts` from the viewer directory.
