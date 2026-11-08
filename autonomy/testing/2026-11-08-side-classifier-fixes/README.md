# LiDAR side classifier fixes, 2026-11-08

## Question

Do [#85](https://github.com/LonghornRacingElectric/lhre/issues/85) and
[#86](https://github.com/LonghornRacingElectric/lhre/issues/86) prevent narrow
boundary merging and recover wrong initial labels without blocking motion?

## Automated results

The regressions reproduce the original failures and pass with the fixes:

- Full and partially observed 2.8/3.0 m straights with 2 m cone spacing retain
  opposite boundaries, including unknown link endpoints and reversed input
  order. Strong opposing votes also veto aligned gap joins; weak outliers can
  still inherit a boundary's majority.
- Startup scans and duplicate stamps do not seed labels. New sides require
  two independent travelled viewpoints; reversing one requires four. Stale
  fragments cannot advance confirmation from observations on a different
  component. Unobserved cones retain accumulated votes.
- A deliberately wrong accepted right-side label recovers to left with
  sustained correction. Publications retain cone IDs and positions and delete
  the old namespace. The actual planner receives the corrected snapshot.
- A real-detector replay discovers only 25% of eligible cones at each
  viewpoint, on 3 m autocross seeds 1–5. After two laps of viewpoints all
  mapped sides agree with truth and one-to-one midpoint errors stay below
  0.25 m. This is idealized map replay, not a physical sensor accuracy claim.
- Unknown and temporarily wrong inferred labels retain a geometry path.
  Both launchers keep geometry planning in inference-only mode. Gazebo fusion
  retains its existing color filter for camera-priority/fallback output.

The full workspace was built and tested. After correcting new test lint
errors and rerunning the affected packages, aggregate colcon results are
**1,083 tests, zero errors/failures, two skips**. The skipped native-FMU tests
require `LHR_TEST_FMU`. The changed perception and Gazebo packages report
150 and 292 passing tests respectively, including lint. An independent review
found two evidence/bootstrap gaps; each received a failing regression and a
verified fix before the final package run. Strict MkDocs validation passes.

## Driving method and provenance

- **Code:** the source tree committed as `9722bb15`, on
  `celina/206-mid360-fusion-gate-defaults`. Tests started before that local
  commit; no runtime source changed during the comparison.
- **Machine:** Apple Silicon Mac, existing ROS 2 Jazzy/Gazebo Harmonic
  development container in Colima (arm64, 6 CPUs, 8 GiB), software rendering.
  This is not red-mesa. Some baseline/inference-only execution overlapped the
  final unit tests in a separate container; these are functional checks, not
  performance measurements.
- **Isolation:** ROS domain 110 and Gazebo partition
  `side-classifier-fixes-20261108`; no GUI or RViz.
- **Plant/course:** Gazebo plant, committed autocross seed 1, default
  controller/planner settings. Both LiDAR runs use Mid-360, EKF and safety.
- **Development exception:** all runs use the documented Mac
  `safety_lidar_timeout_s:=1.5`. The default 0.3 s gate requirement is unchanged.
- **Comparison:** one sim/truth baseline with cameras off; one LiDAR run with
  `lidar_classify_sides:=true enable_stereo_camera:=false
  enable_camera_lidar_fusion:=false`; one with
  `enable_stereo_camera:=true enable_camera_lidar_fusion:=true` (which enables
  side inference automatically).

A temporary harness reused the existing [gate runner's functions and scorer](../tools/gate/README.md)
with explicit per-run comparison arguments, a fresh output folder and a
180 wall-second cap. It did not modify production gate defaults. Raw
`args.txt` files contain the exact executed commands. Stop/completion, cone,
off-course, CTE, EKF, lap-ratio and health checks use the existing MVP scorer.
No threshold was relaxed beyond the documented development timeout.

## Driving results

**Both comparison runs pass every existing scorer check.** Each completed a
lap, reached `FINISHED`, stayed stopped, hit zero cones and had zero
off-course events or safety trips. Both lap ratios are 1.00 against the
33.6281 s sim/truth baseline.

| Run | Mode | Max CTE | Max EKF error | Stop after finish | Result |
| --- | --- | --- | --- | --- | --- |
| `autocross_s1_r1` | Side inference, cameras/fusion off | 0.2464 m | 0.0578 m | 1.44 m | Pass |
| `autocross_s1_r2` | Stereo and camera fusion | 0.2462 m | 0.0568 m | 1.45 m | Pass |

The baseline and both scored rows are in
[`results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-side-classifier-fixes/results.csv).
This is a two-mode development comparison with a relaxed Mac timeout, not
an official gate pass.

### Acceleration follow-up

Three user-run acceleration repeats on the same runtime source (branch
`2794ec7c`, before integrating #230) **all pass**. All reached
`mission_finished` and `FINISHED`, hit zero cones, stayed on course and
remained stopped after finishing. Stop distances were 1.45, 1.47 and 1.45 m;
maximum speeds after the first stop were 0.09, 0.00 and 0.09 m/s, below the
0.5 m/s limit. Max CTE was 0.0100 m and max EKF error 0.0595 m.

These repeats used Mid-360, stereo/fusion, EKF and safety with the documented
1.5 s Mac LiDAR timeout. The complete rows are in
[`acceleration-repeat-results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-side-classifier-fixes/acceleration-repeat-results.csv).
Raw data remains under `~/lhr-test-data/2026-11-08-165212-accel-check/` in the
development container. The earlier single-run rolling failure was not
reproduced here; its cause was not isolated, so these results do not establish
which change removed it.

After integrating [#230](https://github.com/LonghornRacingElectric/lhre/pull/230),
one additional acceleration smoke run also passes every check: stop 1.45 m,
max CTE 0.0055 m, max EKF error 0.0501 m, zero cones/off-course/safety trips.
The new watcher wrote `reason=finished mission=FINISHED sim_s=30.9
wall_s=53.3`; settling is now five simulation seconds and the run cap also
uses simulation time. This run used the same Mac 1.5 s LiDAR timeout.
Its rows are in
[`sim-time-merge-results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-side-classifier-fixes/sim-time-merge-results.csv),
with raw data at `~/lhr-test-data/2026-11-08-issue206-sim-time-merge/`.

### Five-seed autocross campaign

On branch `874e0a15` after #230 integration, the user ran autocross seeds
1–5 three times each with one sim/truth baseline per seed. **All 15 scored
runs pass every check.** Every run completed a lap, reached `FINISHED`,
stayed stopped, hit zero cones, had zero off-course events and retained a
healthy stack. The worst max CTE was 0.2535 m and max EKF error 0.0763 m;
stop distances ranged from 1.37 to 1.85 m. All lap ratios were 1.00.

The actual arguments confirm Mid-360, stereo/fusion, EKF and safety with the
1.5 s Mac diagnostic timeout. Sim speed ranged from 0.66 to 0.93 times real
time including startup. All 15 gate rows and five completed baselines are in
[`autocross-repeat-results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-side-classifier-fixes/autocross-repeat-results.csv).
Raw bags, logs and exact arguments remain at
`~/lhr-test-data/2026-11-08-170935-autocross-check/` in `lhr-autonomy`.

Together with the acceleration repeats, this completes the local scored
MVP-autocross/acceleration coverage for the combined stack. Live camera
interruption/recovery, skidpad and gate-machine rates/default-timeout checks
remain separate acceptance items.

## Data and limits

Raw bags, logs, exact arguments and the comparison harness remain in
`~/lhr-test-data/2026-11-08-side-classifier-fixes/` inside `lhr-autonomy`.
SharePoint upload is pending; raw recordings are not committed.

These checks do not establish physical-car reliability, all-course gate
acceptance, sustained gate-machine sensor rates or skidpad support. Red-mesa
validation remains deferred at the user's request. The earlier
[default-timeout and acceleration stop failures](../2026-11-08-mid360-fusion-gate/README.md)
remain historical evidence. The acceleration follow-up passes three repeats;
no causal acceleration repair is claimed by these classifier fixes.
