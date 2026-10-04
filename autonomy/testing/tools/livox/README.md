# Livox Mid-360 test tools

Scripts that record and analyze a Mid-360 without ROS. They talk to the
sensor over its UDP protocol directly, so they run on a Mac, where Livox
Viewer has no build and `livox_ros_driver2` cannot run in the Docker image
(OrbStack rewrites UDP source addresses, and the SDK matches sensors by
source address). Used for the
[2026-10-04 acceptance test](../../2026-10-04-mid360-acceptance/README.md).

Each script declares its dependencies inline, so `uv run <script>.py` is
all it needs. Run them from this folder: the streamer and the cone runner
share `live.log`, `rec.txt` and `bags/` here (all gitignored).

## Setup

- Host on `192.168.1.50/24`, sensor at `192.168.1.134`. Both are constants
  at the top of `livox_mid360.py`. On macOS:
  `networksetup -setmanual "USB 10/100/1000 LAN" 192.168.1.50 255.255.255.0`.
- The sensor must already send its data to `192.168.1.50`.
  `livox_ros_driver2` sets that on every start; these scripts do not change
  it. `probe` shows the current destinations.
- A campaign folder `~/lhr-test-data/<campaign>/` with a `session.json`
  (sensor height, cone, location, lighting, run prefix), and the
  `~/lhr-test-data/current` link pointing at it. The
  [testing README](../../README.md) has the layout.

## Scripts

| Script | What it does |
| ------ | ------------ |
| `livox_mid360.py probe` | Serial, firmware, state, temperature, and stored network config. Read-only. |
| `livox_mid360.py mode normal`, `mode wakeup` | Spin the motor up, or idle it before unplugging. |
| `livox_mid360.py capture` | Record N seconds to `.npz` and print throughput, loss and IMU stats. |
| `livox_live.py` | Stream to Foxglove at `ws://127.0.0.1:8765`, log health once a second, and record a window on demand: write `<name> <seconds>` to `rec.txt` and it saves `bags/<name>.mcap` and `.npz`. |
| `mid360-layout.json` | Foxglove layout: 3D view, gyro and accel plots, lost packets, health. |
| `tilt.py` | Sensor tilt toward the cone line, from the streamer. For levelling. |
| `cone_run.py` | One cone position: record, find the cone, save two figures, add a row to the campaign's `runs.csv`. |
| `cone.py` | The cone finder and metrics behind `cone_run.py`. |
| `farcheck.py` | Past 12 m, list objects that are new against a run with the cone elsewhere. |
| `geom.py` | Plane fits: flatness, room-corner angles, point cloud against IMU gravity. |
| `envcompare.py` | Compare captures across conditions: noise flags, isolated points, floor noise, range. |
| `bodybase.py` | Split cone hits into body and base, to separate the sensor from cone placement. |
| `bev.py` | Quick top and side view of a capture. |
| `ros2_bag.py` | Convert a `.npz` to a ROS 2 bag that the stack replays unchanged. |

`ros2_bag.py` writes `/lhr/lidar/points` (PointCloud2 in `livox_frame`),
`/livox/imu` (Imu, SI units), a stationary `/lhr/vehicle/odom` so
`lidar_cone_detector` runs, and `/tf_static` from `base_link`. Replay with
`ros2 bag play -s mcap <file>.mcap`.

## Gotchas

- **Keep the streamer attached to a terminal or SSH session.** On macOS a
  process detached with `nohup` loses the right to send to the local
  network about 5 s after its session ends, so the status poll fails.
- **Run it under `caffeinate -dimu`.** A Mac on battery goes to sleep and
  stops the stream.
- **Connect Foxglove to `127.0.0.1`, not `localhost`.** The server listens
  on IPv4 only. The 3D panel needs the `/tf` the streamer sends before
  `livox_frame` shows up as a display frame.
- **A USB Ethernet adapter can drop out for about 6 s at a time.** The
  health log shows it as zero points with "can't assign requested address".
  Check the lost-packet count of every recording.
- **The sensor needs about 7 s from idle before data flows.** `capture` and
  the streamer start the motor themselves.
- **Gyro bias depends on temperature.** Note the core temperature (in the
  health log) next to any IMU number.
