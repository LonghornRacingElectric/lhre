# LAPDOG: drive day viewer (`/trackside-retro`)

Looks back at a drive day the way trackside-live looks at it as it happens.
Open a drive day and scrub through it like a video: a GPS map with F1-style
sectors, a timeline of every lap, corner and braking-zone breakdowns, battery
cell temperatures, the energy plan, cone and note logging.

Runs, notes and cones are kept in the browser (IndexedDB). The page reads
from the server but never writes back to it.

## Use it

Open `/trackside-retro` in `viewer_tool` and pick one of:

- **From database** lists the drive days in the `orion` database that have
  laps. Picking one loads that day's laps, cone flags and telemetry. This is
  the normal way in; there is no file to download.
- **Import JSON** (or drop files on the page) takes a session export from
  trackside-live. Use it for a session that never reached the server, or one
  someone sent you.
- **Last Trackside session** loads the server's cached live session. That
  cache is the trackside-live autosave and holds only the last ~1500 samples
  (about 14 minutes).

The page remembers loaded runs, notes, cones, setup values and where you were
in each run. Loading the same drive day again replaces that run instead of
adding a copy. The run dropdown lists every saved run with a **Delete**
button, including when there is only one run. Deletion asks which run to
remove; the database and the original JSON file are untouched. Removing
another run does not change the currently open run. Removing the current run
opens the next available run, or the start screen if none remain. Dropdowns
are positioned within the viewport and displayed above scrolling/floating
panes.

```bash
npm run test:driveday                                        # vitest, synthetic tracks
DRIVEDAY_SAMPLE=path/to/session.json npm run test:driveday        # plus a real export
DRIVEDAY_COTA_SAMPLE=path/to/cota_session.json npm run test:driveday  # COTA manual-cut and overlay regression
```

Real exports are too big to commit. `tests/driveday/real.test.ts` expects a
clean export (one course, no stray lap presses); on a messy day it fails and
that is a property of the day, not the code.

## Loading from the database

`GET /api/driveday/db` lists the drive days that have laps. `GET
/api/driveday/db?day=<day_id>` builds that day in the same shape as a
trackside-live export (`src/lib/driveday/db.ts`), and the page imports it
exactly like a file, so everything below applies to both.

- **Laps** are the day's `classifier` rows of type `lap`, the same lap
  presses trackside-live syncs. Rows over 15 minutes are the gaps between
  runs or a lap left open; they are not fetched. The importer then sorts the
  rest into timed runs and drives back as usual.
- **Cones and flags** are the `event_flag` rows. `Hit Cone` becomes a cone
  on its lap; other notes become timeline marks.
- **Samples** are one packet per 100 ms (10 Hz) inside the lap windows, from
  `dynamics`, `controls`, `pack` and `thermal`. GPS prefers
  `dynamics.gps_precise` and falls back to `dynamics.gps`. The hottest cell
  comes from `pack.cells_temps`. `avg_cell_v_stat` is not in the database,
  so that trace is empty.
- **Lap energy** is counted from the VCU's `net_energy` and `regen_energy`
  counters, the way trackside-live counts it. A step faster than 200 kW is a
  counter glitch (a frame read as 0), not driving, and is skipped.
- **Frozen GPS.** When the car's GPS stops updating, the database repeats
  one position. A lap whose positions span fewer than three distinct fixes
  is loaded without GPS, so a day with no working GPS says so instead of
  drawing a track of zero length. On 2026-10-17 and 10-18 (days 76, 81 and
  most of 80) this is the case.
- A day is tens of MB of JSON; the route gzips it (about 0.3–0.6 MB) and
  takes 1–6 s.
- Like every `/api` route in `viewer_tool`, it is outside the login
  middleware. It only reads.

## Where an export comes from

The JSON is the session export from the trackside-live page: `laps` (with
each lap's `samples`), `currentLap.lapSamples`, `sampleTail` (the exporter
caps it at 6,000 samples), `sessionInfo`, `soeCutoffCellV`, `targetLaps` and,
when the live page logged any, `flags`. The three sample lists are merged by
timestamp, so every distinct sample in the file is kept, 20 Hz included, and
a sample between two GPS fixes keeps its time and gets an interpolated
position. `types.ts` has the shape this code expects. There is no shared type
with trackside-live yet; when the export changes, this is the file that has
to follow. A session needs at least 30 GPS fixes.

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

- `convert.ts` turns an export into run data. It sorts the logger's laps into
  timed runs and slow drives back, groups the runs into courses, finds each
  track (matching a stored layout if one exists). Sector boundaries are
  selected when the run is opened; see below.
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
laps).

## Sectors and ghost timing

Automatic sectors aim for three balanced driving-time blocks on the fastest
logged lap, then snap the two boundaries to nearby corner/straight transitions
within 12% of lap time of those targets. If no suitable transition exists,
the boundary stays at its balanced-time position. These are analysis sectors,
not official circuit timing or marshal zones.

