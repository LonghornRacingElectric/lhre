# MVP software gate, 2026-10-28

## Question

Does the full autonomy stack, with no ground-truth shortcuts, pass every
gate run in Gazebo? This is the 10/28 Software MVP
([#109](https://github.com/LonghornRacingElectric/lhre/issues/109)). The
stack, the run matrix and the pass lines are in the
[pass criteria](../../docs/plans/mvp-pass-criteria.md).

**Data:** bags, launch logs and metrics for every run are on SharePoint:
Test Data > 2026-10-28-mvp-gate (upload pending).

## Result

**Pass: 18 of 18.** The gate ran on 11/1, four days after the 10/28 target,
on `main` b5cb8101 in the Mac container. Every autocross lap and
acceleration finish completed, with 0 off course, 0 cones hit, max CTE
0.26 m, max EKF error 0.08 m, laps at 1.00 times the truth stack and stops
1.1 to 2.0 m past the line. No run was repeated and safety never tripped.
Camera fusion and skidpad were waived, as the pass criteria allow. Fusion
has since passed too: with #198 on, `main` 33eebc9 passed 18 of 18 the same
day, so the camera waiver is lifted and only skidpad stays waived.

The fault matrix, recorded alongside, caught 52 of 54 injected faults with
no false trip in 3.3 km of clean laps. The steering-freeze check is the gap.

The same stack passed 18 of 18 in dry runs #2 to #4, and again on the
real Mid-360 scan pattern. Dry run #1 passed 17 of 18, and each failure
came from the harness or the scoring, not the stack.

## Stack under test

`perception:=lidar estimator:=ekf safety:=true`, otherwise launch defaults,
the same for every run. CPU-rendered machines add
`safety_lidar_timeout_s:=1.5`: Gazebo still leaves LiDAR gaps up to about
0.7 s there with the cameras off (#160), against the 0.3 s default.

Waived per the pass criteria:

- **Camera fusion (#100):** not merged at the time. Cones were unlabeled,
  and the track builder paired them with its default `boundary` strategy.
  Lifted after the run: see [Camera fusion](#camera-fusion).
- **Skidpad (#114):** not in the sim yet, so the gate is 18 runs, not 21.

### What the stack takes from truth

Under `estimator:=ekf` the bridge's truth odometry and TF move to
`/lhr/vehicle/odom_truth` and `/tf_truth`. The EKF owns
`/lhr/vehicle/odom` and `map -> base_link`, and the LiDAR detector places
cones with the EKF pose. No perception, planning, control, mission or
safety node subscribes to a truth topic or reads the world file. What the
simulator still hands the stack:

| Input | Source | Note |
| --- | --- | --- |
| EKF first pose | First `/lhr/vehicle/odom_truth` sample, once | Allowed: the world picks the spawn, and the real car stages at a known spot |
| Wheel speeds | `inertial_sim` differentiates the truth pose, then adds 0.05 rad/s noise | A sensor model. Speeds follow body motion, so wheel slip never shows up |
| IMU | Gazebo's physics IMU, bridged as is | No noise or bias ([#171](https://github.com/LonghornRacingElectric/lhre/issues/171)), so the EKF error below is optimistic |
| LiDAR cloud | Gazebo `gpu_lidar` with 2 cm range noise ([#161](https://github.com/LonghornRacingElectric/lhre/pull/161)) | A sensor model: VLP-16 grid, no dropout or angular error |
| LiDAR cloud, `lidar_source:=mid360` | `lhr_lidar_sim` casts the vendor Mid-360 pattern from the truth pose against the world file's cones ([#133](https://github.com/LonghornRacingElectric/lhre/pull/133)) | A sensor model, opt-in, used only in dry run 4: 2 cm range noise, no dropout by default, and the world may hold only flat ground and cones |
| LiDAR mount | `vehicle.yaml` | The same file the car uses |
| Track width | `track_builder` pairs at the generator's nominal width (3.5 m, or the acceleration lane), 1 m tolerance | A course prior, not a measurement |
| Scoring | `metrics_node` reads the world file and `/lhr/vehicle/odom_truth` | Allowed: output only |

The IMU and wheel-slip gaps are fidelity, not shortcuts, so they do not
block the gate. They mean the EKF has an easier job here than on the car.

## Gate run

`main` b5cb8101, 11/1, Mac container, all 23 runs in 19 minutes. Per-run
numbers are in [`results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-10-28-mvp-gate/results.csv).

| Event | Runs | Passed | Max CTE | Max EKF error | Stop after the line | Lap / truth stack |
| --- | --- | --- | --- | --- | --- | --- |
| Autocross, seeds 1 to 5 | 15 | 15 | 0.26 m | 0.08 m | 1.1 to 2.0 m (limit 30 m) | 1.00 |
| Acceleration | 3 | 3 | 0.005 m | 0.05 m | 1.5 m (limit 75 m) | n/a |
| Skidpad | waived (#114) | | | | | |

Every run had 0 off course, 0 cones hit and a healthy stack. The
truth-stack baselines lapped in 33.3 to 34.6 s.

## Camera fusion

Fusion ([#198](https://github.com/LonghornRacingElectric/lhre/pull/198))
merged after the gate run, so the gate was repeated with it on: the gate
stack plus `enable_stereo_camera:=true enable_camera_lidar_fusion:=true`,
`main` 33eebc9, 11/1, Mac container. Fusion rejects `perception:=sim`, so
the lap-time baselines are the gate run's own. Per-run numbers are in
[`fusion_results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-10-28-mvp-gate/fusion_results.csv).

| Event | Runs | Passed | Max CTE | Max EKF error | Stop after the line | Lap / truth stack |
| --- | --- | --- | --- | --- | --- | --- |
| Autocross, seeds 1 to 5 | 15 | 15 | 0.26 m | 0.07 m | 1.4 to 2.8 m | 1.00 |
| Acceleration | 3 | 3 | 0.005 m | 0.05 m | 1.2 to 1.5 m | n/a |

An earlier run on #198's head 19ebc7d2 passed autocross 15 of 15 but
failed acceleration: cones the camera called orange went to an
`orange_cones` namespace the track builder does not read, so the orange
finish and exit-lane cones vanished. #198 now keeps the LiDAR's namespace
for orange cones, and both events pass on the merged code.

## Dry runs

| Dry run | Date | Code | Result | Found |
| --- | --- | --- | --- | --- |
| 1 | 10/24 | `main` + #132 + #161, Mac container | 16 of 18, then 17 of 18 on repeat | [#164](https://github.com/LonghornRacingElectric/lhre/issues/164): safety tripped on a stale stream in READY at startup, 2 of 20 starts. [#165](https://github.com/LonghornRacingElectric/lhre/issues/165): metrics compared each estimate with the latest truth rather than truth at its stamp, reporting 0.59 m for a real 0.18 m. [#166](https://github.com/LonghornRacingElectric/lhre/issues/166), found while fixing #164: pursuit could move before the mission manager's first status |
| 2 | 10/24 | `main` f2fea986 (#163, #167, #168), Mac container | 18 of 18, no reruns, no safety trips | Nothing new. The 23 runs took 20 minutes |
| 3 | 10/24 | `main` 0d1ca009 (#160: no ZED rendering with stereo off), Mac container | 18 of 18, no reruns, no safety trips | Nothing new. LiDAR on the Mac went from about 6.3 to 7.7 Hz |
| 4 | 10/25 | [#133](https://github.com/LonghornRacingElectric/lhre/pull/133) 3a1ddeff, Mac container | 18 of 18 on the default grid, and 18 of 18 with `lidar_source:=mid360` | Nothing new. The vendor Mid-360 pattern drives as well as the VLP-16 grid |

Fixes: #167 holds READY until the safety heartbeat is steady and keeps
pursuit stopped until DRIVING (#164, #166). #168 scores the estimate
against truth interpolated at its own stamp (#165).

## Seed 1 at 12 m/s

Not gating. The gate stack with `v_max:=12` on seed 1, the old
[#93](https://github.com/LonghornRacingElectric/lhre/issues/93) failure, 3
runs on 11/1 plus a truth-stack baseline at the same limit. All 3 passed:
laps 23.9 to 24.1 s against 33.6 s at the default 5 m/s, top speed 8.9 m/s
and mean 6.5 m/s (the course never lets it reach 12), max CTE 0.25 m, max
EKF error 0.08 m, stops 4.0 to 4.2 m past the line. The truth stack lapped
in 26.2 s, so the gate stack ran 0.91 times its time.

## Fault matrix

Recorded, not gating
([#117](https://github.com/LonghornRacingElectric/lhre/issues/117)). Each
`lhr_safety` check gets its fault mid-drive on the oval at `v_max` 3, 5 and
8, twice each, plus 5 clean autocross laps for false trips: 59 runs in 71
minutes on 11/1, `main` 01d8f86 in the Mac container, the gate stack plus
`enable_metrics:=false` (see the
[fault tools](../tools/fault_injection/README.md)). Per-run numbers are in
[`fault_results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-10-28-mvp-gate/fault_results.csv).

**52 of 54 faults were caught by the expected check, every caught run
stopped within the PDR formula, and the clean laps drove 3.3 km with no
false trip.** The two misses are both steering freezes at the top tier.

| Fault | Should trip | Caught | Trip time | Max stop | Max formula |
| --- | --- | --- | --- | --- | --- |
| `ekf_kill` | `ODOM_STALE` | 6/6 | 0.28 s | 4.0 m | 4.9 m |
| `controller_kill` | `CMD_STALE` | 6/6 | 0.28 s | 3.3 m | 5.0 m |
| `controller_pause` | `CMD_STALE` | 6/6 | 0.28 to 0.31 s | 3.3 m | 4.9 m |
| `odom_nan` | `EKF_DIVERGED` | 6/6 | 0.03 s | 2.4 m | 3.6 m |
| `odom_spike` | `EKF_DIVERGED` | 6/6 | 0.03 s | 2.3 m | 3.6 m |
| `cmd_nan` | `CMD_INVALID` | 6/6 | 0.08 s | 3.1 m | 3.8 m |
| `geofence` | `GEOFENCE` | 6/6 | 0.05 s | 2.5 m | 4.9 m |
| `lidar_drop` | `LIDAR_STALE` | 6/6 | 1.2 to 1.4 s | 10.8 m | 11.0 m |
| `steer_freeze` | `STEER_TRACKING` | 4/6 | 3.0 to 4.5 s | 17.0 m | 18.3 m |

What the numbers do and do not show:

- **Steering freeze is the gap** ([#208](https://github.com/LonghornRacingElectric/lhre/issues/208)). `STEER_TRACKING` trips once
  the yaw rate misses the commanded steering's by half (at least
  0.15 rad/s) for 1 s, so a freeze near the angle pursuit wants goes unseen
  until the course bends away from it. Caught runs took 3.0 to 4.5 s. At the
  8 m/s tier one run never tripped, and the other tripped `EKF_DIVERGED`
  after 2.1 s instead.
- **The formula takes the measured trip time,** so a slow check still
  passes it: a 4.5 s trip at 3 m/s is allowed 15 m. Trip time is the number to watch, and
  [#200](https://github.com/LonghornRacingElectric/lhre/issues/200) picks
  the heartbeat timeout from it.
- **LiDAR drop measures the Mac's 1.5 s timeout,** not the check. At the
  0.3 s default it would trip in about 0.3 s.
- **The 8 m/s tier faulted at 5.3 m/s** (6.3 m/s for the geofence, which
  trips early in the lap): the oval's corners cap the speed
  ([#207](https://github.com/LonghornRacingElectric/lhre/issues/207)).
- **Stops are optimistic.** The sim's wheels follow the stop command
  exactly until the brake model lands
  ([#199](https://github.com/LonghornRacingElectric/lhre/issues/199)).

## Method

One machine runs the whole matrix with the gate tools, unattended:

```bash
cd autonomy/ros2
./scripts/build.sh
EXTRA="safety_lidar_timeout_s:=1.5" ../testing/tools/gate/run_gate.sh
```

Each autocross seed gets one truth-stack baseline
(`perception:=sim estimator:=truth`) for the lap-time check. A run ends a
few seconds after the mission reaches FINISHED or EMERGENCY, or at 180 wall
seconds. `score_gate.py` writes `results.csv` and `summary.md`. See the
[gate tools](../tools/gate/README.md) for every option.

## Files

- `results.csv`: `score_gate.py`'s per-run table from the 11/1 gate run.
- `fusion_results.csv`: the same table for the 11/1 camera fusion runs.
- `fault_results.csv`: `analyze_run.py`'s per-run table from the 11/1 fault matrix.
