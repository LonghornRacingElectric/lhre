# ZED 2i Stereo Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. The execution method must be selected by the user before implementation.

**Goal:** Add one ZED 2i with a 2.1 mm lens to the Gazebo vehicle, delivering
stereo RGB images and calibration in ROS/RViz.

**Architecture:** Two native camera sensors share a rigid camera-rig link and
configuration from `lhr_vehicle`. The generated model renders the pair;
an optional bridge and static TF publishers expose its calibrated images.
The existing perception and state-estimation modes consume their current inputs.

**Tech stack:** Python, YAML, SDFormat, ROS 2 Jazzy, Gazebo Harmonic,
`ros_gz_bridge`, `tf2_ros`, RViz, pytest/colcon, MkDocs.

**Spec:** [Approved design](zed2i-stereo.md).

## Global constraints

- One physical ZED 2i unit; 2.1 mm wide lens; 0.12 m baseline.
- Default per-view size 1280 x 720, rate 30 Hz in simulation time, ideal
  horizontal FOV 110 degrees. Intrinsics describe the rendered approximation.
- ASSUMED mount center `[1.8, 0.0, 0.65]` m in `base_link`, mount RPY
  `[0.0, 0.0, 0.0]` rad. Real calibration/mount measurements replace assumptions.
- `enable_stereo_camera` defaults to `false`; works in both perception modes
  and both truth/EKF modes.
- Preserve simulation timestamps; exact left/right pairing is required.
- ROS topics use `/lhr/sensor/zed2i/{left,right}/{image_rect,camera_info}`.
- Image and calibration frame IDs are `zed2i_left_camera_optical_frame` and
  `zed2i_right_camera_optical_frame`; optical axes are x right, y down, z forward.
- Zero distortion, identical intrinsics, identity rectification,
  left `P[3] = 0`, right `P[3] = -fx * 0.12`.
- Model edits go through the template/generator; commit generated SDF.
- Update adjacent documentation and pass `uv run --group docs mkdocs build --strict`.
- Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic is the runtime acceptance platform.
  Report unavailable runtime checks explicitly. Never run flashing targets.

## Review focus

- Malformed camera YAML must fail with a named field instead of producing
  invalid rendering/calibration (Task 1).
- A rotated mount must preserve the 0.12 m physical baseline and the SDF/TF
  agreement (Tasks 1 and 2).
- Disabled stereo must preserve the current bridges and RViz behavior, with
  no new camera subscribers (Task 3).
- EKF must retain sole ownership of `map -> base_link` while camera TF stays
  available (Task 3).
- Rendered stereo must agree with metadata, and camera information must be
  available when image consumers subscribe (Task 4).

## File responsibilities

All paths below are relative to `autonomy/ros2/` unless stated otherwise.

| File | Responsibility |
| --- | --- |
| `src/lhr_vehicle/config/vehicle.yaml` | Camera mount, baseline, lens label, and simulation optics |
| `src/lhr_vehicle/lhr_vehicle/__init__.py` | Validated camera configuration and pixel intrinsics |
| `src/lhr_gazebo/templates/model.sdf.in` | Rig link, fixed joint, and two camera sensors |
| `src/lhr_gazebo/scripts/generate_vehicle_model.py` | Render rig geometry and optics from vehicle configuration |
| `src/lhr_gazebo/models/fsae_vehicle/model.sdf` | Generated result |
| `src/lhr_gazebo/lhr_gazebo/stereo_camera.py` | Pure TF records and conditional launch actions |
| `src/lhr_gazebo/config/ros_gz_stereo_bridge.yaml` | Four camera bridge mappings |
| `src/lhr_gazebo/config/stereo.rviz` | Existing displays plus two stereo Image displays |
| `src/lhr_gazebo/launch/gazebo_demo.launch.py` | Enable option, camera actions, RViz selection |
| Package tests and adjacent READMEs | Behavioral checks and usage |

## Task 1: Validated stereo configuration

**Files:** Modify vehicle YAML, loader, `src/lhr_vehicle/test/test_vehicle.py`,
and `src/lhr_vehicle/README.md`.

