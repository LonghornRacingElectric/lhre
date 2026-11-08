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
