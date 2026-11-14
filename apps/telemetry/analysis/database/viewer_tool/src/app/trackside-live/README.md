# Trackside live

The Trackside application defaults to Artemis and its native gateway feed.
Discovery, history and live data use the shared API; existing saved historical
presets remain selectable. Connection settings report the gateway for Artemis.

The freshness badge uses sample time and Kafka backlog, so delayed samples do
not appear current merely because they just arrived. Coverage gaps reconnect
through the existing retry timer. Transport normalization and GPS speed units
are handled by the [telemetry adapters](../../lib/motec/README.md).

Run `npm run dev` from the viewer directory and open `/trackside-live` after
signing in. The [application guide](../../../../README.md) describes the
synthetic MQTT sender and isolated stack. Run `npx tsc --noEmit` and
`npx vitest run --config vitest.config.mjs` for local verification.