**Interfaces:** Introduce a frozen `StereoCamera` dataclass with
`model: str`, `lens_focal_length_mm: float`, `position_m: Vec3`, `rpy_rad: Vec3`,
`baseline_m: float`, `image_width: int`, `image_height: int`,
`update_rate_hz: float`, `horizontal_fov_rad: float`; expose it as
`Vehicle.stereo_camera`. Properties `fx_px`, `fy_px`, `cx_px`, `cy_px`
are derived from size/FOV.

- [ ] Add failing default/geometry tests:

  ```python
  def test_stereo_camera_defaults():
      camera = load_vehicle().stereo_camera
      assert camera.model == 'zed2i'
      assert camera.lens_focal_length_mm == 2.1
      assert camera.baseline_m == 0.12
      assert (camera.image_width, camera.image_height) == (1280, 720)
      assert camera.update_rate_hz == 30.0
      assert camera.position_m == (1.8, 0.0, 0.65)
      assert camera.fx_px == pytest.approx(1280 / (2 * math.tan(math.radians(55))))
      assert camera.fy_px == camera.fx_px
  ```

- [ ] Add malformed-YAML tests by reading `config_path()` with `yaml.safe_load`,
  replacing one entry in `raw['sensors']['stereo_camera']`, writing it with
  `yaml.safe_dump` into `tmp_path`, then expecting `load_vehicle(path)` to raise
  `ValueError` naming that field. Parameterize baseline/rate at zero, negative,
  infinity and NaN; width/height at zero, negative, fractional, and boolean;
  FOV at 0, 180, NaN; position/RPY with wrong length, nonnumeric or nonfinite
  elements. Missing camera configuration also raises a clear `ValueError`.
- [ ] Run `PYTHONPATH=src/lhr_vehicle python3 -m pytest src/lhr_vehicle/test/test_vehicle.py -q`.
  New cases must fail before implementation; preserve existing tests.
- [ ] Add configuration:

  ```yaml
  stereo_camera:
    model: zed2i
    lens_focal_length_mm: 2.1
    baseline_m: 0.12
    position_m: [1.8, 0.0, 0.65]  # ASSUMED
    rpy_rad: [0.0, 0.0, 0.0]     # ASSUMED
    image_width: 1280
    image_height: 720
    update_rate_hz: 30.0
    horizontal_fov_deg: 110.0    # ideal pinhole approximation
  ```

- [ ] Parse with field-specific finite/range/type validation; reject booleans
  as integers and numeric strings as image dimensions. Use the formulas:

  ```python
  fx_px = image_width / (2.0 * math.tan(horizontal_fov_rad / 2.0))
  fy_px = fx_px
  cx_px = image_width / 2.0
  cy_px = image_height / 2.0
  ```

  Require `model == 'zed2i'` and `lens_focal_length_mm == 2.1` for this selected
  rig. Preserve these labels through loading and test rejection of a different
  model or lens. Validate before deriving intrinsics.

- [ ] Document the optical approximation and required model regeneration;
  run vehicle tests and lint, then commit only the Task 1 files on the feature branch.

## Task 2: Generated stereo rig

**Files:** Modify template, generator, generated SDF,
`src/lhr_gazebo/test/test_vehicle_model.py`, and `src/lhr_gazebo/README.md`.

**Consumes:** `Vehicle.stereo_camera` and its intrinsics from Task 1.
**Produces:** Two camera sensors on the same `zed2i_link`, rigidly attached to
`chassis`, with explicit Gazebo topics and optical IDs.

- [ ] Add XML tests against `gen.render(gen.load_source_vehicle())`:

  ```python
  def test_stereo_pair_shares_rig_and_baseline():
      gen = _generator()
      root = ET.fromstring(gen.render(gen.load_source_vehicle()))
      rig = root.find("./model/link[@name='zed2i_link']")
      cameras = rig.findall("sensor[@type='camera']")
      assert len(cameras) == 2
      ys = [float(sensor.findtext('pose').split()[1]) for sensor in cameras]
      assert abs(ys[0] - ys[1]) == pytest.approx(0.12)
      assert all(sensor.findtext('update_rate') == '30' for sensor in cameras)
  ```

