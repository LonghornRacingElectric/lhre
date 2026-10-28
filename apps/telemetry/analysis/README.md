# Telemetry analysis

Python analysis and database access used by ingest and the stack processors.
`//apps/telemetry/analysis:analysis_lib` is the shared Bazel library.

Orion ORM classes are generated from the textproto by
`//lib/codegen/telemetry:orion_models`, not synchronized into this tree.
Existing imports from `sql_utils.models` still work through its re-exports.
Nightwatch, Angelique and shared event/track metadata keep their existing
definitions because they are not the current-car CAN schema.

See [SQL utilities](sql_utils/README.md) and
[the viewer](database/viewer_tool/README.md) for consumers.
