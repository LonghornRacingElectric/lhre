# Drive day viewer

A local web app for looking at a drive day. Import a session export and
scrub through it like a video: a GPS map with F1-style sectors, a timeline of
every lap, corner and braking-zone breakdowns, battery cell temperatures,
the energy plan, cone and note logging.

Nothing leaves your machine. The export is parsed in the browser and kept in
IndexedDB.

## Run it

You need Node 20 or newer.

```bash
npm install
npm run dev
```

Open <http://localhost:3002> for the LAPDOG landing page, or go straight to
<http://localhost:3002/lapdogviewer> for the viewer, then click **Import** or drop a file on the
page. The page remembers imported runs, your notes, cones, setup values and
where you were in each run.

| Script | What it does |
| --- | --- |
| `npm run dev` | Dev server on port 3002 |
| `npm run build` / `npm start` | Production build and server |
| `npm run lint` | ESLint (`next/core-web-vitals` + TypeScript) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | vitest |

To run the tests against a real export as well (they are too big to commit):

```bash
DRIVEDAY_SAMPLE=path/to/session.json npm test
```

## Where the export comes from

The JSON is the session export from the trackside-live page of
[`viewer_tool`](../database/viewer_tool/README.md): `sampleTail`, `laps`,
`sessionInfo`, `soeCutoffCellV` and `targetLaps`. `src/lib/types.ts` has the
shape this app expects. A session needs at least 30 GPS fixes.

## How it is laid out

```text
src/lib/        analysis, no React: convert, rebuild, geometry, segments,
                session, analysis, energy, debrief, storage, player
src/lib/draw/   canvas drawing: map, timeline, traces, charts, cells
src/components/ React: header, map, transport, dock, panels
tests/          vitest, with a synthetic stadium-shaped track in fixtures/
```

- `convert.ts` turns an export into run data. It sorts the logger's laps into
  timed runs and slow drives back, groups the runs into courses, finds each
  track (matching a stored layout if one exists), and cuts sectors at thirds.
- `rebuild.ts` makes each lap of a loop end exactly where the next begins,
  so the car marker never jumps between laps.
- `Player` (`player.ts`) is the one mutable model: playhead, camera, ghost,
  notes. React reads it through two subscriptions, one for options and one
  per painted frame. Canvases are painted from it in one animation frame
  loop, so playback never goes through React state.
- Stored data is under the `driveday:` prefix in localStorage, plus an
  IndexedDB database for runs and track layouts. If IndexedDB is blocked the
  app falls back to memory for the tab and says so.

## Start and finish lines, and courses

Lap times are always the logger's. The lines are never set by hand: the start
line is where the car was when the timed laps began, and the finish line is
where it was when they ended (the middle of those positions over all the
laps), and the sectors are cut at thirds between them.

- **A loop** (laps end where they began) has one start/finish line.
- **An open course** (the finish line is somewhere else, as on an autocross
  day) has a start line and a finish line, and the path between them is the
  course.
- **Courses.** The logger marks a lap every time the button is pressed, so a
  drive back to the start is also a "lap". On import, short or slow legs
  (under 60% of a typical run's length or 40% of its speed) are left out.
  The remaining runs are grouped by where they start and finish and which way
  they go, and each group opens as its own run in the dropdown (for example
  "Course 2 (reverse of 1)"). Laps on different lines are not comparable, so
  they are never mixed in one set of bests, ghosts and charts. Runs whose
  start and finish both lie within about 25 m of each other (or 10% of the
  run length) count as the same course.

## What it does not do

The earlier single-file version had a few things that depended on its host
and were left out here: shared notes between people, an "ask about this run"
box, and comparing against another driver's run. Notes are per browser. Each
would need a server, so they are follow-ups.

## Gotchas

- The selected lap has a thicker, outlined colour trace. Low-value sections
  and the path ahead of the playhead stay visible; ghost laps remain faint.
- Lateral g is shown in the map HUD, GPS hover readings and Car panel as a
  **GPS estimate**: speed squared times the signed curvature of three
  fixes on the smoothed playback path, divided by standard gravity. L/R means left/right turn.
  Endpoints, repeated timestamps, speeds below 1 m/s and fixes less than
  1 m apart over the estimation window show a dash. The window expands
  around high-rate fixes until each side covers at least 150 ms and 1 m,
  up to 1.5 s per side. GPS noise affects this estimate; it is not an IMU
  reading and does not account for banking.
- Car readings reflow within the panel; pack modules A, B and C have their
  own temperature tiles. On phones the map and panels stack vertically,
  legends wrap and tables scroll within their panel.
- The landing page and browser icon use the current LAPDOG logo.

- Imports merge full `laps[].samples` and `currentLap.lapSamples` with `sampleTail` (which the exporter
  caps at 6,000 samples). Overlapping timestamps are merged, not duplicated;
  all distinct timestamps in retained laps are kept, including 20 Hz data.
  Telemetry between GPS fixes keeps its timestamp and gets an interpolated
  position. Samples outside the available GPS range cannot be mapped.
- GPS rate depends on the export, not a fixed 1.8 Hz limit. Playback positions
  and derived speed and acceleration use centred 0.5 s time averages without
  removing samples. Imported fixes, telemetry and logged lap times are kept;
  lap boundaries remain fixed so adjacent laps meet. Already imported runs
  need reimporting to recover lap samples missed by the old importer.
- Third sectors are missing on the unfinished last lap, and cones are only
  counted on completed laps.
- Units (mph or km/h, °C or °F) and the theme are display settings. Stored
  speeds are mph and temperatures °C.

  THIS IS A TEST FOR PUSHING
 
