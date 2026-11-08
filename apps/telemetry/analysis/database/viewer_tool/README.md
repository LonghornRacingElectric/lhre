# viewer_tool

Next.js viewer for live telemetry and replay workflows.

## Current multi-car status

Implemented:
- Header-driven live routing for `Orion` and `Angelique` (`car_type` respected end-to-end).
- Server-side car filtering in `/api/kafka-stream` via `?car=<orion|angelique>`.
- Canonical live topics from raw `sensor_data`: `car_visualization`, `driver_input_visualizer`, `map`, `live_banner`.
- Additional normalized live topics for remaining widgets: `dashboard_screen`, `timing_deltas`, `shutdown_screen`, `thermal_headroom`, `energy_budget`.
- Live car selector in `LiveViewerBanner` with URL/localStorage persistence and keyboard cycling (`[` / `]`).
- Rollout flag support with `NEXT_PUBLIC_VIEWER_MULTI_CAR` (set `false`/`0` to lock viewer to active/default car).
- Car-aware replay packet reads and handshake/end-event/status packet-end lookups.
- Thermal, energy, dashboard, shutdown, and timing widgets now consume live telemetry-backed contracts.

Still pending:
- Full sector/lap timing delta integration (current timing tile is telemetry pace-trend based).
- Add integration tests for simultaneous two-car streams and full widget parity.

## Kafka routing model

- Raw topics are consumed once by the backend Kafka consumer.
- Live widget topics are emitted on the in-process event bus.
- `kafka.routes.json` handles pass-through routing for derived topics (`gg-plot`, `track-mapper`).
- `sensor_data` normalization is handled directly in `src/lib/kafka/kafkaConsumer.ts`.

## Signal discovery (v0)

Build and publish the current car's catalog from the repo root:

```bash
bazel run --config=local //tools/spec:publish_signals
```

The current spec declares `car: "orion"` in `groups.textproto`.
With the viewer running, `GET /api/cars/orion/signals` returns its signal array,
including CAN, GPS, and board-status fields.
`?schema_version=<64-character-lowercase-hash>` selects an exact version in that
car's folder; omitting it follows that car's `current.json` pointer.
The response carries the schema hash in every row and `X-Schema-Version`.
Every car uses the same catalog shape but its own archive; there is no cross-car
fallback. No Kafka or
database connection is needed for discovery. This v0 read endpoint has no
API-key authentication; adding reader/admin keys remains a later phase.

The archive root defaults to `apps/telemetry/catalogs`. For deployment, set
`SIGNAL_CATALOG_DIR` in both the publisher and viewer, or pass
`-- --catalog-root /absolute/path` to the publishing command. It stores
`<car>/<hash>.json` and atomically updates `<car>/current.json`. An identical
publish is a no-op; old versions are immutable. Hashes identify canonical
catalog contents, including car, independently of paths or textproto formatting.
Build outputs still live in `bazel-bin` and are not used as the archive.
See the [archive guide](../../../catalogs/README.md) for storage, backups, and
rollback. Unpublished current versions return 503; unknown cars or missing
requested versions return 404; malformed car slugs/hashes return 400.
No rates are guessed and mixed-unit GPS arrays have a null unit. CAN shared
sources and repeated array slots appear once per protobuf field.

External telemetry must declare an uplink limit; the four GPS fields declare
50 Hz, matching publishd's default snapshot limit. This is a declared policy,
not a measured acquisition rate or a per-signal limit currently enforced by
publishd. The catalog exposes only `rate_hz`: the explicit uplink rate, otherwise
the CAN message frequency. External signals have no native-frequency fallback;
their rate is null when no uplink rate is declared. Numeric scalar
external signals require explicit units; mixed-unit arrays may omit them.

Catalog serving tests: `npx vitest run tests/signals/catalog.test.ts`.

## Environment variables

Example viewer-side Kafka/env values:

```env
KAFKA_BROKERS=localhost:29092
KAFKA_CLIENT_ID=viewer-tool
KAFKA_GROUP_ID=viewer-tool-group
KAFKA_TOPICS=status,sensor_data,gg-plot,track-mapper
KAFKA_ROUTES_FILE=./kafka.routes.json
KAFKA_SENSOR_TOPIC=sensor_data
NEXT_PUBLIC_DEFAULT_LIVE_CAR=orion
NEXT_PUBLIC_VIEWER_MULTI_CAR=true
```

