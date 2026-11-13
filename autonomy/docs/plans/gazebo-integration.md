# Gazebo Integration Plan

## Goal

Replace the kinematic vehicle simulator (`lhr_sim_kinematic`) with a full Gazebo physics simulation with realistic sensor-based perception, targeting Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic.

## Current Status

**Phase 1 (Gazebo physics):** Complete.
**Phase 2 (LiDAR perception):** Complete for the MVP: `perception:=lidar estimator:=ekf` laps autocross seeds 1 to 5 and finishes acceleration in the [gate dry runs](../../testing/2026-10-28-mvp-gate/README.md). Cones are unlabeled by default and paired by Delaunay boundary; side inference is opt-in (`lidar_classify_sides:=true`).
**Phase 3 (Camera fusion + tuning):** Partial: ZED 2i stereo ([#96](https://github.com/LonghornRacingElectric/lhre/pull/96)) and an HSV debug classifier exist; LiDAR fusion is [#100](https://github.com/LonghornRacingElectric/lhre/issues/100). See [camera-fusion.md](camera-fusion.md) for detailed plan.

## What Works Today

- Gazebo physics sim with direct joint control (no AckermannSteering plugin)
- Sim perception mode (`perception:=sim`): ground-truth colored cones, Delaunay boundary pairing, fully reliable
- LiDAR perception mode (`perception:=lidar`): pointcloud clustering into unlabeled cones, Delaunay boundary pairing. Geometry side inference with one-to-one `classified` pairing is opt-in (`lidar_classify_sides:=true`)
- Oval track (`track_style:=oval`) — LiDAR pipeline completes laps reliably
- Autocross seeds 1 to 5 and acceleration: the LiDAR + EKF stack passed all 18 runs in [gate dry runs](../../testing/2026-10-28-mvp-gate/README.md) 2 and 3
- `OpaqueFunction`-based launch file auto-resolves world SDF from `track_style` + `seed`
- RViz runs alongside Gazebo with centerline, cones, and odometry visualization

## Known Issues

1. **Residual cone-map drift** — timestamp-aligned transforms and one-to-one scan association remove the known sharp-turn duplication mechanism. EKF drift can still move the persistent map and needs visual and on-car validation.

2. **Sparse-map side labels** (only with `lidar_classify_sides:=true`, which is opt-in): the live LiDAR scan contained fewer cones than the generated-track tests assumed. The initial Delaunay graph connected a right-boundary fragment to the left, and immutable labels preserved the mistake. One-to-one pairing cannot correct a wrong side label.

3. **Path continuity:** partly addressed by [#83](https://github.com/LonghornRacingElectric/lhre/pull/83). `constrained_greedy` chaining stops at the last valid point instead of jumping a gap, and pursuit stops when the path goes stale (`path_timeout_sec`). On a partial map the path can still run short.

4. **No measured cone colour** — LiDAR infers the track side from vehicle-relative observations and boundary continuity. A camera is still needed to measure blue/yellow colour and independently validate the inference.

5. **Backwards path wrapping** — the chain starts from the vehicle and goes forward, then wraps backwards through midpoints behind the vehicle. Cosmetic only (pure pursuit ignores the backwards portion) but messy in RViz.

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

**Status:** done in [#83](https://github.com/LonghornRacingElectric/lhre/pull/83). The default `chaining_alg:=constrained_greedy` bounds each step (`min_step_m`, `max_step_m`) and its turn from the path heading (`max_turn_deg`), and stops at the last valid point instead of jumping a gap ([chaining](../../ros2/src/lhr_track_builder/README.md#chaining)). The analysis below predates it.

When side labels are correct, minimum-cost one-to-one pairing removes duplicated midpoint candidates. Live validation has already shown wrong labels and an unsafe partial path. Rework side inference with reversible labels or measured colour, then evaluate ordering on partial maps. Possible continuity constraints include:
- Add a maximum step distance to the chain (skip midpoints that are too far from the last chained point)
- Use angular continuity — prefer the next point that continues roughly in the same direction
- Only chain midpoints within a forward arc of the vehicle (ignore midpoints behind)

#### 2b.3 — Validate on autocross

Test with `track_style:=autocross perception:=lidar` and iterate on the above until the car completes laps.

**Status:** done. The MVP gate dry runs lap autocross seeds 1 to 5 with `perception:=lidar estimator:=ekf` ([write-up](../../testing/2026-10-28-mvp-gate/README.md)).

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

Measured colours would give the existing one-to-one pairing strategy more
reliable left/right inputs than geometry alone.

#### 3.3 — Update track builder for fused perception

**File:** `lhr_track_builder/track_builder_node.py`

With camera-classified cones, use the existing one-to-one `'classified'` pairing strategy with measured sides and confidence. Fall back to `'boundary'` for unclassified cones.

### Phase 4 — Fidelity Tuning

Goal: make the simulation match the real car closely enough for control parameter transfer.

- Tune tire friction, mass distribution, and steering dynamics to match real car
- Add sensor noise models (camera exposure variation; LiDAR range noise is done, [#161](https://github.com/LonghornRacingElectric/lhre/pull/161))
- Test at competition speeds (up to 15 m/s)
- Validate against real car telemetry data
- Add GNSS correction and validate the existing IMU/wheel-speed EKF against car data

---

## Architecture

```
PHASE 1 (perception:=sim)            PHASE 2 (perception:=lidar)
─────────────────────────            ──────────────────────────
lhr_trackgen (ground-truth cones)    Gazebo gpu_lidar sensor (range noise)
lhr_sensor_sim (FOV filter)          lhr_perception (pointcloud → unlabeled cones)
Gazebo physics (joint control)       Gazebo physics (joint control)

lhr_track_builder (boundary)     →   STAYS (classified with lidar_classify_sides:=true)
lhr_state_estimation             →   STAYS (EKF with estimator:=ekf, else Gazebo truth)
lhr_control                      →   STAYS (pure pursuit + curvature speed planning)
lhr_mission_manager              →   STAYS (FSAE state machine)
lhr_safety                       →   STAYS (fault checks + heartbeat with safety:=true)
lhr_metrics                      →   STAYS (CTE, lap detection, CSV output)
```

Phase 3 adds a camera branch. The ZED 2i stereo input and an HSV debug classifier are in; fusing measured colour into the LiDAR cones is [#100](https://github.com/LonghornRacingElectric/lhre/issues/100). `track_builder`'s boundary pairing already rejects same-colour edges, so labels only need to reach it.

## Implemented Components

### Vehicle Model (SDF)

File: `lhr_gazebo/models/fsae_vehicle/model.sdf`

- Ackermann steering geometry (front two wheels steer, rear two driven)
- Geometry, masses and steering limits come from `lhr_vehicle/config/vehicle.yaml` (Orion: wheelbase 1.549 m, track 1.212 m, wheel radius 0.2045 m); `model.sdf` is generated from it
- Direct joint control (JointPositionController + JointController) — NOT AckermannSteering
- Sensors: IMU (100 Hz), GPU LiDAR (1800x16, 0.9 to 100 m, 10 Hz, Gaussian range noise from `sensors.lidar.range_noise_std_m` in `vehicle.yaml`, 0.02 m). The ZED 2i pair is only in the generated `model_stereo.sdf` ([#160](https://github.com/LonghornRacingElectric/lhre/pull/160))

### Cone Models + World Generation

Script: `lhr_gazebo/scripts/generate_world.py`

- `--style` flag selects generator: `autocross` (Catmull-Rom spline), `oval` (arc-length ellipse), `simple` (wobble oval), `acceleration` (straight lane)
- `--seed`, `--num-waypoints`, `--radius`, `--jitter`, `--width`, `--cone-spacing` parameters
- Vehicle spawn auto-computed on straightest track section
- ODE physics at 1 kHz, real-time factor 1.0
- `gz-sim-sensors-system` with Ogre2 render engine
- Cones are static with collision meshes; `lhr_metrics` scores contacts as `cones_hit` ([#156](https://github.com/LonghornRacingElectric/lhre/pull/156))
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

The package's worlds, models, generators and nodes are documented in the [lhr_gazebo README](../../ros2/src/lhr_gazebo/README.md).

## Key Debugging Lessons

1. **AckermannSteering plugin is too sluggish** — direct joint control gives near-instant response.
2. **Gazebo Harmonic LiDAR topic** is `/lidar/points/points` (not `/lidar/points`) for PointCloud2.
3. **LiDAR self-detection** — 360° LiDAR hits the car body. Requires vehicle exclusion zone filter in sensor frame.
4. **A single sensor-frame side check fails on curves** — accumulated local votes plus boundary continuity are required when both boundaries briefly appear on the same side of the sensor.
5. **Cone duplication during swerves** came from timestamp-mismatched transforms and many-to-one scan association; timestamp interpolation and one-to-one association address both causes.
6. **Many Delaunay width edges create midpoint branches** even with good detections. Side inference plus one-to-one cross-track assignment removes those branches.
7. **Vehicle spawn position matters** — spawn on the straightest section to avoid loop closure artifacts.
8. **`symlink-install` doesn't always update** Python files. When in doubt: `rm -rf build/<pkg> install/<pkg>`.
