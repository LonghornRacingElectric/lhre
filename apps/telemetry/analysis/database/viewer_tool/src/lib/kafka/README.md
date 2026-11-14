# Legacy transport and widget normalization

Historical Orion routes use the Kafka consumer and in-process bus here. Artemis
application routes use the [query gateway adapter](../query/README.md), which
imports `buildNormalizedSensorRoutes` only to preserve existing widget shapes.
That helper accepts snake_case native fields and camelCase decoded fields.
Artemis map widgets prefer `gps_precise` coordinates and fall back to `gps`
when the precise vector is empty or unavailable.
Importing it does not start a consumer.

The backend gateway owns Artemis Kafka consumption. Do not create another
consumer for an Artemis widget. Run `npx vitest run --config vitest.config.mjs
tests/query` from the viewer directory to verify the adapter's field mapping.
