# Signal catalogs

Reads the generated Orion catalog for DISCOVER. This is schema metadata,
not observed values or a live-device registry. Unknown cars are never served
Orion's schema as a fallback.

The catalog exposes one `rate_hz`: the uplink override, otherwise the CAN
message frequency. A rate of 0 means no trimming; null means unknown or conflicting
shared-source rates. External signals use their uplink rate or null.

`SIGNAL_CATALOG_DIR` contains `signals.json` for the current schema and optional
`<schema_hash>.json` archives. Without an override, the reader uses the repo's
`bazel-bin/lib/codegen/telemetry` directory relative to the viewer working
directory. Files are read on each request so deploying an updated catalog does
not require a process restart; deploy files by atomic rename.

The hash is validated before it can become a filename. Every loaded catalog
must have matching top-level/row versions and unique signal names. Missing
current catalogs return 503, unavailable requested versions 404, and corrupt
files 500. Archival is explicit; codegen does not retain old builds.

See the [viewer setup](../../../README.md) for generation and endpoint usage.
