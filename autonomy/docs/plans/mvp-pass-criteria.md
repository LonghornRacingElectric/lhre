# Software MVP pass criteria (10/28)

**Status: approved, 2026-10-18.** Tracks
[#108](https://github.com/LonghornRacingElectric/lhre/issues/108). The gate
run itself is
[#109](https://github.com/LonghornRacingElectric/lhre/issues/109), and the
work feeding it is the
[Software MVP milestone](https://github.com/LonghornRacingElectric/lhre/milestone/1).

The MVP is "a full simulated lap with no shortcuts" (Autonomy Plan, Notion).
This page turns that into a run matrix and thresholds, so 10/28 is a pass or
a fail and not a judgment call. Thresholds come from the FSAE driverless
penalties in [fsae-track-and-events.md](../rules/fsae-track-and-events.md)
where the rules give one.

## The stack under test

No autonomy node may read ground truth. Truth is for scoring only.

| Piece | MVP setting | Why |
| --- | --- | --- |
| Pose | `estimator:=ekf` | Ground-truth odometry is the biggest shortcut left ([#101](https://github.com/LonghornRacingElectric/lhre/issues/101)) |
| Cones | `perception:=lidar` with range noise | Sim cones come straight from trackgen |
| Cone color | Camera + LiDAR fusion ([#100](https://github.com/LonghornRacingElectric/lhre/issues/100)) | Sim colors are free; real ones are not |
| Plant | Gazebo | Camera and LiDAR physics live there. BobDyn ([#103](https://github.com/LonghornRacingElectric/lhre/issues/103)) is optional |
| Safety | `safety:=true` | READY waits for a steady heartbeat ([#167](https://github.com/LonghornRacingElectric/lhre/pull/167)); a trip fails the run |
| Parameters | Launch defaults, identical for every run | Per-seed tuning would pass the gate and fail the car |

One exception to the defaults: CPU-rendered machines add
`safety_lidar_timeout_s:=1.5` to every run. Gazebo leaves LiDAR gaps up to
about 0.7 s there, against the 0.3 s default meant for the real Mid-360.

Allowed truth, and only this:

- The EKF seeds its first pose from truth. At competition the car stages
  at a known spot (6 m behind the start line for autocross), so this
  matches reality.
- `lhr_metrics` and the trackgen reference centerline, for scoring.
- Sensor models built from sim state: `inertial_sim` wheel speeds (truth
  pose plus noise, no slip) and Gazebo's physics IMU (no noise or bias yet,
  [#171](https://github.com/LonghornRacingElectric/lhre/issues/171)). Both make the EKF error optimistic.

## Run matrix

| Event | Tracks | Repeats | Runs |
| --- | --- | --- | --- |
| Autocross | trackgen seeds 1 to 5 | 3 each | 15 |
| Acceleration | rules layout ([#113](https://github.com/LonghornRacingElectric/lhre/issues/113)) | 3 | 3 |
| Skidpad | rules layout ([#114](https://github.com/LonghornRacingElectric/lhre/issues/114)) | 3 | 3 |

Seed 1 stays in on purpose: it was the known failure
([#93](https://github.com/LonghornRacingElectric/lhre/issues/93)), and the
gate dry runs are the first evidence it laps. Repeats
exist because message interleaving differs run to run; a stack that passes
once and fails the next time has not passed.

## What every run must show

Scored against the fixed trackgen reference
([#94](https://github.com/LonghornRacingElectric/lhre/issues/94)), never the
planner's own centerline, and timed on sim time.

| Check | Threshold | Source |
| --- | --- | --- |
| Completes | `outcome` is `lap` (autocross, confirmed by the reference finish gate) or `mission_finished` (accel, skidpad: the mission's own finish, judged on the EKF pose) | Rules: completion points |
| Stops after finish | Speed 0 and mission `FINISHED` within 30 m (autocross), 75 m (accel), 25 m (skidpad) of the finish line, and stays stopped | Rules: finish distances |
| Off course | 0 | Rules: OC is a DNF in accel and skidpad, +10 s in autocross |
| Cones hit | 0 | Rules: DOO is +2 s per cone |
| Max cross-track error | ≤ 1.0 m from the reference | Minimum track width is 3 m, so cones sit about 1.5 m from the centerline |
| Max EKF position error | ≤ 0.5 m against truth | Above this the cone map smears |
| Lap time | ≤ 1.5 × the same seed on the truth stack (`estimator:=truth perception:=sim`) | Catches a stack that only passes by crawling |
| Health | No node exits, no `timeout` outcome, no safety trip | |

The gate passes when **all 21 runs pass**. A failure gets an issue in the
milestone and the run is repeated after the fix, never dropped.

### Recorded, not gating

| Check | Target | Source |
| --- | --- | --- |
| Injected faults stop the car | Stale LiDAR, EKF divergence, geofence exit and a blocked steering command each trip `safety_node`, and the car stops within d = v(T + 0.2 s) + v²/2a | PDR safety loop ([#116](https://github.com/LonghornRacingElectric/lhre/issues/116), [#117](https://github.com/LonghornRacingElectric/lhre/issues/117)) |

Reported with the gate run. It becomes a gate at the 12/2 bench rig, once
the hardware half of the loop exists.

### Gaps in today's metrics

- Off course and `cones_hit`
  ([#156](https://github.com/LonghornRacingElectric/lhre/pull/156)) check a
  conservative box from `lhr_vehicle`, the body plus every wheel at any
  steering angle, against the cone-center boundary and each cone's base.
  That is stricter than the rules (any part of the box over the cone line,
  not all four wheels out), and on a 3.5 m track a contact trips at about
  0.79 m of CTE, before the 1.0 m CTE row.
- Distance to stop after the finish is measured from the recorded bag by
  the gate scorer (`testing/tools/gate/score_gate.py`), not by metrics.
- Once the mission completes, pursuit ramps the speed command down at
  `max_decel` and Gazebo's wheels follow it exactly, so sim stop distances
  are optimistic until #116 adds a regen and spring-brake model.

## Waivers if something misses 10/28

The Plan's MVP is one full lap. Everything else is upside, so a miss below
is recorded here and does not fail the gate:

- **Camera fusion (#100) not merged:** run the launch defaults (unlabeled
  LiDAR cones, `boundary` pairing; the side classifier stays opt-in) and
  note the waiver.
- **Skidpad (#114) not merged:** autocross and acceleration (18 runs) are
  the gate.
- **BobDyn (#103), Map AutoX
  ([#105](https://github.com/LonghornRacingElectric/lhre/issues/105)),
  Foxglove ([#107](https://github.com/LonghornRacingElectric/lhre/issues/107)):**
  never gating.

No waiver for EKF, LiDAR, or the reference metrics. Those are the MVP.

## Where it runs

- **Gate run (#109):** Gazebo, headless, through the
  [gate tools](../../testing/tools/gate/README.md) on one machine: the Mac
  Docker container for the dry runs, or a native Ubuntu 24.04 machine.
  The full matrix takes about 20 minutes. Results go in
  `autonomy/testing/2026-10-28-mvp-gate/` per
  [testing/README.md](../../testing/README.md): the table and chosen plots.
  Raw recordings go to SharePoint.
- **Regression after 10/28:** #91's `run_headless.sh` on the kinematic
  stack, cheap enough for CI. Whether it gates PRs in `ci.yml` is a
  separate decision.

## Open questions

- The dry runs hold EKF error to 0.07 m, but on a noise-free IMU (#171).
  Does 0.5 m still hold once IMU noise and bias land?
- Five autocross seeds, or more? A run takes under a minute, so more seeds
  are cheap.
- The gate runs on Gazebo only. #91's kinematic plant is the regression
  after 10/28.