To choose your own boundaries, pause a completed lap at the desired landmark
(or click the track to seek there), open **Sectors**, and use **End S1 here**
or **End S2 here**. Keep them in order, at least 5 m apart and from the
start/finish. **Auto-place sectors** resets the suggestion. The boundaries
are saved per track in this browser, so changing laps or reopening a run
does not move them. Other stored runs on that track use them when reopened.

Every lap is timed through the same two gates using interpolated GPS crossing
times. Map colours, distance charts, sector bests and the stitched **Best
sectors** lap all use those gates. Missing crossings show a dash (blank in
CSV), never invented equal-time sectors. An unfinished final sector stays
unavailable.

**Best** is the fastest unskipped logged lap, not the cone-adjusted best.
Ghosts and race replay start together at logged lap time zero and stop at
their logged finish time. A faster lap can be behind through an individual
section, but its replay stops first. Recorded endpoints are kept rather than
snapped to the shared finish gate; a finished ghost is labelled **Finished**.
Distance comparisons are anchored at logged
start/finish, so their final gap matches the difference in lap times.

## Courses

- **A loop** has one start/finish line. Manual presses can happen at different
  points; a run also counts as a loop when another comparable run drives
  through its start or finish away from that other run's endpoints. A late
  press that extends into the next circuit does not lengthen the reference
  layout: the layout uses one observed return to its starting position.
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
- **Labels.** Laps keep the export's labels (for example, `Manual 3`), even
  when earlier presses or drive-back legs are filtered out. The timeline,
  charts, best-sector tooltips and CSV use those labels. The unfinished tail
  is `In progress`, rather than an invented lap number.
- **Overlay and joins.** The thick road is the shared reference layout; the
  thin dashed base-lap overlay follows its measured path and is never closed
  with an invented straight chord. A small gap between the manual start and
  end of a completed loop lap gets a labelled dashed join along the layout.
  It is untimed and has no speed/pedal colours. Open courses and unfinished
  laps are not joined. Reimport older runs to apply the corrected loop/course
  classification and original lap labels.
- **Sections.** Corner detection samples the reference layout every roughly
  2 m, smooths over a roughly 10 m neighbourhood, and requires sustained
  curvature for about 8 m. Short straight fragments are excluded from
  automatic sector-boundary suggestions. This reduces GPS-noise fragments;
  the Sectors menu still lets you choose boundaries at your own landmarks.

## Imported cones

Session exports can include `flags` with absolute logger timestamps. `Hit Cone`
(or `Cone`) flags on retained completed laps are imported automatically into
the lap counts, adjusted times, map, timeline and cone-hit list. Other flags
and hits outside those laps do not become cone penalties. A hit at a shared
lap boundary belongs to the next lap. The `saved` flag only indicates whether
the live database write succeeded; a recorded hit still counts when it is false.

Imported hits use stable IDs, so reopening or reimporting does not duplicate
them. Manually added cones are kept, and deleting an imported hit prevents it
from returning on reload. Reimport older runs to pick up exported flags that
the previous importer ignored. Older exports without `flags` cannot supply
automatic cone counts; you can still enter them manually.

## What it does not do

The earlier single-file version had a few things that depended on its host
and were left out here: shared notes between people, an "ask about this run"
box. Notes are per browser; sharing them would need a server.

## Compare files and play video

Load both runs (from the database or as JSON), open one, then choose **More → Compare**.
Choose another stored run on the same route and a completed lap. It becomes
the ghost on the map, telemetry distance charts, corner comparison and lap
comparison table. Logged times stay unchanged; sectors use the active run's
gates and brake percentages use its pressure scale. Current-run bests,
cone counts and theoretical laps are not changed by the comparison.

Route matching uses the stored track identity and direction, not the venue
name. Point-to-point courses must also share their ordered start and finish
within 6 m, as well as the existing layout coverage checks. Reimport files
that were imported separately before this feature so they share a layout.
The comparison choice lasts while the run is open.

In the **Video** window, use **Open video**. New files open in **Video preview**
at their beginning, with native playback, audio and seeking controls. Pause
at a recognisable event, move telemetry to the same event, then use **Sync
this frame**. The shared **Play** control and session timeline then control telemetry
and video together; the synced video panel has no duplicate play button or
scrubber. **Adjust sync** returns to independent preview; an
optional **Fine-tune alignment** control nudges the offset by 0.1 seconds.
The offset is the video time at session time zero; negative offsets work
for recordings that start later. Outside the clip, its first or last frame
is held with a visible recording-boundary label.

