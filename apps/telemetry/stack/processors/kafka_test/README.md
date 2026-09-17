# Kafka test processor

This optional development processor emits synthetic status messages and can
observe the `db_inserts` topic. It is useful for UI and transport testing, not
for production ingestion.

Targets follow the standard `kafka_test_binary`, `_image`, `_load`, `_push`,
and `_smoke_test` contract. Enable it explicitly with
`./apps/telemetry/stack/server_devtool.sh enable kafka_test`; it is never part
of `core_images`.