- [ ] Test 1280x720 RGB format, equal HFOV, explicit separate image/info topics,
  exact optical frame IDs, zero distortion, and valid nonzero pixel intrinsics.
  Compare render geometry using a dataclass-replaced vehicle with nonzero rig
  yaw/pitch: local camera separation must remain 0.12 m along rig y. Confirm
  joint parent/child and the rig pose's `relative_to` frame explicitly.
- [ ] Run model tests and confirm the new XML tests fail.
- [ ] Add one sensor-only rigid link. Its pose is relative to `chassis`, using
  mount position minus the chassis origin and the configured RPY. Attach it
  with a fixed joint. Give it explicit negligible simulation inertia (mass
  0.001 kg, diagonal inertia 0.000001 kg m2) and no collision geometry; document
  this bookkeeping approximation so an omitted SDF inertia cannot add a
  default kilogram to the car.
- [ ] Add left/right sensor poses `(0, +baseline/2, 0)` and
  `(0, -baseline/2, 0)` in the rig. Each uses `type="camera"`, equal update
  rate, `R8G8B8`, clips 0.1/100 m, explicit topics, and optical frame ID.
  Set lens intrinsics to Task 1 values, centered principal point, zero skew,
  and zero distortion. Keep both sensors on the rig link so mount rotation
  does not change Harmonic's y-separation baseline calculation.

  The left sensor structure is:

  ```xml
  <sensor name="zed2i_left" type="camera">
    <pose>0 ${stereo_half_baseline} 0 0 0 0</pose>
    <always_on>true</always_on>
    <update_rate>${stereo_rate}</update_rate>
    <topic>/zed2i/left/image_rect</topic>
    <camera>
      <horizontal_fov>${stereo_hfov}</horizontal_fov>
      <image><width>${stereo_width}</width><height>${stereo_height}</height><format>R8G8B8</format></image>
      <clip><near>0.1</near><far>100</far></clip>
      <camera_info_topic>/zed2i/left/camera_info</camera_info_topic>
      <optical_frame_id>zed2i_left_camera_optical_frame</optical_frame_id>
      <lens>
        <intrinsics><fx>${stereo_fx}</fx><fy>${stereo_fy}</fy><cx>${stereo_cx}</cx><cy>${stereo_cy}</cy><s>0</s></intrinsics>
      </lens>
    </camera>
  </sensor>
  ```

  The right sensor uses name `zed2i_right`, negative local y, and `/right/`
  topics plus `zed2i_right_camera_optical_frame`. Its physical pose supplies
  the baseline; Harmonic supplies the stereo projection translation.
- [ ] Extend generator substitutions for all camera values. Preserve adequate
  precision for pixel intrinsics and baseline; avoid changing unrelated
  existing model formatting.
- [ ] Regenerate and run:

  ```bash
  python3 src/lhr_gazebo/scripts/generate_vehicle_model.py
  python3 src/lhr_gazebo/scripts/generate_vehicle_model.py --check
  PYTHONPATH=src/lhr_vehicle python3 -m pytest src/lhr_gazebo/test/test_vehicle_model.py -q
  ```

- [ ] Update the Gazebo README with model assumptions, run lint, and commit
  template/generator/generated output/tests/docs together.

## Task 3: Optional ROS bridge, camera TF, and RViz

**Files:** Create `stereo_camera.py`, `ros_gz_stereo_bridge.yaml`, `stereo.rviz`,
`src/lhr_gazebo/test/test_stereo_camera.py`; modify launch, package manifest,
Gazebo README, and `README.md` in the ROS workspace.

**Consumes:** `Vehicle.stereo_camera`.
**Produces:** `camera_transforms(camera: StereoCamera) -> list[dict]`, whose
records contain `parent`, `child`, `translation` tuple and `rpy` tuple;
`stereo_actions(camera: StereoCamera, config_dir: str) -> list` converts
the records into named `tf2_ros/static_transform_publisher` Nodes and a
separate named `ros_gz_bridge/parameter_bridge` Node.

