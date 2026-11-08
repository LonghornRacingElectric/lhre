# Signal catalogs

DISCOVER reads immutable published catalogs from the requested car's folder.
The current spec declares Orion; other cars use the same format and need
their own published artifacts. There is no Orion fallback or hardcoded car list.

The archive root is `SIGNAL_CATALOG_DIR`, defaulting to `apps/telemetry/catalogs`
relative to the viewer working directory. `<car>/current.json` selects the
default hash; `?schema_version=<hash>` bypasses the pointer and reads that exact
`<car>/<hash>.json`. Hash order and modification timestamps never select a version.
Files are read each request, so activation and rollback need no viewer restart.

Car slugs and hashes are validated before becoming paths. Loaded catalog car,
top-level version and requested filename must agree; signal names
must be unique. An unknown car folder returns 404, an unpublished/broken current
reference 503, a missing explicit version 404, and malformed JSON or mismatched
identity 500. Publishers validate the content hash before storing immutable files.

`schema_version` belongs only at the catalog's top level, never in signal rows.
The API returns the signal array and identifies its version in `X-Schema-Version`.

The catalog exposes one `rate_hz`: uplink override, otherwise CAN frequency.
Zero means no trimming; null means unknown or conflicting sources. External
signals use their uplink rate or null.

See the [archive guide](../../../../../../catalogs/README.md) and
[viewer setup](../../../README.md#signal-discovery-v0).
