# Machine benchmark, 2026-11-16

## Question

Which of our machines can run the autonomy stack's Gazebo gate, how fast,
and does the gate's result depend on the machine it runs on?

## Answer

- **Every machine that finished passes the full gate 18 of 18 at the default
  0.3 s LiDAR timeout, and the result does not depend on the machine.** Each
  seed's lap time matches across machines within about 0.01 s, from 0.24x to
  0.81x real time. That answers the worry in
  [#201](https://github.com/LonghornRacingElectric/lhre/issues/201) that
  reference laps were machine dependent: the gate scores in sim time.
- **One run tops out near 0.8x real time whatever the hardware.** black-ridge
  (Ryzen 9 5950X, RTX 4070 SUPER) ran at 26% CPU and 16% GPU and still did not
  beat red-mesa. Per-run speed is limited by the stack, not the machine, so
  more throughput has to come from running gates in parallel
  ([below](#parallel-runs)).
- **WSL2 with an NVIDIA GPU works** once Mesa is pointed at the GPU with
  `GALLIUM_DRIVER=d3d12`. Without it, WSL silently renders on the CPU
  (llvmpipe). The February problems behind the "No WSL" rule (ROS Humble,
  Ignition Fortress, an older Mesa without OpenGL 3 on D3D12) do not reproduce
  on Ubuntu 24.04 with Jazzy and Harmonic. **WSL with Intel graphics does not
  work:** 0.09x real time, and 3 of 7 runs never started.
- **hil runs the gate in the autonomy Docker image on its Quadro M4000** at
  0.66x, 22 minutes per gate, which covers the first point of
  [#204](https://github.com/LonghornRacingElectric/lhre/issues/204).

## Results

One full gate campaign per machine, same commit, same settings.

| Machine | Setup | Gate | Speed (x real time) | Gate wall time | CPU busy | GPU busy | Max EKF error / CTE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| red-mesa | Native Ubuntu 24.04; Ryzen 7 5800HS (16 threads), RTX 3060 Laptop | **18/18** | 0.81 (0.78 to 0.83) | 19 min | 57% | 15% | 0.061 / 0.268 m |
| black-ridge | WSL2 Ubuntu 24.04; Ryzen 9 5950X (32 threads), RTX 4070 SUPER | **18/18** | 0.77 (0.74 to 0.79) | 19 min | 26% | 16% | 0.063 / 0.264 m |
| hil | Docker on Ubuntu 26.04; 2x Xeon E5-2650 v4 (48 threads), Quadro M4000 | **18/18** | 0.66 (0.63 to 0.68) | 22 min | 30% | 9% | 0.064 / 0.267 m |
| blue-crest | Colima container (arm64); Apple M5, 6 CPUs, CPU rendering | **18/18** | 0.60 (0.56 to 0.67) | 25 min | 77% | none | 0.084 / 0.258 m |
| white-canyon VM | Hyper-V Ubuntu 24.04; i5-9500T, 4 vCPUs, CPU rendering | **18/18** | 0.24 (0.23 to 0.25) | 57 min | 89% | none | 0.094 / 0.254 m |
| white-canyon WSL | WSL2 Ubuntu 24.04; i5-9500T, 6 cores, Intel UHD 630 via D3D12 | **Stopped** after 7 runs: 4 laps, 3 hung | 0.09 | about 2 h projected | 33% | 3% | n/a |

Speed is sim seconds per wall second for each gate run, startup included, as
the gate summary reports it; the range is the spread across the 18 runs. CPU
and GPU busy are averages of 5 s samples over the campaign. Memory peaked at
about 4 GB on every machine. [`results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-16-machine-benchmark/results.csv)
has every run; [`machines.md`](machines.md) is the generated summary.

On white-canyon the CPU-only VM is slower than the others but reliable. Under
WSL, OpenGL reaches the UHD 630 through D3D12 (OpenGL 4.1), but finished runs
took about 520 s of wall time for 46 s of sim, and 3 of the first 7 hung:
Gazebo's clock never moved, so `watch_run.py` ended them after `HANG_S`. The
run was stopped there.

## Parallel runs

Pending. Four isolated gate campaigns at once on hil and on black-ridge,
started 11/16 2:09 PM. `run_gate.sh`'s `cleanup()` pkills every `gz sim` and
bridge it can see, so campaigns that share a process namespace kill each
other. Each campaign therefore gets its own namespace and network, plus its
own `ROS_DOMAIN_ID` and `GZ_PARTITION`:

- hil: one GPU container per campaign with `--network none`
  ([`par-hil.sh`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-16-machine-benchmark/par-hil.sh)).
- black-ridge: one `unshare --pid --net --mount-proc` namespace per campaign
  inside WSL, loopback only
  ([`par-wsl.sh`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-16-machine-benchmark/par-wsl.sh)).

## Method

- **Commit:** `main` at `1a99e037` everywhere, built with `colcon` natively,
  in WSL, or in the image.
- **Workload:** [`run_gate.sh`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/tools/gate/run_gate.sh)
  defaults: autocross seeds 1 to 5, three repeats each, and three acceleration
  runs, on the gate stack from
  [`stack.args`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/tools/gate/stack.args)
  at the default `safety_lidar_timeout_s` of 0.3 s. Skidpad is waived: `main`
  has no skidpad world yet.
- **Wrapper:** [`bench.sh`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-16-machine-benchmark/bench.sh)
  records the machine (CPU, GPU, renderer, rendering environment) and samples
  `vmstat` and `nvidia-smi` every 5 s around `run_gate.sh`.
  [`compare.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-16-machine-benchmark/compare.py)
  turns each machine's output into `machines.md` and `results.csv`.

## Machine setup

- **red-mesa:** native Ubuntu 24.04 and ROS 2 Jazzy, per
  [GETTING-STARTED](../../ros2/GETTING-STARTED.md).
- **black-ridge:** WSL 2.4.13 with Ubuntu 24.04 and Jazzy installed natively
  inside it. Export `GALLIUM_DRIVER=d3d12` and
  `MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA` before launching; `eglinfo -B` then
  reports `D3D12 (NVIDIA GeForce RTX 4070 SUPER)` with OpenGL 4.6. WSL's
  memory cap was raised to 16 GB in `.wslconfig` for the parallel runs.
- **hil:** Ubuntu 26.04 has no Jazzy packages, so it runs the
  [Docker image](../../ros2/docker/README.md) with the NVIDIA container
  toolkit, `docker run --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all`. Headless
  EGL in the container reports `Quadro M4000`.
- **blue-crest:** the Mac Colima container from `ros2/docker`, arm64, which
  also shows the stack runs on ARM like the AGX Orin.
- **white-canyon:** HP EliteDesk 800 G5 Mini. VM: Hyper-V, Ubuntu 24.04
  server, 4 vCPUs and 10 GB. WSL: WSL 3.0.1, Ubuntu 24.04.

## Caveats

- One campaign per machine. The within-campaign spread is in the table;
  repeats would firm the speeds up.
- black-ridge is a daily desktop and was not otherwise idle-controlled.
- This is the gate on `main` today, not the Sim v2 matrix: the FMU plant,
  IMU noise, extreme and real courses and skidpad are not on `main` yet.
- The CPU-rendered machines (blue-crest, the white-canyon VM) pass at 0.3 s,
  where the MVP notes said CPU rendering needs 1.5 s (#160). That note may be
  stale for the current gate stack; worth confirming before relying on it.

## Data

Raw logs and bags are on each machine under
`~/lhr-test-data/2026-11-16-bench-<machine>` (hil and blue-crest under the
mounted `autonomy/ros2/data/`). SharePoint upload pending.

## Files

- [`results.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/2026-11-16-machine-benchmark/results.csv): one row per gate run, every machine.
- [`machines.md`](machines.md): the per-machine summary `compare.py` writes.
- [`log.md`](log.md): what was run when.
- `bench.sh`, `compare.py`, `par-hil.sh`, `par-wsl.sh`: the scripts above.