- [ ] Test five static transforms and their axes:

  ```python
  def test_stereo_transform_chain():
      camera = load_vehicle().stereo_camera
      by_child = {t['child']: t for t in camera_transforms(camera)}
      assert by_child['zed2i_link']['parent'] == 'base_link'
      assert by_child['zed2i_link']['translation'] == camera.position_m
      assert by_child['zed2i_left_camera_frame']['translation'] == (0.0, 0.06, 0.0)
      assert by_child['zed2i_right_camera_frame']['translation'] == (0.0, -0.06, 0.0)
      assert by_child['zed2i_left_camera_optical_frame']['rpy'] == (-math.pi/2, 0.0, -math.pi/2)
  ```

- [ ] Numerically compose the transforms for a nonzero mount yaw/pitch and
  verify forward/right/down axes and 0.12 m separation; compare with the rig
  and sensor poses rendered by Task 2. This catches coordinate-convention
  errors beyond checking identical constants.
- [ ] Extend the existing `_launch_setup` action-capture test pattern to all
  `sim/lidar` x `truth/ekf` x `true/false` combinations. Defaults must produce
  no camera actions. Enabled cases must produce one stereo bridge and five
  static transform nodes; stereo transforms never remap to `/tf_truth`.
  Existing main bridge EKF remappings remain intact.
- [ ] Test bridge YAML has exactly four unique mappings, GZ_TO_ROS direction,
  `gz.msgs.Image` / `sensor_msgs/msg/Image` and `gz.msgs.CameraInfo` /
  `sensor_msgs/msg/CameraInfo`. Test RViz defaults remain the same when disabled
  and both enabled Image topics match the bridge configuration.
- [ ] Run new tests before implementation and confirm failures.
- [ ] Implement transforms with the rig center/RPY, local y offsets, and
  optical RPY above. Static publisher arguments use explicit `--x`, `--y`,
  `--z`, `--roll`, `--pitch`, `--yaw`, `--frame-id`, `--child-frame-id` options.
  Use `use_sim_time=True` where appropriate.

  The pure record builder follows this interface:

  ```python
  def camera_transforms(camera):
      transforms = [{'parent': 'base_link', 'child': 'zed2i_link',
                     'translation': camera.position_m, 'rpy': camera.rpy_rad}]
      for side, sign in (('left', 1.0), ('right', -1.0)):
          frame = f'zed2i_{side}_camera_frame'
          transforms.extend([
              {'parent': 'zed2i_link', 'child': frame,
               'translation': (0.0, sign * camera.baseline_m / 2.0, 0.0),
               'rpy': (0.0, 0.0, 0.0)},
              {'parent': frame, 'child': f'zed2i_{side}_camera_optical_frame',
               'translation': (0.0, 0.0, 0.0),
               'rpy': (-math.pi / 2.0, 0.0, -math.pi / 2.0)},
          ])
      return transforms
  ```
- [ ] Add a dedicated four-entry bridge YAML with sensor-data QoS and lazy
  subscriptions. CameraInfo must still flow with image consumers; verify the
  installed bridge supports the selected QoS/lazy fields. Add `sensor_msgs`
  to package dependencies.

  Each image mapping has this shape; use the explicit counterpart topic and
  type for camera information, and repeat for the right side:

  ```yaml
  - gz_topic_name: /zed2i/left/image_rect
    ros_topic_name: /lhr/sensor/zed2i/left/image_rect
    gz_type_name: gz.msgs.Image
    ros_type_name: sensor_msgs/msg/Image
    direction: GZ_TO_ROS
    qos_profile: SENSOR_DATA
    lazy: true
  ```
- [ ] Declare `enable_stereo_camera` with default `false` and boolean choices.
  Resolve it in `_launch_setup`; import/call camera actions only when true.
  Select `stereo.rviz` when enabled, preserving `default.rviz` otherwise.
  Build stereo.rviz from the current default with two Image displays using
  matching sensor-data subscriber QoS. Existing `setup.py` globs already
  install `config/*`.
- [ ] Document exact launch usage and rebuild requirements:

  ```bash
  ./scripts/run_gazebo_demo.sh track_style:=oval perception:=lidar \
    estimator:=truth enable_stereo_camera:=true
  ```

- [ ] Run the launch tests in ROS, package lint, and commit Task 3 files.
  Without ROS, run pure helper/XML/YAML tests and explicitly leave launch
  evaluation and node startup unverified.

## Task 4: Runtime acceptance and final integration

