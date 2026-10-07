# Gazebo Integration Plan

## Goal

Replace the kinematic vehicle simulator (`lhr_sim_kinematic`) with a full Gazebo physics simulation with realistic sensor-based perception, targeting Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic.

## Current Status

**Phase 1 (Gazebo physics):** Complete.
**Phase 2 (LiDAR perception):** Side inference and one-to-one pairing are implemented and covered by generated autocross regressions. Full visual lap validation remains.
**Phase 3 (Camera fusion + tuning):** Not started. See [camera-fusion.md](camera-fusion.md) for detailed plan.

## What Works Today

- Gazebo physics sim with direct joint control (no AckermannSteering plugin)
- Sim perception mode (`perception:=sim`) — ground-truth cones, index pairing, fully reliable
- LiDAR perception mode (`perception:=lidar`) — pointcloud clustering, geometry-inferred sides, one-to-one pairing
- Oval track (`track_style:=oval`) — LiDAR pipeline completes laps reliably
- `OpaqueFunction`-based launch file auto-resolves world SDF from `track_style` + `seed`
- RViz runs alongside Gazebo with centerline, cones, and odometry visualization

## Known Issues

1. **Residual cone-map drift** — timestamp-aligned transforms and one-to-one scan association remove the known sharp-turn duplication mechanism. EKF drift can still move the persistent map and needs visual and on-car validation.

2. **Partial-map path ordering** — classified one-to-one pairing removes midpoint branches. The remaining greedy ordering needs runtime validation while only part of a track has been observed.

3. **No measured cone colour** — LiDAR now infers the track side from vehicle-relative observations and boundary continuity. A camera is still needed to measure blue/yellow colour and independently validate the inference.

4. **Backwards path wrapping** — the chain starts from the vehicle and goes forward, then wraps backwards through midpoints behind the vehicle. Cosmetic only (pure pursuit ignores the backwards portion) but messy in RViz.

5. **Cone collisions disabled** — cones are ghost objects. The car drives through them instead of being penalized.

---

## Remaining Work

### Phase 2b — LiDAR Tuning (no new sensors)

Goal: make LiDAR perception reliable enough to complete autocross laps.

#### 2b.1 — Improve cone dedup robustness

**File:** `lhr_perception/lidar_cone_detector.py`

The detector now interpolates odometry at the pointcloud timestamp and performs nearest one-to-one association per scan. The remaining dedup uses a fixed 1.5m radius with running-average position merging. During swerves, residual localization or measurement error can exceed this. Options:
- Gate new cone additions on vehicle angular velocity (skip detections during rapid yaw change)
- Use a larger dedup radius (up to ~1.8m, limited by same-side cone spacing)
- Weight running average by distance from sensor (closer = more accurate = higher weight)

#### 2b.2 — Improve centerline chaining

**File:** `lhr_track_builder/track_builder_node.py`

Geometry-inferred sides and minimum-cost one-to-one pairing remove duplicated midpoint candidates. The remaining greedy ordering should be evaluated on partial maps. Options if runtime validation still shows discontinuities:
- Add a maximum step distance to the chain (skip midpoints that are too far from the last chained point)
- Use angular continuity — prefer the next point that continues roughly in the same direction
- Only chain midpoints within a forward arc of the vehicle (ignore midpoints behind)

#### 2b.3 — Validate on autocross

Test with `track_style:=autocross perception:=lidar` and iterate on the above until the car completes laps.

### Phase 3 — Camera Fusion

Goal: add a camera sensor to see cone colors, enabling proper left/right classification and simpler pairing.

#### 3.1 — Add camera sensor to vehicle model

**File:** `lhr_gazebo/models/fsae_vehicle/model.sdf`

Add a forward-facing camera to the chassis link (near the LiDAR mount). Bridge the image topic via `ros_gz_bridge`.

#### 3.2 — Camera-based cone color classification

**File:** `lhr_perception/` (new node or extend `lidar_cone_detector`)

- Subscribe to camera image + LiDAR cones
- Project each cone's map position into the camera frame
- Sample the pixel color at the projected location
- Classify as blue (left) or yellow (right)
- Publish classified MarkerArray with `left_cones`/`right_cones` namespaces

This restores proper left/right classification, allowing the track builder to use simpler and more reliable pairing strategies (index or nearest-neighbor) instead of Delaunay.

#### 3.3 — Update track builder for fused perception

**File:** `lhr_track_builder/track_builder_node.py`

With camera-classified cones, add a `'fused'` pairing strategy (or reuse `'nearest'`) that pairs left/right cones with confidence. Fall back to `'boundary'` for unclassified cones.

### Phase 4 — Fidelity Tuning

Goal: make the simulation match the real car closely enough for control parameter transfer.

- Re-enable cone collisions and tune control to avoid them
- Tune tire friction, mass distribution, and steering dynamics to match real car
- Add sensor noise models (LiDAR range noise, camera exposure variation)
- Test at competition speeds (up to 15 m/s)
- Validate against real car telemetry data
- Add IMU-based state estimation (replace ground-truth odometry)

---

## Architecture

