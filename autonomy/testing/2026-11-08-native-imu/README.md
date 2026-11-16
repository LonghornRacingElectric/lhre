# Native Gazebo IMU fidelity

Issue [#171](https://github.com/LonghornRacingElectric/lhre/issues/171) identified
that the Gazebo EKF received a perfect physics IMU: synthetic noise and
`gyro_bias` were attached to the disabled inertial-sim publisher.

The requirements-only branch adds native six-axis white noise and startup
bias from vehicle.yaml, applies signed `gyro_bias` to the actual Gazebo gyro,
and retains noisy/noise-free gate evidence and reproduction tools. It does
not change EKF math, wheel fusion, controller, actuation, safety thresholds,
or the existing 100 Hz IMU acquisition cadence. GPS fusion, gyro calibration,
stationary corrections and jitter-reduction prototypes are separate work.

## Historical gate rerun

These retained results are from the **2026-11-08** native IMU noise-and-bias
implementation on base `da9d5a95a9a49679a05c5a313432dfb8a55626dd` plus its
fingerprinted working tree. They are not a fresh gate run of the current
branch. The retained `manifest.json` fingerprints an uncommitted working
tree, not a clean revision. It already lists `imu_model.py` and
`validate_imu.py` with the same hashes as the pre-review branch snapshot in
`branch-verification.json`; the stationary ±0.03 rad/s cases exercised that
signed-bias overlay. Calling that implementation "noise-only" was wrong.
The hashes have been preserved rather than refreshed from today's files.
The manifest's raw-data location is now relative to the repository root;
the historical vehicle YAML SHA is
`123ad891ed3db8737fffe52b529775337c824bb150e2c602345abb0b0ec398dd`.

The EKF limit is **0.5 m maximum position error**, not p95.

| Course | Clean outcome | Noisy outcome | Clean mean / maximum (m) | Noisy mean / maximum (m) |
| --- | --- | --- | ---: | ---: |
| Autocross seed 1 | Early emergency, 3.14 s | Lap, 33.66 s | 0.0187 / 0.0380 | 1.4986 / 2.2968 |
| Autocross seed 2 | Lap, 33.82 s | Lap, 33.86 s | 0.0374 / 0.0526 | 1.1684 / 1.8708 |
| Autocross seed 3 | Lap, 33.72 s | Invalid lap/contact, 31.62 s | 0.0337 / 0.0553 | 5.0984 / 8.1924 |
| Acceleration | Finished, 18.18 s | Finished, 18.12 s | 0.0471 / 0.0577 | 1.2400 / 3.2772 |

Noise exposed the optimistic error estimate: every noisy trial failed the
EKF error check. Full-gate verdicts were 3/4 clean and 0/4 noisy. Seed 2 is
the clearest completed-lap comparison; early/invalid runs do not establish
full-lap maximum error. Failed checks and missing results are retained in
`comparison.csv` and the clean/native result tables.

![Historical clean and noisy EKF errors](figures/position-error.png)

This bounded campaign used seeds 1–3 once and one acceleration run, without
skidpad. Both modes used a **1.5 s LiDAR timeout** and the then-current
wheel-assisted estimator. It is not a full five-seed/three-repeat gate or
a pass at the default 0.3 s timeout. The seed-1 clean emergency was
`LIDAR_STALE`; an unrelated solver workload overlapped that first clean
trial but was stopped before clean seeds 2/3 and the noisy trials.

Default-timeout probes are retained separately. The clean probe stopped
with `LIDAR_STALE` after 14.92 scored seconds (maximum error 0.0541 m).
The noisy probe failed before driving samples existed: its error is
unavailable, not zero. One realization per course does not establish a
statistical noise distribution or isolate all causes of driving failure.

## Sensor verification and assumptions

The historical parked topic check retained 1,501 samples per configuration
over 15 simulated seconds, after settling. Measured gyro white-noise sigmas
were 0.0005519–0.0005645 rad/s versus configured 0.00055536; accelerometer
sigmas were 0.00702–0.00723 m/s² versus configured 0.00693435. Both positive
and negative 0.03 rad/s fault offsets reached the bridged z gyro. Startup
offsets were scored separately from temporal white noise. Results are in
`stationary.csv` and `stationary-checks.json`.

The [configuration documentation](../../ros2/src/lhr_vehicle/README.md#native-gazebo-imu)
records Livox's ICM40609 identification, TDK component datasheet values, SI
conversions, the assumed one-sided 50 Hz bandwidth and assumed ±3σ mapping
of initial-offset scales. These are component approximations, not measured
filtering or calibration of the donated unit. Thermal drift, saturation,
scale errors, axis coupling and wheel slip remain outside this change.

The EKF still consumes gyro/acceleration, not Gazebo's ideal orientation.
One-time truth initialization and truth used for metrics retain their
existing roles; this is a fidelity fix, not removal of a truth shortcut.

## Pre-review branch validation, 2026-11-15

The isolated branch is based on current `main`,
`1a99e0372958cd70ee7cf1ff2ff714a666cb3c46`. The historical results above are
not substituted for a measurement of its newer LiDAR mount, fusion stack,
scoring and safety behavior.

Both SDFs were regenerated and passed the generator freshness check. All
16 ROS packages build. Regression tests exercise all six native noise
channels, density/unit conversion, invalid configuration, standard/stereo
overlays, signed faults, launch routing and metrics parameter types.

Completed results cover 15 ROS packages: 1,271 checks passed and two existing
optional native-FMU checks skipped. This includes all 427 Gazebo and 72
vehicle checks. Strict MkDocs, both `gz sdf -k` checks, and whitespace checks
pass. The full-workspace suite is not claimed: BobSim first lacked its native
dependency paths, and a bounded retry with those paths did not complete.
No tests were skipped or weakened to obtain a pass. `branch-verification.json`
records this branch's source fingerprints and software results separately
from the original campaign manifest.

A fresh stationary measurement and matched gate campaign could not run in
the original preparation environment: opening/binding a local UDP socket raised
`PermissionError: [Errno 1] Operation not permitted`, preventing normal
ROS/Gazebo transport. This is not a scored driving failure or a successful
rerun. A gate comparison could not be collected locally. Reviewer-supplied
host evidence for `08e459e` is recorded below; it is separate from local
checks and does not validate the subsequent seed-scheduling changes.

Use the [current reproduction commands](../../ros2/src/lhr_gazebo/README.md#native-imu-noise-and-bias)
to collect clean/noisy campaigns with otherwise identical settings. Preserve
all failed trials, unknown errors, default-timeout outcomes and exact launch
arguments. No new passing estimator or autonomous-lap claim is made here.

## Reviewer-supplied red-mesa campaign, 2026-11-15

The PR review reports a full matched campaign on `08e459e`: five autocross
seeds × three repeats plus three acceleration runs, skidpad waived, with
the default **0.3 s LiDAR timeout**. Raw data is reported at
`red-mesa:~/lhr-test-data/2026-11-15-pr296-imu`. These are the reviewer's
results supplied with the review, not measurements collected or raw bags
independently inspected during this revision.

| Check | Noise off | Noise on |
| --- | --- | --- |
| Gate | PASS, 18/18 | FAIL, 0/18 |
| Maximum EKF position error | 0.05–0.07 m | 1.9–8.4 m; every run fails `ekf` |
| Other failures | None reported | Seed 4: cone, invalid lap and never stopped in all three repeats; seed 3: 1.29–1.38× reference lap time and two failed stop checks |

The reviewer reports all stationary checks passing and 36/36 bag
cross-checks agreeing. Noisy maximum errors agree with the historical
campaign: seed 1 2.39 vs 2.30 m, seed 3 8.43 vs 8.19 m, acceleration
3.38 vs 3.28 m. The three seed-3 repeats measured 8.42, 8.45 and 8.43 m.
At that revision track seed also seeded Gazebo: repeats reused their bias
draw, so these 18 runs represented six course/seed groups, not 18
independent noise realizations.

`imu_noise` now defaults to `false` in launch and metrics. Bias estimation
remains follow-up [#87](https://github.com/LonghornRacingElectric/lhre/issues/87),
starting with confirmed standstill during READY; enabling noise in the
required gate stack waits for that work. The new `imu_seed` separates
Gazebo randomness from track geometry, is recorded in metrics, and advances
once per gated run. This changes future draw assignments; neither historical
campaign is relabeled as a run of that new schedule. A fresh noisy gate
pass is not claimed.

## Review-fix verification, 2026-11-16

All 16 ROS packages build. The affected-package suites pass **694 tests**:
530 Gazebo, 92 metrics and 72 vehicle, including Python lint. Strict MkDocs
and whitespace checks pass. Tests cover default-off noise, enabled noise
with both signs of injected bias, standard/stereo overlays, distinct track
and Gazebo seeds, metrics node startup in both perception modes, per-repeat
draw assignment and seed-range errors. Incomplete bag cases and baseline-only
plots are covered without launching a simulator.

The updated checker passes all eight retained historical bag measurements.
Regenerating the comparison preserves all eight existing CSV rows exactly;
the figure above was rerendered with failure hatching corrected for completed
runs that fail `ekf`. Historical source hashes exactly match the manifest
retained beside the original raw captures. `review-verification.json` records
the software checks and current source hashes separately from historical
provenance. No fresh simulator campaign of the new seed schedule was run.

## Evidence and preservation

`bag-crosscheck.csv` independently recomputes the eight available historical
EKF measurements from timestamped poses. All eight agree within the stated
0.02 m / 0.002 rad delivery/rounding tolerances. Historical `verification.txt`
and `manifest.json` describe that historical native-noise-and-bias campaign,
not current test counts.

Raw historical bags remain locally in the preserved experimental workspace
under `autonomy/ros2/data/imu171`; they have not been uploaded to SharePoint
and are never committed. The progress branch is
`koa/imu-gnss-jitter-progress-20261115`. A Git branch preserves source and
selected evidence, not ignored raw captures; keep the original data directory.