**Files:** Update `src/lhr_gazebo/README.md`, workspace `README.md`,
`autonomy/docs/plans/camera-fusion.md`, and this plan with actual evidence.
If a fixture is needed, create `src/lhr_gazebo/test/stereo_target.sdf` alongside
existing tests so it does not create a new undocumented code directory.

**Consumes:** Generated model, optional launch integration, approved runtime
acceptance criteria. **Produces:** Verified results or explicit unresolved
runtime checks, with no invented pass claims.

- [ ] Build and run the relevant test suite in the supported ROS environment:

  ```bash
  ./scripts/build.sh
  colcon test --packages-select lhr_vehicle lhr_gazebo lhr_perception lhr_state_estimation
  colcon test-result --verbose
  ```

- [ ] On a machine with a display, run the documented camera launch command.
  Pause mission motion using `auto_go:=false` for the initial image check.
  Read topic metadata, sample calibration, TF, and image rates:

  ```bash
  ros2 topic info -v /lhr/sensor/zed2i/left/image_rect
  ros2 topic echo --once /lhr/sensor/zed2i/right/camera_info
  ros2 run tf2_ros tf2_echo base_link zed2i_left_camera_optical_frame
  ros2 topic hz /lhr/sensor/zed2i/left/image_rect
  ros2 topic hz /lhr/sensor/zed2i/right/image_rect
  ```

- [ ] Collect 100 four-way equal-timestamp sets using temporary rclpy
  subscriptions to the four image/info topics, keyed by
  `(msg.header.stamp.sec, msg.header.stamp.nanosec)`. Use sensor-data QoS,
  spin until 100 complete sets or 120 wall-clock seconds, and fail the check
  on timeout. For each set assert RGB8/1280x720, nonempty correctly sized
  pixel payloads, matching image/info frame IDs and timestamps, increasing
  stamps, equal K matrices, identity R, zero D, left P translation zero,
  and `-right.p[3] / right.p[0] == 0.12` within `1e-6` m. Keep temporary
  recordings and scripts outside tracked source unless a reusable tool is
  explicitly warranted; no raw logs or bags enter git.
- [ ] Use a fixed colored/textured target whose front face is measured 5 m
  along camera-forward from the rig center. Place it in a stationary fixture
  world using the generated vehicle model and the same Sensors plugin. At
  default intrinsics expected horizontal disparity is about 10.76 pixels;
  measure corresponding target corners in both images and require the
  approved two-pixel horizontal/vertical tolerances. Confirm RViz scene
  visibility and TF in both truth and EKF modes. Save a concise result table
  and selected evidence, distinguishing runtime facts from assumptions.
- [ ] Measure real-time factor with Gazebo statistics and wall-clock image
  throughput. Disable the camera bridge/RViz subscribers and confirm camera
  image rendering becomes lazy; initialization overhead may remain. Do not
  equate simulation-time update rate with wall-clock throughput.
- [ ] Update docs with observed results, limitations, and follow-up for
  physical calibration; run `uv run --group docs mkdocs build --strict` and
  `git diff --check`. If runtime is unavailable, retain an explicit pending
  acceptance list instead of marking this milestone fully validated.
- [ ] Review the whole diff against the approved design, commit only owned
  files, and prepare a PR titled `Autonomous: add ZED 2i stereo simulation`.
  Do not include the pre-existing root `AGENTS.md` edit. Publishing a PR is
  a separate step from this implementation plan's approval.

## Execution environment and handoff

Use an isolated feature worktree at execution time. The current source is
local main `5719b088f226b4a9f60d34cbe53e8ead768f8e31`; planning changes are
uncommitted and must be carried into that worktree without disturbing the
pre-existing root `AGENTS.md` modification. Do not depend on unmerged PR 83.

The current Mac has Docker/Colima installed, but the daemon is stopped and
ROS is unavailable on the host. Pure configuration/generator checks can run
locally; the ROS launch, stereo stream, and visual checks require the
documented Ubuntu environment. No GUI demo scripts run from a headless agent.

Recommended execution: native implementation in this session, with one
whole-branch review afterward. The three code tasks share configuration,
generated geometry, and frame interfaces, making sequential implementation
straightforward. The user must review this plan and select native or
subagent-driven execution before implementation starts.
