# LAPDOG in Trackside Retro

`/trackside-retro` embeds the current LAPDOG workspace inside viewer_tool's
existing login, navigation and deployment. It includes movable video/track/
analysis panes, a resizable full-width timeline with wheel zoom, cross-file
lap comparisons on the same route, configurable sectors, cone logging,
lateral g estimates, data-quality checks and portable HTML debriefs.

## Open a session

Use **From database** to load an Orion drive day. The database is the only
source: the viewer does not import session JSON files or the Trackside Live
session cache. The route returns at most one packet per 100 ms, and only inside
lap windows; the displayed sample and GPS rates reflect that response rather
than assuming a fixed rate. Missing or
frozen GPS cannot supply a route and is reported instead of drawing a false
track.

## Review a run

Video, Track and Analysis can be hidden, reordered, resized or floated. The
timeline retains its speed graph and lap colours when compressed; scroll to
zoom around the pointer, shift-scroll to pan and double-click to reset.
Delete saved runs directly from the run selector. Notes and cones remain in
the timeline toolbar. Match the route before comparing another file's laps.

Use the analysis panels for telemetry, corners, battery cells, driving review,
setup comparisons, quality checks and HTML reports. Recorded lap times stay
fixed; sectors can use sensible geometry-based boundaries or manual markers.
Lateral g is estimated from GPS, not measured by an IMU. Steering jumps are
flagged without guessing a correction. Notes, layouts and runs are stored
in this browser (IndexedDB and the existing `driveday:` localStorage prefix).
Load a day again to apply improved GPS recovery and quality checks.

## Video

Choose an onboard file in Video. Every selection automatically requests an
H.264 playback copy from the loopback helper; the original stays unchanged.
Sync a frame with telemetry, optionally add a second checkpoint for drift,
and enable speed or racing-line arrows. The arrows are a calibrated
projection of the recorded route, not a computed ideal racing line.

`npm run dev` and `npm start` start the local helper automatically. For a
remote viewer, the helper must run on the user's computer, not only on the
server: `npm run video-helper -- --origin https://your-viewer-host.example`.
The panel provides setup and retry guidance. Conversion details and limits
are in [the helper README](../../../tools/README.md).

## Code and checks

- `src/lib/driveday/`: import, analysis, storage, player and canvas drawing.
- `src/components/driveday/`: LAPDOG panes and controls.
- `src/app/trackside-retro/`: route, fonts and styles scoped under `.ddv`.
- `tests/driveday/`: synthetic regression fixtures; real exports stay local.

```bash
npm run test:driveday
npm run test:video-helper
DRIVEDAY_UTA_DIR=path/to/Sessions npm run test:driveday
DRIVEDAY_COTA_SAMPLE=path/to/session.json npm run test:driveday
```

The standalone LAPDOG workspace was used as the source for this refresh.
Imports are namespaced under `driveday`; viewer_tool-specific adapters retain
server-cache loading, event notes and the host's authentication. Styles and
canvas theme tokens are scoped to the retro wrapper so other pages keep
their own palette.

## Loading from the database

`GET /api/driveday/db` lists the drive days that have laps. `GET
/api/driveday/db?day=<day_id>` builds that day in the same shape as a
trackside-live export (`src/lib/driveday/db.ts`), and the page converts it
with the same review workflow above.

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

## Driver view

Open **Driver** in Analysis to keep a combined throttle/brake graph and lap/
sector table visible together. Green is throttle; red is front brake pressure
normalized to this session's observed peak. Subdued dashed traces show the
fastest eligible completed lap on this course, aligned by track distance.
The comparison is independent of the map ghost selection and can be hidden.
If the current lap is the best, its traces are drawn once. If no eligible lap
exists, the comparison is unavailable rather than falling back to a skipped
lap. Display envelopes preserve brief pedal peaks without resampling stored
channels.

The cursor, live pedal percentages, current lap and current-sector underline
use the same Player clock as synchronized video. Drag the graph to scrub;
arrow keys move 0.1 s (Shift: 1 s). Select a lap or sector to seek directly.
The table keeps recorded split times fixed and marks the current row and
active sector. Graph labels remain HTML text so resizing does not distort
fonts. Fresh browsers open Driver by default; existing panel preferences
are preserved. The full Laps panel retains cone and eligibility editing.

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
