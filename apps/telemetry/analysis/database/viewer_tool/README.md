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

Trackside Retro's **Driver** panel combines overlaid throttle/front-brake
traces with a subdued session-best comparison and a lap/sector table that
highlights the current playback position. It shares the video sync clock;
see [the driver guide](src/lib/driveday/README.md#driver-view).

### Team layouts and simultaneous analysis

Choose **Team layout** for Driver (video, map, pedals and laps), Electronics
(full-width car, cells, power and temperatures), or Trackside (laps and cones, map
and video). Windows still hide, resize, move and float. Analysis chips now
independently open and close panels. Multiple panels tile in wide windows and
stack in narrow ones, sharing the playback clock. Named layouts retain every
open panel; older single-panel layouts still load. The timeline starts compact
and remains manually resizable. Existing saved sizes are preserved.

Layout presets and saved configurations share the **Layouts** menu. **Import**
contains JSON files, the last Trackside session and database drive days. Each
analysis panel has its own Float, Dock and Close controls; drag docked panel
headers to reorder them. Floating panels move by their header and resize from
the corner. Their position and size are remembered in this browser.

Docked analysis panels resize from the bottom-right grip (or its arrow keys).
Widths snap to a shared grid; narrow workspaces stack panels at full width.
Panel height and width preferences persist separately from floating geometry.
Panel contents have additional inset padding in narrow workspaces.

Driver and Trackside hide intervals between laps from the timeline; Electronics
shows the entire session. Use **More > Show between laps** in the playback
toolbar to switch. Hidden intervals collapse from the axis and playback skips
them, while zoom, seeking and video retain original session timestamps. Notes
and alerts in hidden intervals reappear when enabled. No samples are removed.
If a recording has no lap intervals, the full session remains visible. The
selected map trace uses a thinner four-pixel stroke with a contrasting casing.

### Compact analysis panels

Analysis panels focus on charts, measurements and actions. The Laps panel has
an Include checkbox for each completed lap instead of marking dropdowns and
eligibility-rule controls; existing saved marks remain intact. Compare selects
a lap for comparison, and cone counts and penalties remain editable. Help text
lives here rather than in panel paragraphs. The experimental 3D replay panel
has been removed; video, track, layouts and other analysis panels remain.

Analysis windows clip their contents and scroll independently, docked or floating. The compact header keeps session controls beside the title where space allows. Timeline chapters retain their colours at every height: purple is fastest, yellow is slowest, green is other eligible laps; excluded laps remain hatched. Notes use speech bubbles and timed cones use orange dots above the chapters.

The header shows the session name once; Runs opens the session selector. Video, Track and Analysis can each be hidden or restored from the toolbar. On the map, the gray reference layout uses a neutral dashed centerline beneath the selected lap, rather than sector coloring. Fine dots show the selected ghost’s recorded path (hidden when Ghost is off or the same lap is selected); larger dashes at the lap boundary show an untimed continuation between manual presses. Layout-only mode still colors the reference by sector.

Legacy “Manual N” lap names display as “Lap N”, preserving their logged numbers. Metric tooltips fit within the viewport and scroll when their content exceeds the available height.

Docked analysis panels fill their workspace and divide its height equally whenever panels are opened, closed or docked. Manual resizing remains available until the next panel change. Pane headers retain drag and window controls without repeating Video/Track labels.

Use the side-by-side control above analysis panels to switch between equal columns and equal rows. Dropping a panel header on the left or right third of another header snaps it to that side; the center retains reorder behavior. Each panel scrolls independently. Map controls include inset spacing around Colour by.

Track color, overlay and sector controls live in the track window header, freeing map height. They wrap within narrow or floating windows while window actions stay accessible.

The map road band is 11.5 ft (3.5052 m) wide at the current map scale, centered on the reference path. This is a visual width guide, not surveyed road boundaries. The car remains a readable position dot; its marker size does not represent the physical vehicle envelope.

Video, track and analysis header rows share the same inset and minimum height. Runs sizes to its label so the session title has more room. Driver lap tables include an inset and reserved scrollbar space.

Out laps are omitted from the lap table, ghost choices, timeline chapters and previous/next lap navigation. Source samples and lap indices remain intact, preserving notes and cone associations. The optional between-lap timeline view can still expose recording gaps.

The compact top toolbar leaves more space for session metadata, which wraps instead of truncating.
