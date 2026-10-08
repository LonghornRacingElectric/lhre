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

Open <http://localhost:3002>, then click **Import JSON** or drop a file on the
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

- `convert.ts` turns an export into run data. It finds the track, matches it
  to a stored layout if one exists, and cuts sectors at thirds of the loop.
- `rebuild.ts` re-cuts the laps wherever the start/finish line is now. The
  line is saved per track, so every run on that layout shares it.
- `Player` (`player.ts`) is the one mutable model: playhead, camera, ghost,
  notes. React reads it through two subscriptions, one for options and one
  per painted frame. Canvases are painted from it in one animation frame
  loop, so playback never goes through React state.
- Stored data is under the `driveday:` prefix in localStorage, plus an
  IndexedDB database for runs and track layouts. If IndexedDB is blocked the
  app falls back to memory for the tab and says so.

## What it does not do

The earlier single-file version had a few things that depended on its host
and were left out here: shared notes between people, an "ask about this run"
box, and comparing against another driver's run. Notes are per browser. Each
would need a server, so they are follow-ups.

## Gotchas

- GPS runs at about 1.8 Hz, so sector times carry a tenth or two of noise and
  some braking zones are only one or two fixes long.
- Third sectors are missing on the unfinished last lap, and cones are only
  counted on completed laps.
- Units (mph or km/h, °C or °F) and the theme are display settings. Stored
  speeds are mph and temperatures °C.
