# FMU-driven Gazebo on red-mesa, 2026-11-07

## Question

Can Orion's BobLib FMU drive the Gazebo car
([#202](https://github.com/LonghornRacingElectric/lhre/issues/202)) fast and
reliably enough for the 11/14 Sim v2 gate, on red-mesa at the default 0.3 s
LiDAR timeout? This tested
[#221](https://github.com/LonghornRacingElectric/lhre/pull/221) and
[#222](https://github.com/LonghornRacingElectric/lhre/pull/222) before review.

**Data:** bags, launch logs and timing output are on red-mesa under
`~/lhr-test-data/` (SharePoint upload pending).

## Result

**Not yet.** With #222's pacing the FMU drives whole courses: autocross seed 1
lapped in 35.2 s and acceleration finished, both with 0 cones and 0 off course.
Four things stand between that and the gate:

- **Speed.** Each 20 ms FMU step averages 125 to 140 ms in the full stack, so
  the sim runs at about one sixth of real time. By a rough estimate, the gate
  and fault matrix would take about 9 hours on one machine.
- **Half of it is the FMU itself.** Alone, with no Gazebo or stack, it averages
  about 68 ms per step.
- **Parallel runs need separate machines.** Two or three full-stack runs at once
  on red-mesa slowed each FMU to 180 to 280 ms per step, and every autocross run
  tripped `LIDAR_STALE` before driving.
- **The car never quite stops.** Braking fades near zero, so both completed runs
  were still at 0.1 to 0.2 m/s when recording ended and scored "never stopped".

#221 on its own does not work on a fast machine: Gazebo outruns the FMU, pose
lag grows without bound, and the FMU crashes on pull-away.

## Setup

- **Machine:** red-mesa, an ASUS ROG Zephyrus G14 with a Ryzen 7 5800HS
  (8 cores, 16 threads), RTX 3060 6 GB and 16 GB, on native Ubuntu 24.04.4,
  kernel 7.0, NVIDIA 595.99.
- **FMU:** built for x86 from #221's exporter in the simulation container,
  BobSim `f58f932` and BobLib `75b6e03`, SHA-256 `9cc5cbb0…5fa5d3da`.
- **Code:** #221 head `efe2d737`, #222 head `82a45081`.
- **Stack:** `plant:=fmu perception:=lidar estimator:=ekf safety:=true`,
  Gazebo's GPU LiDAR, no cameras, launch defaults including the 0.3 s LiDAR
  timeout.

## Gate runs

| Code | Runs | Result | Notes |
| --- | --- | --- | --- |
| #221 | Autocross seeds 1 to 5, acceleration, 5 truth baselines | 0 of 6 scored; every baseline tripped `ODOM_STALE` | Pose lag grew about 0.9 s per second, to about 10 s within 20 s. `fmi2DoStep` failed right after pull-away in every run. The native drive test failed the same way at t = 5.03 s |
| #222, 300 s wall cap | First baseline | Timed out before finishing | Pacing held lag at 18 ms, but the sim ran at about one sixth of real time |
| #222, 1200 s wall cap | Autocross seed 1, acceleration | Both completed, 0 cones, 0 off course | Seed 1: lap 35.20 s (33.60 s on the Gazebo plant), max CTE 0.24 m, EKF error 0.20 m. Acceleration: CTE 0.03 m, EKF error 0.29 m. Lag 19 ms. Both "never stopped": 0.12 and 0.21 m/s when recording ended 3 wall seconds after `FINISHED` |

EKF error on the FMU plant (0.20 to 0.29 m) is about four times the Gazebo
plant's on the same courses. It stays under the 0.5 m limit, but has less room
once IMU noise lands
([#171](https://github.com/LonghornRacingElectric/lhre/issues/171)), and should
improve when the FMU supplies the inertial inputs
([#205](https://github.com/LonghornRacingElectric/lhre/issues/205)).

## The FMU alone

[`fmu_bench.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-07-fmu-gazebo-red-mesa/fmu_bench.py)
steps the FMU with fixed pedal phases on an otherwise idle red-mesa. Values are
mean milliseconds per 20 ms step; anything over 20 is slower than real time.

| Setup | Standing | Accelerating | Cruising | Braking | Weighted mean |
| --- | --- | --- | --- | --- | --- |
| Alone, tolerance 1e-6 (as built) | 53 | 98 | 29 | 68 | 68 |
| Alone, 1e-4 | 14 | 72 | 24 | 49 | 47 |
| Alone, 1e-3 | 6 | 60 | 19 | 109 | 49 |
| 2 at once, 1e-6 | 57 | 105 | 31 | 73 | +6% each |
| 4 at once, 1e-6 | 63 | 119 | 35 | 82 | +21% each |

- **Tolerance changes the answer, not just the speed.** The same pedal inputs
  end acceleration at 6.8 m/s at 1e-4 and 5.8 m/s at 1e-6, and cruise at 8.9
  instead of 8.1 m/s. Loosening it needs a correctness check first.
- **Standing still costs 53 ms per step,** and logged a nonlinear-system failure
  at 2.0 s. The solver also builds its Jacobian numerically (dense finite
  differences), which is usually slow.
- **FMUs alone scale across cores:** four at once cost 21% each, about 3.3
  times the throughput.

## Parallel full-stack runs

Each run had its own ROS domain and its own Gazebo partition (from #222), with
no shared cleanup. There was no sign of runs disturbing each other's topics or
world. Throughput is what broke.

| Runs at once | Outcome | Mean ms per FMU step |
| --- | --- | --- |
| 1 (gate runs above) | 2 of 2 completed | 125 to 141 |
| 2 (autocross seeds 1 and 2) | Both tripped `LIDAR_STALE` in `READY` | 223 |
| 3 (seeds 1 and 2, acceleration) | Acceleration completed; both autocross tripped `LIDAR_STALE` in `READY` | 183 (acceleration), 277 to 279 |

The full stack, not the FMU alone, saturates red-mesa's CPU. LiDAR frames then
arrive too far apart even in sim time.

## What it means for 11/14

- **One full-stack FMU run at a time on red-mesa,** so about 9 hours for the
  gate and fault matrix unless the FMU gets faster or the runs move to more
  machines (`hil`, or the cloud credits VMS has).
- **Results have to stop depending on wall time**
  ([#227](https://github.com/LonghornRacingElectric/lhre/issues/227)): wall-clock
  run caps and settle time broke scoring here.
- **Asks for #221 and #222:** solver speed (the numeric Jacobian, the
  nonlinear systems, the cost at rest), whether 1e-6 is converged given how much
  1e-4 moves the result, a brake hold near zero, and the pull-away crash.

## Method

Gate runs used the [gate tools](../tools/gate/README.md) with
`EXTRA="plant:=fmu fmu_path:=…"`. Parallel runs used separate launches with
`ROS_DOMAIN_ID` 40 and up, each in its own process group. The FMU-alone numbers
come from `fmu_bench.py`: 2 s standing, 5 s at 0.15 accelerator, 3 s at 0.08,
2 s at 0.3 brake.

## Files

- `fmu_bench.py`: the FMU-alone timing script.
- `results.csv`: one row per run in this campaign.
