# LAPDOG in Trackside Retro

`/trackside-retro` embeds the current LAPDOG workspace inside viewer_tool's
existing login, navigation and deployment. It includes movable video/track/
analysis panes, a resizable full-width timeline with wheel zoom, cross-file
lap comparisons on the same route, configurable sectors, cone logging,
lateral g estimates, data-quality checks and portable HTML debriefs.

## Open a session

Use **Import**, drop session JSON, or choose **Last Trackside session** to
load `/api/motec/live/session-cache`. Imports merge completed-lap samples,
current-lap samples and the recent tail by timestamp. Every stored timestamp
is retained, including 50 Hz recordings; the displayed sample and GPS rates
reflect the export rather than assuming a fixed rate. Empty per-lap arrays
can be recovered from overlapping tail samples. Missing or frozen GPS cannot
supply a route and is reported instead of drawing a false track.

The server cache strips per-lap samples and keeps at most 1,500 tail samples;
the downloaded export keeps at most 6,000 tail samples. At 50 Hz those tails
span about 30 seconds and two minutes respectively. Neither reconstructs
older samples absent from the file. This refresh does not change live
capture, the cache, database ingestion or retention settings.

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
Reimport a run to apply improved GPS recovery and quality checks.

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
