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
Camera fusion and skidpad are waived, as the pass criteria allow.

The same stack passed 18 of 18 in dry runs #2 to #4, and again on the
real Mid-360 scan pattern. Dry run #1 passed 17 of 18, and each failure
came from the harness or the scoring, not the stack.

## Stack under test

`perception:=lidar estimator:=ekf safety:=true`, otherwise launch defaults,
the same for every run. CPU-rendered machines add
`safety_lidar_timeout_s:=1.5`: Gazebo still leaves LiDAR gaps up to about
0.7 s there with the cameras off (#160), against the 0.3 s default.

Waived per the pass criteria:

- **Camera fusion (#100):** not merged. Cones are unlabeled, and the track
  builder pairs them with its default `boundary` strategy.
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
