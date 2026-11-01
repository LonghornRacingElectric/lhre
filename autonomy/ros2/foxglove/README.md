# Foxglove layouts

`lhr_gazebo.json` follows a Gazebo run without RViz: truth and detected cones,
the fixed reference centerline, planned path, vehicle pose against truth,
mission state and live metrics. It adapts the Gazebo layout from PR #119
to the current launch. `lhr_sim.json` remains the kinematic demo's layout.

## Live Gazebo run

Build and source the workspace as described in [Getting started](../GETTING-STARTED.md).
Install `ros-jazzy-foxglove-bridge` for native ROS. From `autonomy/ros2`:

```bash
./scripts/run_gazebo_demo.sh \
  track_style:=autocross seed:=1 perception:=lidar estimator:=ekf \
  lidar_source:=mid360 foxglove:=true foxglove_port:=8767 \
  gui:=false rviz:=false auto_go:=true safety:=true
```

1. Open Foxglove on the same computer as the simulator.
2. Click **Open connection**, select **Foxglove WebSocket**, enter
   `ws://localhost:8767`, then click **Open**.
3. Open the **Layouts** menu and choose **Import from file...** (some versions
   show **Add > Import Personal Layout**).
4. Select `autonomy/ros2/foxglove/lhr_gazebo.json` inside this checkout.
   In Ubuntu's file picker, Ctrl-L accepts a pasted absolute path.
5. Live data updates automatically. Wait for startup and `DRIVING`; no playback
   button is needed for a live connection.

The layout file is also available in the repository as
[`lhr_gazebo.json`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/foxglove/lhr_gazebo.json).
Keep metrics enabled (the default). Run one simulator at a time. To inspect
the GPU source, stop the first run and repeat with `lidar_source:=gazebo`.
If local GPU delivery trips the unchanged freshness gate,
`safety_lidar_timeout_s:=1.5` is a diagnostic override, not default-timeout
acceptance; see the [retained timing limitation](../src/lhr_gazebo/README.md#gpu-grid-rollback).

| Panel | What to check |
| --- | --- |
| 3D | Translucent blue/yellow/orange cylinders are nominal truth cone envelopes read from the selected SDF. Bright orange spheres are unclassified detections; optional side classification changes their colors. Purple is the fixed truth centerline; green is the planner's changing path. |
| Pose | Orange estimated odometry and cyan truth odometry should follow the course. X/Y plots compare their map-frame positions. Quaternion Z/W plots compare orientation without Euler-angle wrapping; the 3D pose arrows show heading. |
| Speed | Command, estimated and truth longitudinal speeds update while driving and fall to zero after completion. |
| Steering | Commanded steering angle in radians. |
| Mission | Shows the current state, including `DRIVING`, `FINISHED` or `EMERGENCY`. |
| Run metrics | Expand `status[0]` and `values` to inspect named CTE, off-track distance, contacts, progress, duration and estimator errors. Units are metres, seconds and radians; blank errors mean unavailable, not zero. |

Raw LiDAR is included but hidden by default to keep cones readable. Enable
`/lhr/lidar/points` in the 3D topic settings for inspection. A source's cloud
frame must have a valid transform; if it does not, inspect the header and TF
ownership rather than adding a competing static transform. The layout itself
requires only `map` and `base_link` transforms.

The pose comparison expects `estimator:=ekf`: truth mode has no separate
`/lhr/vehicle/odom_truth` stream. The metrics summary stops updating when the
scorer ends; mission state and speed continue to show the final stop. Truth
cones use `/lhr/viewer/cones_truth`; the fixed reference path uses
`/lhr/viewer/centerline_truth`. Neither feeds perception or planning.

## Record and check the layout

Gazebo has no `record:=true` argument. While it runs, use another sourced
terminal to record all layout inputs (MCAP stays under ignored `data/`):

```bash
ros2 bag record -s mcap -o data/bags/gazebo-layout \
  /clock /tf /tf_static /lhr/vehicle/body \
  /lhr/viewer/cones_truth /lhr/viewer/centerline_truth \
  /lhr/sensor/cones_detected /lhr/track/centerline /lhr/lidar/points \
  /lhr/vehicle/odom /lhr/vehicle/odom_truth /lhr/vehicle/cmd \
  /lhr/mission/status /lhr/metrics/live
```

Stop the recorder with Ctrl-C after driving and stopping, then run:

```bash
LAYOUT=foxglove/lhr_gazebo.json ./scripts/check_layout.sh data/bags/gazebo-layout
```

Use a new output directory for each run. Open the recorded `.mcap` in Foxglove
and import the same layout. `check_layout.sh` checks topic/frame availability;
it does not prove that fields update or that panels render correctly.

## Visual acceptance for #107

Check both `gazebo` and `mid360` sources with LiDAR perception and EKF:

1. Import the committed layout; confirm every panel opens without schema or TF errors.
2. Confirm both truth cones and detected cones appear, with distinct appearance.
3. Follow a driving vehicle; distinguish the fixed truth centerline from the updating planned path.
4. Confirm estimated/truth poses and X/Y/orientation plots populate and agree reasonably.
5. Confirm live metrics and mission state populate, then watch speed reach zero.
6. Confirm the run is understandable with `rviz:=false` throughout.

Record the tested revision, source, launch arguments and any diagnostic timeout
with the result. Protocol/topic checks alone are insufficient to close #107;
the graphical checklist needs a person to verify it in Foxglove.

## Previous bounded checks

Before the temporary worktree was cleared by a reboot, PR #133 base `3a1ddef`
plus these changes built the 12 packages needed by `lhr_gazebo`, passed 298
affected-package checks and strict MkDocs. The two headless LiDAR/EKF
autocross seed-1 recordings passed `check_layout.sh` and checks of every
plotted field against deserialized messages. Both reached `FINISHED` with
zero truth speed and command:

| Source | LiDAR timeout | Valid lap | Max CTE | Cone contacts |
| --- | --- | --- | --- | --- |
| `mid360` | 0.3 s | 33.6147 s | 0.2423 m | 0 |
| `gazebo` | 1.5 s, diagnostic | 33.6047 s | 0.2484 m | 0 |

An initial 150 s wall-limited attempt was interrupted before completion and
exposed a shutdown publication error. The guard was fixed and regression-tested.
A late Mid-360 WebSocket probe missed metrics within its message limit and
was not counted as a pass; updating metrics and the terminal summary were
present in the recording. Live SDK subscriptions received the new viewer data.

These are historical bounded simulator results, not a full acceptance gate.
The old `/tmp` recordings no longer exist. The restored layout matches its
previous SHA-256: `49abcb0f8b7a2cbfc91754ce3e3e02fa2791e1f9760d4435849dfef061ecc7d6`.
**Graphical acceptance remains pending the user's checklist above.**

After restoration in the persistent worktree, the 12-package build and all
298 affected-package checks pass again, as does strict MkDocs. A stationary
startup check negotiated `foxglove.sdk.v1` and received LiDAR, live metrics,
truth odometry, truth cones and the fixed truth centerline. This confirms
startup and transport after recovery, not graphical or renewed driving
acceptance.
