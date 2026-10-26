# FMU autocross seeds 1 to 5

All five autocross seeds completed one verified lap with
`plant:=fmu perception:=lidar estimator:=ekf`. Every run recorded zero
cone contacts and zero off-course samples. This supplies the five-seed
completion evidence requested by
[#103](https://github.com/LonghornRacingElectric/lhre/issues/103).
[#159](https://github.com/LonghornRacingElectric/lhre/pull/159) was still
open when these runs finished; its merge remains required to close #103.

## Results

| Seed | Outcome | Lap time (s) | Mean CTE (m) | Max CTE (m) | Max EKF position error (m) | Cone contacts | Off-course samples |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | lap | 42.0423 | 0.0526 | 0.1312 | 0.3489 | 0 | 0 |
| 2 | lap | 42.2877 | 0.0497 | 0.1584 | 0.3021 | 0 | 0 |
| 3 | lap | 42.1403 | 0.0474 | 0.1782 | 0.2405 | 0 | 0 |
| 4 | lap | 41.5769 | 0.0591 | 0.1610 | 0.2715 | 0 | 0 |
| 5 | lap | 43.2177 | 0.0599 | 0.1781 | 0.2899 | 0 | 0 |

The full metrics rows are in
[results.csv](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-10-26-fmu-seeds/results.csv).
CTE and course checks use truth odometry against the fixed generated
reference. Control uses the EKF estimate. Each bag contains a true
`/lhr/metrics/lap_complete` signal, LiDAR points, estimated and truth
odometry, mission status, and the matching FMU provenance.

Seed 2 logged a LiDAR simulator `RuntimeError` while taking a subscription
message during shutdown. The accepted lap, final CSV row, and recorder
stop all preceded that traceback. No run logged a process error before
launch sent its shutdown signals. The shutdown error remains unresolved.

These are one-lap completion runs, not the full
[MVP gate](../tools/gate/README.md). They do not establish stopping distance,
three-repeat reliability, a truth-stack lap-time ratio, acceleration or
skidpad completion, or real-car accuracy. Metrics exits at the verified lap
and shuts down launch before a full stop can be measured. LiDAR comes from
the MVS sensor simulator, not Gazebo's GPU sensor or physical hardware.

## Method and identity

Runs used the clean `tanush/fmu-autonomy-plant` checkout at
`aa4ab3d`, in the `lhr-autonomy-dev` ROS 2 Jazzy container on macOS arm64.
All five used the same launch defaults: `v_max=4.0` m/s for LiDAR mode,
`fmu_pedal_gain=0.1`, and `start_on_track=true`. Only the seed, output paths,
recording flag, run ID, and wall watchdog changed. The watchdog was 900 s
instead of the headless runner's 120 s; no run reached it. No per-seed
tuning or runtime source changes were used.

- BobSim commit: `22c50565e516babb0f320ca24c140e6614b4beb3`.
- BobLib exporter commit: `4741a5ec6201635051031ed356a1bb55932df89c`.
- FMU SHA-256: `93654e501afbdacac2c713451b5bd2885ab5cf0855c375d197f686f0e9c34628`.
- Dynamics vehicle SHA-256: `e024a5e36ac3660889e298a8f88e599718b5fd97e23a39e1ec58f60f4df200f5`.
- Autonomy vehicle SHA-256: `b6092de69fe278ef44ba220ab71334126d4a238bbc765a25c4b2c998e4522a3b`.

The retained build
[manifest](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-10-26-fmu-seeds/provenance.json)
also identifies model, record and tire bytes. Runtime provenance checks
accepted the existing artifact against this checkout. The workspace build
passed, and the two opt-in native FMU tests passed before the runs.
The native drive test emitted SciPy's iteration-progress warning; FMPy's
optional arm64 logger proxy was unavailable. Neither prevented accepted
FMI steps or passing tests.

Raw data remains in the gitignored host directory
`autonomy/ros2/data/2026-10-26-fmu-seeds/`. Each
`fmu_autocross_seed<N>_run1/` holds `metrics.csv`, `args.txt`, `exit_code.txt`,
`launch.log`, and `bags/<run_id>/`. The root holds `provenance.json` and
`audit.json`. SharePoint upload is pending; there is no shared raw-data link
yet. Keep the local recordings until the upload is verified.

## Repeat

Use the [FMU setup](../../ros2/src/lhr_sim_fmu/README.md), then run inside
the ROS container from `autonomy/ros2`. Use a new output directory for
each campaign to retain every attempt.

```bash
./scripts/build.sh
LHR_TEST_FMU=/opt/lhr/simulation/out/fmu/OrionFMI.fmu \
  python3 -m pytest src/lhr_sim_fmu/test/test_native_fmu.py -q

OUT=data/fmu-seeds-rerun
mkdir -p "$OUT"
for seed in 1 2 3 4 5; do
  run="fmu_autocross_seed${seed}_run1"
  mkdir -p "$OUT/$run"
  METRICS_CSV="$OUT/$run/metrics.csv" ./scripts/run_headless.sh \
    plant:=fmu track_style:=autocross perception:=lidar estimator:=ekf \
    seed:="$seed" timeout_sec:=900 record:=true \
    run_id:="$run" bag_dir:="$OUT/$run/bags" \
    > "$OUT/$run/launch.log" 2>&1 || break
done
```

The native test command needs the ROS and workspace setup sourced, as
described in the FMU guide. Check process logs and bag contents as well as
the headless exit code: a successful row alone does not rule out shutdown
errors.
