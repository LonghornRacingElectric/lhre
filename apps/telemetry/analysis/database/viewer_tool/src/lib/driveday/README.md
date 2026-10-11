# Drive day viewer (`/trackside-retro`)

Looks back at a drive day the way trackside-live looks at it as it happens.
Import a session export and scrub through it like a video: a GPS map with
F1-style sectors, a timeline of every lap, corner and braking-zone
breakdowns, battery cell temperatures, the energy plan, cone and note
logging.

Nothing leaves the browser. The export is parsed client-side and kept in
IndexedDB, so the page works on an archived export with no car and no Kafka.

## Use it

Open `/trackside-retro` in `viewer_tool`, then **Import JSON** (or drop a
file on the page) or **Last Trackside session**, which loads the server's
cached live session. That cache is the trackside-live autosave and holds only
the last ~1500 samples (about 14 minutes), so for a whole day use the JSON
downloaded from trackside-live. The page remembers imported runs, notes,
cones, setup values and where you were in each run.

```bash
npm run test:driveday                                   # vitest, synthetic tracks
DRIVEDAY_SAMPLE=path/to/session.json npm run test:driveday   # plus a real export
```

Real exports are too big to commit. `tests/driveday/real.test.ts` expects a
clean export (one course, no stray lap presses); on a messy day it fails and
that is a property of the day, not the code.

## Where the export comes from

The JSON is the session export from the trackside-live page: `sampleTail`,
`laps`, `sessionInfo`, `soeCutoffCellV`, `targetLaps` and, when the live page
logged any, `flags`. `types.ts` has the shape this code expects. There is no
shared type with trackside-live yet; when the export changes, this is the
file that has to follow. A session needs at least 30 GPS fixes.

## How it is laid out

```text
src/lib/driveday/         analysis, no React: convert, rebuild, geometry,
                          segments, session, analysis, energy, debrief,
                          storage, player
src/lib/driveday/draw/    canvas drawing: map, timeline, traces, charts, cells
src/components/driveday/  React: header, map, transport, dock, panels
src/app/trackside-retro/  the route: page.tsx mounts the App, driveday.css
                          carries the viewer's palette scoped under .ddv so
                          it does not fight viewer_tool's globals
tests/driveday/           vitest, with a synthetic stadium-shaped track in
                          fixtures/
```

- `convert.ts` turns an export into run data. It sorts the logger's laps
  into timed runs and slow drives back, groups the runs into courses, finds
  each track (matching a stored layout if one exists), and cuts sectors at
  thirds.
- `rebuild.ts` makes each lap of a loop end exactly where the next begins,
  so the car marker never jumps between laps.
- `Player` (`player.ts`) is the one mutable model: playhead, camera, ghost,
  notes. React reads it through two subscriptions, one for options and one
  per painted frame. Canvases are painted from it in one animation frame
  loop, so playback never goes through React state.
- Stored data is under the `driveday:` prefix in localStorage, plus an
  IndexedDB database for runs and track layouts. If IndexedDB is blocked the
  viewer falls back to memory for the tab and says so.

## Start and finish lines, and courses

Lap times are always the logger's. The lines are never set by hand: the start
line is where the car was when the timed laps began, and the finish line is
where it was when they ended (the middle of those positions over all the
laps), and the sectors are cut at thirds between them.

- **A loop** (laps end where they began) has one start/finish line. Manual
  lap presses on a loop land tens of metres apart, so a run also counts as a
  lap of the loop when another run drove on through the point where it
  began or ended. On a course with its own start and finish lines no run
  does that.
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
- **Lap names** are the logger's labels ("Manual 3"), not a renumbering from
  1, so a lap here is the same lap on the trackside-live page and in the CSV
  export. Whatever was driven after the last press shows as "... (in
  progress)" when it is at lap pace, and is dropped as the drive back to the
  pits when it is not.

## What it does not do

The earlier single-file version had a few things that depended on its host
and were left out: shared notes between people, an "ask about this run" box,
and comparing against another driver's run. Notes are per browser. Each would
need a server, so they are follow-ups.

## Gotchas

- GPS runs at about 1.8 Hz, so sector times carry a tenth or two of noise and
  some braking zones are only one or two fixes long. Expect the drawn line to
  cut corners on a tight autocross course; that is the data, not the fit.
- Third sectors are missing on the unfinished last lap, and cones are only
  counted on completed laps.
- Units (mph or km/h, °C or °F) and the theme are display settings. Stored
  speeds are mph and temperatures °C.
- This started as a standalone Next.js app (`apps/telemetry/analysis/driveday_data_viewer`,
  removed in the PR that moved it here). It lives inside `viewer_tool` so it
  shares the login, the session cache and one deploy.
