# Development log

Times are America/Chicago on 2026-11-08; runner console times use UTC.

- Reproduced nine narrow/opposing-link failures before the #85 change.
- Reproduced non-recovering labels, stationary seeding and stale evidence
  before the #86 change. Retained existing progressive-map regressions.
- Independent review found stale-fragment confirmation and a recovery-motion
  deadlock. Added failing node/config regressions, then per-component evidence
  support and inference-only geometry planning. Fusion color filtering stays
  enabled.
- Build completed for all 16 packages. New test E501 errors were corrected;
  rerun perception/Gazebo suites pass. Final aggregate results: 1,083 tests,
  zero errors/failures, two artifact-dependent native-FMU skips.
- 01:51:09: Started the headless sim/truth baseline in a fresh output folder.
- 01:52:05: Started the Mid-360 inference-only/EKF run with stereo/fusion off.
- 01:53:01: Started the Mid-360/stereo/fusion/EKF run with the same controller
  settings and documented Mac diagnostic timeout.
- Both runs scored a lap and passed all eight existing checks. Inference-only:
  max CTE 0.2464 m, EKF error 0.0578 m, stop 1.44 m. Fusion: 0.2462 m,
  0.0568 m and 1.45 m. Zero cones/off-course events and no safety trips.
- User follow-up: `2026-11-08-165212-accel-check`, three acceleration repeats
  before pulling #230. All pass every scorer check; stop distances 1.45,
  1.47 and 1.45 m, max post-stop speeds 0.09, 0.00 and 0.09 m/s. Retained
  separately from the earlier failed single run.
- Integrated #230 (`06d23796`) into the fixing branch, preserving gate sensor
  defaults and complete-command provenance. Gate/metrics tests pass; aggregate
  results are 1,089 tests, zero errors/failures, two native-FMU skips.
- Post-merge acceleration smoke passes every check with the new watcher:
  `reason=finished mission=FINISHED sim_s=30.9 wall_s=53.3`, stop 1.45 m.
  Raw output: `2026-11-08-issue206-sim-time-merge`.
- User campaign `2026-11-08-170935-autocross-check`: seeds 1–5, three repeats
  each and five baselines. All 15 gate runs pass; zero cones/off-course/safety
  trips, max CTE 0.2535 m, max EKF error 0.0763 m, stops 1.37–1.85 m and
  lap ratios 1.00. Exact arguments confirm the combined stack and Mac 1.5 s
  timeout. Raw output retained in the container.
- User camera-interruption check on seed 2: user reports it looked good.
  Final scorer independently passes 1/1, with zero cones/off-course/health
  failures, max CTE 0.2542 m, EKF error 0.0481 m and stop 1.37 m. Retained
  separately at `2026-11-08-175111-seed2-camera-fallback-check`; no automated
  camera/LiDAR timestamp trace or measured pause duration was captured.
- Provisional integration of draft skidpad #137 head `938d96c4` with #206
  `f02f206f`, local detached merge `503024c2`: all 16 packages build and
  seven affected suites pass 800 tests with no errors/failures/skips.
- The one Mid-360/stereo/fusion/EKF/safety skidpad attempt failed at the
  180.0 sim-second cap (264.2 wall seconds), still `DRIVING` with acquisition
  holding and the plant stationary at the start. No final metrics CSV was
  emitted, so the scorer reports `no_metrics` and 0/1 pass. Captured camera
  support is below the acquisition requirements; replay of the observed
  orange points rejects the entry corridor. The existing autocross reference
  also places the valid skidpad start 10.586 m off its polyline. No acceptance
  repeats were attempted with these blockers. Raw output is preserved in
  both containers at `2026-11-08-issue206-skidpad-smoke`.
