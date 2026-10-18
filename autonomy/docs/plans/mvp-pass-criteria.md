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
| Parameters | Launch defaults, identical for every run | Per-seed tuning would pass the gate and fail the car |

Allowed truth, and only this:

- The EKF seeds its first pose from truth. At competition the car stages
  at a known spot (6 m behind the start line for autocross), so this
  matches reality.
- `lhr_metrics` and the trackgen reference centerline, for scoring.

## Run matrix

| Event | Tracks | Repeats | Runs |
| --- | --- | --- | --- |
| Autocross | trackgen seeds 1 to 5 | 3 each | 15 |
| Acceleration | rules layout ([#102](https://github.com/LonghornRacingElectric/lhre/issues/102)) | 3 | 3 |
| Skidpad | rules layout (#102) | 3 | 3 |

Seed 1 stays in on purpose: it is the known failure
([#93](https://github.com/LonghornRacingElectric/lhre/issues/93)). Repeats
exist because message interleaving differs run to run; a stack that passes
once and fails the next time has not passed.

## What every run must show

Scored against the fixed trackgen reference
([#94](https://github.com/LonghornRacingElectric/lhre/issues/94)), never the
planner's own centerline, and timed on sim time.

| Check | Threshold | Source |
| --- | --- | --- |
| Completes | `outcome` is `lap` (autocross) or `mission_finished` (accel, skidpad), confirmed by the reference finish gate | Rules: completion points |
| Stops after finish | Speed 0 and mission `FINISHED` within 30 m (autocross), 75 m (accel), 25 m (skidpad) of the finish line | Rules: finish distances |
| Off course | 0 | Rules: OC is a DNF in accel and skidpad, +10 s in autocross |
| Cones hit | 0 | Rules: DOO is +2 s per cone |
| Max cross-track error | ≤ 1.0 m from the reference | Minimum track width is 3 m, so cones sit about 1.5 m from the centerline |
| Max EKF position error | ≤ 0.5 m against truth | Above this the cone map smears |
| Lap time | ≤ 1.5 × the same seed on the truth stack (`estimator:=truth perception:=sim`) | Catches a stack that only passes by crawling |
| Health | No node exits, no `timeout` outcome | |

The gate passes when **all 21 runs pass**. A failure gets an issue in the
milestone and the run is repeated after the fix, never dropped.

### Gaps in today's metrics

- `cones_hit` and an OC flag do not exist yet. Both need the vehicle
  footprint from `lhr_vehicle` checked against trackgen cone positions;
  that belongs in #94's rework.
- Distance to stop after the finish line is not recorded.
- [#91](https://github.com/LonghornRacingElectric/lhre/pull/91) already
  moves timing to sim time, weights CTE by distance, and adds an `outcome`
  column, so it lands first.

## Waivers if something misses 10/28

The Plan's MVP is one full lap. Everything else is upside, so a miss below
is recorded here and does not fail the gate:

- **Camera fusion (#100) not merged:** run with the LiDAR side classifier
  for cone sides and note the waiver.
- **Accel and skidpad (#102) not merged:** autocross alone (15 runs) is
  the gate.
- **BobDyn (#103), Map AutoX
  ([#105](https://github.com/LonghornRacingElectric/lhre/issues/105)),
  Foxglove ([#107](https://github.com/LonghornRacingElectric/lhre/issues/107)):**
  never gating.

No waiver for EKF, LiDAR, or the reference metrics. Those are the MVP.

## Where it runs

- **Gate run (#109):** Gazebo, `gui:=false`, run by hand on one Ubuntu
  machine. Results go in `autonomy/testing/2026-10-28-mvp-gate/` per
  [testing/README.md](../../testing/README.md): the table, chosen plots,
  and the script. Raw recordings go to SharePoint.
- **Regression after 10/28:** #91's `run_headless.sh` on the kinematic
  stack, cheap enough for CI. Whether it gates PRs in `autonomy.yml` is a
  separate decision.

## Open questions

- Is 0.5 m EKF error achievable on autocross without GNSS, or should it be
  a recorded number instead of a gate?
- Five autocross seeds, or more? Each Gazebo run is roughly a minute.
- Does the gate run on the Gazebo plant only, or also on #91's kinematic
  plant with the Mid-360 LiDAR model?
