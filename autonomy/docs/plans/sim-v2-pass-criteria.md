# Sim v2 pass criteria (11/14)

**Status: draft, 2026-11-07.** Tracks
[#201](https://github.com/LonghornRacingElectric/lhre/issues/201). The work
feeding it is the
[Sim v2 milestone](https://github.com/LonghornRacingElectric/lhre/milestone/2),
due at the 11/14 lock-in.

The [MVP gate](mvp-pass-criteria.md) passed 18 of 18 on a sim that flatters
the stack: wheels that stop exactly, a noise-free IMU, the VLP-16 grid instead
of the Mid-360, unlabeled cones, no skidpad, and courses that are shorter and
gentler than any legal autocross. Sim v2 reruns the gate with those removed.
This page fixes what the gate is before the work lands, so 11/14 is a pass or
a fail.

## What changed from the MVP

| | MVP (10/28) | Sim v2 | Why |
| --- | --- | --- | --- |
| Plant | Gazebo's own dynamics | Orion's BobLib FMU drives the Gazebo car; Gazebo only renders sensors ([#202](https://github.com/LonghornRacingElectric/lhre/issues/202), [#203](https://github.com/LonghornRacingElectric/lhre/issues/203)) | Tires, brakes, motor and actuator response come from the real car's model |
| Braking | Wheels follow the speed command exactly | Regen-only service braking and the spring-brake EBS on the FMU's inputs ([#199](https://github.com/LonghornRacingElectric/lhre/issues/199)) | Stop distances mean something |
| IMU and wheel speeds | Gazebo's noise-free IMU; wheel speeds from the truth pose, no slip | From the FMU, with noise and bias ([#205](https://github.com/LonghornRacingElectric/lhre/issues/205), [#171](https://github.com/LonghornRacingElectric/lhre/issues/171)) | The EKF gets the job it will have on the car |
| LiDAR | VLP-16 grid | `lidar_source:=mid360`, the vendor scan pattern ([#206](https://github.com/LonghornRacingElectric/lhre/issues/206)) | The car's sensor |
| Cone color | Waived | Camera fusion on ([#206](https://github.com/LonghornRacingElectric/lhre/issues/206)) | Passed 18 of 18 on 11/1; now the default |
| Autocross courses | Seeds 1 to 5 of a perturbed circle: 155 to 186 m, 7 m minimum radius, no long straights | Rules-compliant courses, extreme but legal scenarios and real competition layouts, all gating ([#211](https://github.com/LonghornRacingElectric/lhre/issues/211), [#212](https://github.com/LonghornRacingElectric/lhre/issues/212), [#213](https://github.com/LonghornRacingElectric/lhre/issues/213)) | The MVP seeds were easier than any legal course, and real events do not follow our generator |
| Skidpad | Waived | In the gate ([#114](https://github.com/LonghornRacingElectric/lhre/issues/114)) | Third dynamic event |
| Lap time reference | 1.5 times the truth stack on the same seed | 1.5 times a reference lap computed from the course itself | The truth stack laps 11 to 20% slower on red-mesa than on the Mac, and fails outright on 2 of 3 rules-compliant seeds |
| LiDAR timeout | 0.3 s, or 1.5 s on CPU-rendered machines | 0.3 s, no exception | The gate runs on a GPU machine ([#204](https://github.com/LonghornRacingElectric/lhre/issues/204)) |
| Fault matrix | Recorded, not gating | Gating, with fixed trip-time limits | The milestone's definition of done |

## The stack under test

No autonomy node may read ground truth. Truth is for scoring only.

| Piece | Setting |
| --- | --- |
| Plant | `plant:=fmu` |
| Pose | `estimator:=ekf` |
| Cones | `perception:=lidar lidar_source:=mid360` |
| Cone color | `enable_stereo_camera:=true enable_camera_lidar_fusion:=true` |
| Safety | `safety:=true`, default `safety_lidar_timeout_s` (0.3 s) |
| Parameters | Launch defaults, identical for every run. Settings that #202 to #206 make the defaults need no extra arguments |

Allowed truth, and only this:

- The EKF seeds its first pose from truth. The car stages at a known spot at
  competition.
- `lhr_metrics`, the world's reference centerline and the gate scorer, for
  scoring.
- Sensor models built from sim state: the Mid-360 caster (truth pose and the
  world's cones), the FMU's IMU and wheel speeds with modeled noise and bias,
  and Gazebo's rendered cameras.

## Run matrix

| Set | Courses | Repeats | Runs |
| --- | --- | --- | --- |
| Autocross, rules-compliant (#211) | `rules_autocross` seeds 1 to 5 | 3 | 15 |
| Acceleration | rules layout | 3 | 3 |
| Skidpad (#114) | rules layout | 3 | 3 |
| Extreme but legal (#212) | the 7 fixed scenarios | 1 | 7 |
| Real layouts (#213) | `amz_fsg`, `amz_fsi` | 1 | 2 |
| MVP regression | `autocross` seeds 1 to 5 | 1 | 5 |
| **Gate total** | | | **35** |

The 21-run core keeps the MVP's shape on harder courses. The 7 extreme
scenarios each push one rules limit: 3 m width, an 80 m straight into a
minimum-diameter hairpin, a decreasing-radius turn, one course with a slalom
and a chicane, a doubled-back section, a 500 m lap, and one that combines
several limits. The MVP seeds stay as a regression
check, once each.

The real layouts are AMZ's FSG and FSI cone files, imported unchanged. They
are the closest thing we have to a course someone else designed, and they do
not respect our generator: FSG is 305 m with a 4.01 m tightest centerline
radius and 2.95 m narrowest clear width, FSI is 214 m with 4.12 m and 2.82 m,
both a little inside our rules checker's limits, and FSG's gates are spaced
too far apart for the planner's default 4 m step. They gate anyway, on the
same launch defaults as every other run. The stack has to handle what a real
course hands it; a course that needs its own parameters is a stack gap, not a
course problem
([#225](https://github.com/LonghornRacingElectric/lhre/issues/225)). The launch flag that acknowledges a flagged layout
(`allow_noncompliant_track:=true`) is allowed, since it changes no stack
setting.

## What every run must show

| Check | Threshold | Change from the MVP |
| --- | --- | --- |
| Completes | `lap` (autocross) or `mission_finished` (acceleration, skidpad) | None |
| Stops after finish | Speed 0 and `FINISHED` within 30 m (autocross), 75 m (acceleration), 25 m (skidpad) of the finish, and stays stopped | None, but now on modeled brakes (#199) |
| Off course | 0 | None |
| Cones hit | 0 | None |
| Max cross-track error | At most 1.0 m from the reference | None. On a 3 m course a cone contact registers first, at about 0.75 m |
| Max EKF position error | At most 0.5 m against truth | None, but now with a noisy IMU (#171) on laps up to 500 m |
| Lap time | At most 1.5 times the reference lap | New reference, see below |
| Health | No node exits, no `timeout` outcome, no safety trip | None |
| FMU pose lag | Rendered sensor pose at most 100 ms behind the FMU state | New (#203): one LiDAR frame at 10 Hz |

**The reference lap** is the time to drive the world's reference centerline
under the launch's own speed limits (`v_max`, the lateral-acceleration limit
and the longitudinal limits), computed by the scorer from the course alone.
It depends only on the course and the parameters, so it is the same on every
machine and cannot fail the way the truth stack does. The gate scorer has to
compute it before the 11/14 run.

The gate passes when **all 35 runs pass**. A failed run gets an issue in the
milestone and is repeated after the fix, never dropped.

## Fault matrix

Gating in Sim v2, and separate from the 35 gate runs: each of the 9 fault
cases at `v_max` 3, 5 and 8 m/s, twice each (54 runs), plus 5 clean laps on
`rules_autocross` seeds 1 to 5, so 59 runs, on the FMU plant with the stack
above. Run with the [fault tools](../../testing/tools/fault_injection/README.md).

| Check | Threshold |
| --- | --- |
| Caught | Every fault trips its expected check, never a different one |
| Trip time, stale streams (`ODOM_STALE`, `CMD_STALE`, `LIDAR_STALE`) | At most the check's timeout plus 0.2 s |
| Trip time, invalid or diverged values (`EKF_DIVERGED`, `CMD_INVALID`, `GEOFENCE`) | At most 0.2 s |
| Trip time, `STEER_TRACKING` | At most 1.0 s ([#208](https://github.com/LonghornRacingElectric/lhre/issues/208)) |
| Stop distance | Within d = v(T + 0.2 s) + v²/2a, with T the check's trip-time limit above (not the measured trip time) and a the spring brake's 6 m/s² floor |
| Top tier speed | The 8 m/s tier is at 7.5 m/s or more when the fault fires ([#207](https://github.com/LonghornRacingElectric/lhre/issues/207)) |
| Clean laps | 0 false trips |

Using the trip-time limit instead of the measured trip time closes the gap
the 11/1 matrix showed: a slow check got a longer allowed stop and still
passed. Two checks have no fault case yet: IMU and wheel-speed dropouts
([#126](https://github.com/LonghornRacingElectric/lhre/issues/126)) and a dead
`mission_manager` ([#127](https://github.com/LonghornRacingElectric/lhre/issues/127)).
Each joins the matrix with its check.

## Gaps in the sim

- The FMU drives a rigid, planar sensor rig: suspension travel, roll and pitch
  stay inside the model and are not rendered. Cone contacts are geometric
  checks, not modeled impacts.
- Every gate course has to fit inside the safety geofence, a fixed radius
  around where the car arms. The 150 m default may not reach the 500 m extreme
  scenario or the real layouts, so it is being sized to the largest course
  ([#229](https://github.com/LonghornRacingElectric/lhre/issues/229)).
- Our rules checker is stricter than at least two real layouts. Generated
  courses stay inside it; the stack is held to the real layouts too.
- Off course and cone contact use the conservative car box from the MVP,
  stricter than the rules.

## Where it runs

- **Gate:** red-mesa, native Ubuntu 24.04 with an RTX 3060, headless, through
  the [gate tools](../../testing/tools/gate/README.md). The MVP fusion gate
  passed 18 of 18 there at the default timeout on 11/4. The Mac container is
  for development only; its 1.5 s LiDAR timeout does not count for the gate.
- **Results:** `autonomy/testing/2026-11-14-sim-v2-gate/` per
  [testing/README.md](../../testing/README.md). Raw recordings go to
  SharePoint.

No waivers. If something misses 11/14, the gate fails, the miss gets an
issue, and the write-up reports what passed.

## Open questions

- Does the full v2 stack (FMU, Mid-360 caster and both cameras) hold the
  0.3 s LiDAR timeout on red-mesa? Its CPU, not its GPU, was the limit on
  11/4.
- Does 0.5 m of EKF error hold on 500 m laps once IMU noise and bias land,
  with no GNSS?
- Should the rules checker loosen to admit the real layouts' 4.0 m radius
  and 2.8 m width, so generated courses can be as tight as real ones?