## Local setup

The viewer, the Prisma seed scripts, and the docker compose stack all read one
`.env` at the repository root (`cp .env.example .env`).

Install deps. The `postinstall` hook generates the `auth`, `telemetry`, and
`angelique` Prisma clients into `.prisma/` and runs `prisma db push` on the auth
SQLite file (`AUTH_DATABASE_URL`) so it matches `prisma/auth.prisma` — there are
no migrations, and the committed `prisma/dev.db` predates `User.isAdmin`:

```bash
npm install
```

To create a login during install, set `VIEWER_USERNAME` and `VIEWER_PASSWORD`
(and `VIEWER_ADMIN=1` for an admin) in the root `.env`; the user is upserted, so
reinstalling resets the password. Or create one any time:

```bash
npm run prisma-auth-seed -- <username> <password> [admin]
```

If the auth setup step fails, rerun it with `npm run prisma-auth-setup`. Note
that `prisma/dev.db` is tracked in git, so the push modifies it in your working
tree; don't commit it with your own users in it.

> Note: `prisma/orion.prisma` still includes optional-list fields (`Float[]?`) that Prisma 6.x rejects, and nothing imports its client, so `prisma-orion-generate` is not part of `postinstall`.

Generate protobuf TS stubs (optional helper scripts):

```bash
npm run protobuf-angelique
npm run protobuf-orion
```

Run dev server:

```bash
npm run dev
```

## Hosting

The viewer is self-hosted: `npm run build` + `npm start` on the telemetry
box, kept up by PM2 (see [the stack README](../../../stack/README.md)) and
reached through a Cloudflare tunnel. Nothing in this directory targets
Vercel; the `lapdog` Vercel check that used to post on PRs belonged to the
removed standalone drive day app and is managed in Vercel's dashboard, not
here.

## Trackside live

`/trackside-live` is the pit-wall page: live samples from Kafka, lap timing,
energy plan, driver dash. Lap controls, in the Live Laps panel:

- **Start Lap / Log Lap** starts the timer, then closes a lap and re-arms the
  next one (a loop). **Stop Lap** closes the lap without re-arming, for
  autocross, accel and skidpad runs where the drive back is not a lap; Start
  Lap arms the next run. Both are leader-only.
- **Add time** enters a lap by its duration (`m:ss.ss` or seconds) when the
  official timing or a stopwatch has it and the page does not. It is a
  `manual` lap with no samples, sectors or energy, so it counts toward laps
  and the average and nothing else.
- A new session is named `<venue> · <event type> N · YYYY-MM-DD`, N counting
  this browser's sessions of that type today; type over it if you want.

The page keeps the last 6,000 samples in memory and in the JSON export, so
at 20 Hz the export holds the last 5 minutes of samples and at 50 Hz the last
2 minutes (laps keep their times either way). The full stream is in Postgres.

## Drive day viewer

`/trackside-retro` replays a trackside-live session export after the fact:
map with sectors, lap timeline, corners and braking zones, cell temperatures,
energy plan. It runs entirely in the browser and keeps its data in IndexedDB.
How it sorts lap presses into courses, where the export comes from and how to
test it are in [src/lib/driveday/README.md](src/lib/driveday/README.md);
`npm run test:driveday` runs its tests.

## LAPDOG refresh

Trackside Retro includes the current LAPDOG video/track/analysis workspace,
wheel-zoom timeline, route-matched cross-file comparisons, GPS-quality checks
and driving debrief tools. Drive days load only from the database; Trackside
event notes come with them. See [the retro guide](src/lib/driveday/README.md).

Video playback copies are made locally using `ffmpeg-static`.
`npm run dev` and `npm start` launch the loopback helper; remote users run
`npm run video-helper -- --origin https://your-viewer-host.example` on their
own computer. Originals are not uploaded to the viewer server. See
[helper setup](tools/README.md). No database setup is needed for the helper.