```
PHASE 1 (perception:=sim)            PHASE 2 (perception:=lidar)
─────────────────────────            ──────────────────────────
lhr_trackgen (ground-truth cones)    Gazebo gpu_lidar sensor
lhr_sensor_sim (FOV filter)          lhr_perception (pointcloud → inferred track sides)
Gazebo physics (joint control)       Gazebo physics (joint control)

lhr_track_builder (index pairing)    lhr_track_builder (one-to-one classified pairing)
lhr_control                      →   STAYS (pure pursuit + curvature speed planning)
lhr_mission_manager              →   STAYS (FSAE state machine)
lhr_metrics                      →   STAYS (CTE, lap detection, CSV output)
```

Future Phase 3 adds a camera branch feeding into `lhr_perception` so measured cone colour can confirm or replace geometry-inferred sides.

## Implemented Components

### Vehicle Model (SDF)

File: `lhr_gazebo/models/fsae_vehicle/model.sdf`

- Ackermann steering geometry (front two wheels steer, rear two driven)
- Geometry, masses and steering limits come from `lhr_vehicle/config/vehicle.yaml` (Orion: wheelbase 1.549 m, track 1.212 m, wheel radius 0.2045 m); `model.sdf` is generated from it
- Direct joint control (JointPositionController + JointController) — NOT AckermannSteering
- Sensors: IMU (100 Hz), GPU LiDAR (360x16 channels, 0.5–25 m, 10 Hz)

### Cone Models + World Generation

Script: `lhr_gazebo/scripts/generate_world.py`

- `--style` flag selects generator: `autocross` (Catmull-Rom spline), `oval` (arc-length ellipse), `simple` (wobble oval)
- `--seed`, `--num-waypoints`, `--radius`, `--jitter`, `--width`, `--cone-spacing` parameters
- Vehicle spawn auto-computed on straightest track section
- ODE physics at 1 kHz, real-time factor 1.0
- `gz-sim-sensors-system` with Ogre2 render engine
- Blue/yellow cone collisions disabled for tuning (Phase 4 will re-enable)
- Cone models use generated cone-shaped STL meshes (not box primitives)
- Worlds installed by colcon via `setup.py` `data_files`

### Launch File

File: `lhr_gazebo/launch/gazebo_demo.launch.py`

Uses `OpaqueFunction` for runtime resolution:
- `track_style` + `seed` → auto-resolves world SDF path (source tree or install dir)
- `perception` → selects sim or lidar node set
- `gui` → Gazebo GUI or headless
- All control/mission/metrics params exposed as launch args

### ROS2 ↔ Gazebo Bridge

File: `lhr_gazebo/config/ros_gz_bridge.yaml` — 11 bridged topics.

### Joint Command Adapter

File: `lhr_gazebo/lhr_gazebo/joint_cmd_adapter.py`

AckermannDriveStamped → 6 individual joint commands with proper Ackermann differential geometry.

## Gazebo Version Matrix

| Ubuntu | ROS 2 | Gazebo | Install |
|--------|-------|--------|---------|
| 24.04 | Jazzy | Harmonic (gz-sim 8) | `ros-jazzy-ros-gz` |

## File Structure

```
ros2/src/lhr_gazebo/
├── config/
│   ├── ros_gz_bridge.yaml          (11 topic bridges)
│   └── default.rviz
├── launch/
│   └── gazebo_demo.launch.py       (OpaqueFunction-based, auto-resolves world)
├── lhr_gazebo/
│   ├── __init__.py
│   └── joint_cmd_adapter.py
├── models/
│   ├── fsae_vehicle/
│   │   ├── model.sdf               (vehicle + IMU + LiDAR)
│   │   └── meshes/                  (carBody.stl, carTire.stl)
│   ├── cone_blue/
│   │   ├── model.sdf
│   │   └── meshes/cone.stl
│   ├── cone_yellow/                 (same structure)
│   ├── cone_orange_small/           (same structure)
│   └── cone_orange_large/           (same structure)
├── scripts/
│   └── generate_world.py           (--style autocross|oval|simple)
├── worlds/
│   ├── autocross_seed1.sdf
│   └── oval_seed1.sdf
├── package.xml
├── setup.py                        (installs worlds/*.sdf)
└── setup.cfg

ros2/src/lhr_perception/
├── lhr_perception/
│   ├── __init__.py
│   ├── cone_side_classifier.py     (geometry-based side inference)
│   └── lidar_cone_detector.py      (PointCloud2 → classified MarkerArray)
├── package.xml
├── setup.py
└── setup.cfg
```

## Key Debugging Lessons

1. **AckermannSteering plugin is too sluggish** — direct joint control gives near-instant response.
2. **Gazebo Harmonic LiDAR topic** is `/lidar/points/points` (not `/lidar/points`) for PointCloud2.
3. **LiDAR self-detection** — 360° LiDAR hits the car body. Requires vehicle exclusion zone filter in sensor frame.
4. **A single sensor-frame side check fails on curves** — accumulated local votes plus boundary continuity are required when both boundaries briefly appear on the same side of the sensor.
5. **Cone duplication during swerves** came from timestamp-mismatched transforms and many-to-one scan association; timestamp interpolation and one-to-one association address both causes.
6. **Many Delaunay width edges create midpoint branches** even with good detections. Side inference plus one-to-one cross-track assignment removes those branches.
7. **Vehicle spawn position matters** — spawn on the straightest section to avoid loop closure artifacts.
8. **`symlink-install` doesn't always update** Python files. When in doubt: `rm -rf build/<pkg> install/<pkg>`.
