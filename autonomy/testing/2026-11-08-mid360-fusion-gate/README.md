# Mid-360 and camera fusion development checks, 2026-11-08

## Question

Does the gate runner select the combined Mid-360/stereo/fusion stack while
preserving truth baselines, and can that stack drive in the Mac development
container? This checks the implementation of
[#206](https://github.com/LonghornRacingElectric/lhre/issues/206), not the
official [Sim v2 acceptance](../../docs/plans/sim-v2-pass-criteria.md).

## Result

**Configuration and automated checks pass. Default-timeout driving fails on
this development machine.** The seed-1 gate run tripped `LIDAR_STALE` in
`DRIVING` at the unchanged 0.3 s timeout. Before the trip it recorded zero
cone hits, zero off-course events, max CTE 0.2453 m and max EKF error
0.0508 m. The sim/truth baseline completed its lap in 33.6264 s and stopped
1.95 m past the finish. The failed gate run remains in the results.

The full colcon run reports 1,056 tests, zero errors/failures and two skips:
the native-FMU launch/relaunch and compiled-aero tests require a built FMU
via `LHR_TEST_FMU`. All 286 Gazebo tests pass, including runner planning,
complete command provenance, both cloud sources/estimators, actual planner
startup, camera-loss fallback/recovery and orange-cone geometry. Strict
MkDocs validation passes. Independent code review found no implementation
issues.

With the documented Mac-only 1.5 s LiDAR timeout, seed 1 passes the existing
scorer. Acceleration completes with no cone hits, off-course events or safety
trip, but fails `stops`: after first reaching standstill it rolls at up to
0.69 m/s, above the scorer's 0.5 m/s limit. This is a retained failure; its
cause has not been isolated. The diagnostic passes **one of two** gate runs.

| Setting | Run | Outcome | Max CTE | Max EKF error | Stop distance | Max speed after first stop | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Default 0.3 s | Autocross seed 1 | Emergency, `LIDAR_STALE` | 0.2453 m | 0.0508 m | Not reached | Not reached | Fail |
| Diagnostic 1.5 s | Autocross seed 1 | Lap | 0.2487 m | 0.0506 m | 1.44 m | 0.00 m/s | Pass |
| Diagnostic 1.5 s | Acceleration | Mission finished | 0.0064 m | 0.0490 m | 1.97 m | 0.69 m/s | Fail: did not stay stopped |

All three rows have zero cone hits and off-course events. The two baselines
and all gate rows are retained in
[`results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-08-mid360-fusion-gate/results.csv).
Neither campaign establishes full course acceptance or gate-machine rates.

## Method and provenance

- **Code:** `f3a17796` on `celina/206-mid360-fusion-gate-defaults`. Later
  edits in this campaign are documentation only.
- **Machine:** Apple Silicon Mac, Colima arm64 VM (6 CPUs, 8 GiB), existing
  `lhr-autonomy-dev` ROS 2 Jazzy/Gazebo Harmonic container, software rendering.
  This is not red-mesa. The default-timeout campaign partly overlapped the
  BobSim unit tests in another container; these runs establish no standalone
  performance result.
- **Plant:** Gazebo's own dynamics, not the FMU. Headless: `gui:=false
  rviz:=false`. All run commands are saved in their raw `args.txt` files.
- **Gate stack:** `perception:=lidar estimator:=ekf safety:=true
  lidar_source:=mid360 enable_stereo_camera:=true enable_camera_lidar_fusion:=true`.
- **Baseline:** `perception:=sim estimator:=truth lidar_source:=gazebo`,
  with stereo and fusion explicitly disabled. The scorer retains the MVP
  truth-baseline lap reference and wall-time settling.
- **Default-timeout command:** from `autonomy/ros2`, after building and
  sourcing ROS and the installed overlay:

```bash
SEEDS=1 REPEATS=1 ACCEL_REPEATS=0 SKIDPAD_REPEATS=0 BASELINE=1 RUN_S=120 \
  OUT="$HOME/lhr-test-data/2026-11-08-issue206-dev" \
  ../testing/tools/gate/run_gate.sh
```

The diagnostic used the same command with `ACCEL_REPEATS=1`, a fresh
`OUT="$HOME/lhr-test-data/2026-11-08-issue206-diagnostic"` and
`EXTRA="safety_lidar_timeout_s:=1.5"`. The full unit suite had finished before
the diagnostic started. No application parameters were tuned between its
courses; only this documented development timeout differs from defaults.

Three short `ros2 topic hz` observers received LiDAR and both image streams,
but overlapped teardown and did not establish a sustained driving window or
real-time factor. Those logs do not satisfy the gate-machine rate requirement.
The [gate tools](../tools/gate/README.md#sensor-rate-evidence) describe the
required measurement method, including explicit wall-time measurement.

## Fallback limitations reproduced

Pure checks confirm both known algorithm risks, with no perception algorithm
changes under this issue:

- [#85](https://github.com/LonghornRacingElectric/lhre/issues/85): forty
  cones on a 3.0 m straight, 2 m apart along each side, with votes +1/-0.9,
  all become left-side labels; 20 of 40 are incorrect.
- [#86](https://github.com/LonghornRacingElectric/lhre/issues/86): after
  seeding `[LEFT, RIGHT]`, ten independent `[RIGHT, LEFT]` proposals leave
  the original labels unchanged. This establishes label locking, not
  closed-loop performance under motion.

## Data and remaining acceptance

Raw bags, launch/recorder logs, exact arguments and short rate-monitor logs
remain under `~/lhr-test-data/2026-11-08-issue206-dev/` and
`~/lhr-test-data/2026-11-08-issue206-diagnostic/` in `lhr-autonomy`.
SharePoint upload is pending; no raw recordings are committed.

Red-mesa access was unavailable. Its measured rates, live camera
interruption/recovery and full course campaign remain required. Skidpad is
still absent on this branch (#114), and FMU/gate timing/matrix/scorer work
belongs to the linked integration issues. No skidpad waiver or relaxed Mac
timeout counts as completion of #206.