The video is read through a local browser URL, never uploaded or copied into
run storage. Its alignment is saved per file (name, size and last-modified
time) and run, so replacing a clip does not reuse another clip's offset. Reselect the file after
reopening the run. Closing the Video panel pauses the video; reopening it
resumes synchronisation. Browser codec support applies (H.264 MP4 or WebM
usually works). GoPro HEVC/H.265 clips require a browser with an HEVC decoder;
an MP4 extension alone does not guarantee support. If decoding fails, the
panel shows a visible explanation and **Open another video**, rather than a
blank player. Make an H.264 MP4 copy for browsers without HEVC support.
Codec detection reads only small header/tail slices of large files; full
recordings are streamed by the browser, never read into a giant array buffer.

### Video overlays

After syncing a frame, the collapsed **Video settings** menu toggles the speed/pedal HUD, GPS
lateral g and lap clock, track-map inset, and **Racing line**. Move the HUD
between bottom-left and bottom-right; the inset uses the opposite corner.
Overlays appear only while video and telemetry are synced and inside the
recording. Fullscreen includes them and preserves the video's aspect ratio.
These are live viewer overlays; the original video file is unchanged.

The racing line uses spaced, coloured **chevron arrows** pointing along the
recorded path, shrinking toward the horizon. It is an **approximate recorded GPS preview**, not an ideal
line or a camera-tracked augmented-reality overlay. It projects the next
55 m of the watched or ghost lap using the watched car's GPS heading and a
level-road camera model. Green means throttle, yellow lift and red braking
(above 15% of the run's peak pressure). No GPS line is drawn at very low
speed, without a usable heading, or beyond available recorded lap points.
It stops when the path turns behind the camera rather than joining unseen
points across a hairpin.

Use **Racing line** and the horizon, horizontal-position and perspective
sliders to fit the camera, or drag the line sideways. Settings are saved
per file and run. GPS noise, banking, hills and camera motion affect the
fit; anchoring the line to road features throughout a clip would require
camera calibration and image tracking that this viewer does not perform.

## Workspace windows

The default desktop layout is **Video | Track | Analysis**, above one
full-width playback and timeline row. The track includes
the map; analysis holds the existing panel
picker. Video has its own window, so watching it does not close a graph.
Narrow screens stack the windows in their saved order.

The timeline spans the workspace by default. **More → Dock timeline into
track** restores the embedded layout; **Undock timeline** moves it back.
There is no separate layout dropdown. Compact heights retain seeking,
chapters, timed events and loops, without the speed graph. Pane heights adapt
to the space left by the full-width row. Hiding Track while its timeline is
embedded restores a shared playback row. Placement is saved, and **Reset
layout** restores the full-width timeline.
Drag the small horizontal grip above the playback controls to resize the
timeline manually (48–240 px), or use Up/Down when the grip is focused.
Double-click it for the compact strip. The height is saved independently of
placement and works inside Track too. Short heights show the compact lap
strip instead of squeezing the speed graph; **Reset layout** restores 108 px.
Text remains at a fixed pixel size as the graph resizes; long chapter labels
truncate with an ellipsis instead of stretching to fit.

Video prioritises its preview area. Its regular synced controls are just
**Adjust sync** and **Fullscreen**; alignment and overlay controls are under
**Video settings**. Unlinked video retains native independent controls, and
fullscreen has its own playback control while the shared controls are hidden.

- Use the **Windows** buttons to hide/show each window, or its header's **×**.
- Drag a docked header onto another header to reorder, or use its left/right
  header buttons. Drag dividers to resize adjacent docked windows; arrow keys adjust
  a focused divider, and double-click equalises the pair.
- **Float** detaches a window within this browser tab. Drag its header to move
  it, resize from the bottom-right corner, and use **Dock** to return it to
  the layout. Clicking a floating window brings it forward.
- **Reset layout** restores the default order, visibility and sizes. Layout
  is saved in this browser, and floating windows are kept reachable after a
  viewport change. These are in-tab windows, not separate OS popups.

Moving, floating and hiding preserve mounted content and the loaded video.
Hiding Video pauses its media without stopping telemetry; showing it again
seeks to the current telemetry position if synced. **D** toggles Analysis.

The run selector, From database, Import JSON, Settings and window toggles share a single toolbar.
It scrolls horizontally on narrow screens instead of wrapping or widening
the page. Pane headers expose move, float/dock and hide buttons; small map panes show compact lap
and speed readouts, and playback groups ghost selection in **Ghost** and
loops and timeline placement in **More**. **+ Note** and **+ Cone** stay directly
on the timeline toolbar. Keyboard shortcuts
remain available. Overlay and calibration controls start collapsed under
**Video settings** so the video keeps its space.

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
- This started as a standalone Next.js app (`apps/telemetry/analysis/driveday_data_viewer`,
  removed in the PR that moved it here). It lives inside `viewer_tool` so it
  shares the login, the session cache, the database and one deploy. ADR-013
  records the earlier decision.
