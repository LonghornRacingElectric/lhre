# lhr_sim_fmu

Run Orion's full BobLib Modelica vehicle through FMI 2.0 co-simulation.
The upstream CVODE solver advances the model. The existing ROS simulator
interface publishes `/clock`, rear-axle odometry and TF after accepted steps.
The MVS default plant remains kinematic. The Gazebo launcher also supports
`plant:=fmu`, with Gazebo rendering sensors at the FMU pose; its default
remains `plant:=gazebo`.

## Build and run

Build the artifact in the simulation container, then build the ROS workspace:

```bash
make -C simulation fmu
# In the autonomy ROS container, from autonomy/ros2:
colcon build --symlink-install --packages-up-to lhr_demo
source install/setup.bash
scripts/run_headless.sh plant:=fmu track_style:=oval v_max:=5 timeout_sec:=180
```

Install `fmpy==0.3.32` with pip in a native ROS environment. The autonomy
Docker image includes it after rebuilding. Building the artifact needs
OpenModelica 1.27.1 and CMake from the simulation image; running it does not.
The standard Docker mount resolves the artifact at
`/opt/lhr/simulation/out/fmu/OrionFMI.fmu`. With a native checkout, set
`BOBSIM_ROOT` and `BOBSIM_VEHICLE` as described for the
[DynPy plant](../lhr_sim_bobsim/README.md). An empty `fmu_path` resolves from
`BOBSIM_ROOT` to the adjacent `out/fmu/OrionFMI.fmu`.

| Node parameter | Default | Meaning |
| --- | --- | --- |
| `external_clock` | false | Subscribe to Gazebo time instead of using the MVS timer |
| `fmu_path` | empty | Artifact path; adjacent `provenance.json` is required |
| `pedal_gain` | 0.1 | Pedal demand per m/s of speed error |
| `command_timeout_sec` | 0.5 | Brake toward zero speed after commands expire |

The MVS launcher exposes `fmu_path` and `fmu_pedal_gain` with typed startup
checks in both perception and estimator modes. It records `fmu_pedal_gain`
in metrics. Shared initial pose, clock, odometry and EKF remappings match the
other plants. Changing parameters is a startup operation, not live tuning.

## Gazebo clock mode

See the [Gazebo guide](../lhr_gazebo/README.md#fmu-motion) for launch flags,
sensor limitations and observed pose-lag logging. Both perception branches
forward artifact, pedal gain and command timeout with explicit ROS types.
`external_clock=true` cancels the inherited timer and cannot be combined with
`publish_clock=true`. The first positive Gazebo clock stamp anchors native
FMU time zero. Each complete 20 ms interval advances the plant once; duplicate
clock samples do nothing. Odometry and TF carry the accepted integration time,
which never exceeds the received clock. Rewinding the world fails the node;
restart the launch to create a fresh FMI instance.

The fixed-step catch-up loop preserves elapsed simulation time; it is not a
real-time speed guarantee. There is no second `/clock` publisher in FMU Gazebo
mode. Initial pose comes from the world's saved vehicle spawn.

## Gazebo timing report

Gazebo FMU mode now defaults to `fmu_lockstep:=true`. The coordinator pauses
simulation time while the plant solves and its pose is sent to Gazebo. It
allows the next interval only after that handshake. `fmu_step_sec:=0.02`
sets both the plant integration interval and the coordinated physics batch.
It must be a positive integer multiple of the world's physics step and less
than 0.1 s. Missing states, failed control/pose requests and backward time
jumps fail the run. `fmu_lockstep:=false` selects the earlier asynchronous
comparison mode. MVS behavior and the default Gazebo dynamics plant are unchanged.

`/lhr/sim/fmu_step_timing` publishes one JSON record per accepted step. Its
wall duration includes the driver adapter, native FMI call and output mapping;
it excludes waiting for Gazebo and rendering. Slow steps are reported, not
hidden. The report shows their mean, nearest-rank p95, maximum, deadline
misses and simulated seconds per plant-compute second. Its observed real-time
factor also includes the coordinator and Gazebo overhead.

Start the observer before the launch, in separate terminals with the same
`ROS_DOMAIN_ID` and the workspace sourced:

```bash
ros2 run lhr_sim_fmu timing_report --seconds 135 \
  --output data/fmu-timing/cameras-off.json
# Other terminal, with a display available to the renderer:
timeout -s INT -k 15 130 ros2 launch lhr_gazebo gazebo_demo.launch.py \
  plant:=fmu perception:=lidar estimator:=ekf safety:=true \
  safety_lidar_timeout_s:=1.5 gui:=false rviz:=false enable_metrics:=false
```

The 1.5 s LiDAR watchdog is the existing CPU-rendering diagnostic setting;
the launch default remains 0.3 s. It does not change the 0.1 s pose-age
acceptance limit.

Repeat with observer `--stereo` and launch `enable_stereo_camera:=true`.
Reports include artifact provenance and raw timing/pose-age samples. Keep
these files in ignored run-data storage. `timing_passed` requires measured
4–5 m/s motion, an explicit FMU clock origin, each selected sensor stream,
no unmatched frames after its first observed accepted integration step, and a maximum observed age below 0.1 s. A missing stream or an empty
speed band fails; a slow solver can pass when simulation time waits for it.
Frames before the first observed accepted integration step are retained and
counted as `pre_start`. This excludes the initial two-tick clock handshake
and sensor frames rendered before any FMU step is available. The boundary
is the recorded clock origin plus the first observed accepted FMU time. A missing timebase
fails acceptance.

This checks the sampled pose-age diagnostic from the Gazebo guide, not
per-ray/per-pixel sensor truth or a full driving gate.

### Mac container result (2026-11-06)

Two autocross seed-1 measurements used 135 s observer windows and 130 s
launch deadlines, including shutdown. Both used LiDAR/EKF, the gate's default
5 m/s speed limit, `fmu_lockstep:=true` and `fmu_step_sec:=0.02`. Each launch
used a different automatically generated Gazebo transport partition. Both
passed the sampled timing check. Safety stayed enabled with the existing
1.5 s CPU LiDAR watchdog override. The 0.3 s default still tripped
`LIDAR_STALE` in an earlier READY run; that stationary report failed its
unmeasured-speed check. This work does not establish sensor throughput or
fix that separate freshness failure.

| At 4–5 m/s | Cameras off | Stereo on |
| --- | --- | --- |
| Accepted steps in speed band | 849 | 968 |
| Plant call mean | 26.24 ms | 27.51 ms |
| Plant call p95 | 47.27 ms | 50.54 ms |
| Plant call maximum | 95.35 ms | 241.11 ms |
| Calls over the 20 ms interval | 511 | 615 |
| Plant-compute speedup | 0.762x | 0.727x |

Overall observed simulation/wall-time ratios were 0.230x with cameras off and
0.247x with stereo on. These include startup, waiting and driving.

| Sensor pose-age observations | Count | Mean | Maximum |
| --- | --- | --- | --- |
| Cameras-off LiDAR | 137 | 19.00 ms | 19.00 ms |
| Stereo-on LiDAR | 139 | 19.00 ms | 19.00 ms |
| Stereo left | 362 | 28.64 ms | 38.00 ms |
| Stereo right | 350 | 28.10 ms | 38.00 ms |

No post-bootstrap observations were unmatched. The stereo report retained
1 LiDAR, 4 left-image and 3 right-image bootstrap frames as `pre_start`.
Pose age is sampled from Gazebo's model-pose observations; camera observation
age can add up to 19 ms. These are bounded timing measurements, not a lap,
a full gate pass, a guaranteed real-time rate or per-pixel sensor validation.
The native pull-away solver failure and native IMU/wheel work (#205) remain
independent.

The runs used the current artifact `fe1aadc873f7852993faf0691467093d78e83f9730f4efc52f79f83ceed2328b`,
BobSim `f58f932`, BobLib `75b6e03` and canonical vehicle hash
`c4dc19eb29749755054802385e6930261e6ec3720bc7e4730fb217cdc4acaaa0`.
The JSON reports retain complete artifact provenance, timing samples and pose
samples in ignored `data/issue203/` storage. Both runs kept the same settings;
no solver tolerance or model input was changed to meet the pose-age limit.

## Vehicle and command contract

[The build](../../../../simulation/docs/fmu.md) uses the single canonical
`vehicle/vehicle.yml` and tire files. It supports Orion's bellcrank/stabar
front and rear axles. It binds the generated record to the driver-input-only
VehicleFMI entry point. Driver, VCU, motor, battery, brakes, tires and chassis
remain inside the full model. It does not use VehicleSim's built-in maneuver.

The speed adapter applies exclusive accelerator/brake pedals in [0, 1].
`fmu_pedal_gain` is a proportional driver gain, not a commanded acceleration.
The P-only driver can settle below the requested speed, and brake demand
fades as speed approaches zero. Stops can take longer than in the kinematic
plant; do not reuse its stopping-distance baseline.
The driver uses the model longitudinal velocity as ideal speed feedback, as
in the DynPy adapter. Speed-sensor errors in this actuation loop are not modeled.
Road-wheel steering uses the canonical vehicle's public KinPy rack mapping
and rack travel per handwheel revolution. YAML steering angle/rate and speed
limits bound requests. Actuator response remains inside Modelica. No state
is clamped to zero at rest, so settling and small creep remain observable.

The wrapper uses the rear axle midpoint (y=0 at the nominal wheel-center
height), projected into XY. It exports heading and twist from the chassis
frame. The loader checks that reference against the canonical vehicle.
Roll, pitch and suspension remain internal; the ROS interface is planar. One fixed map transform places the native start at the requested
ROS pose. The adapter does not integrate yaw rate or velocity to invent pose.
In MVS mode the simulation clock starts at ROS time 1 s, while the FMU starts at 0 s;
accepted step increments are identical.

The loader rejects altered artifact/vehicle/tire bytes and a different BobSim
calibration commit. It publishes the complete manifest plus the autonomy
vehicle hash on transient-local `/lhr/sim/provenance`. Keep that message or
the manifest with study outputs; CSV alone does not carry the FMU hash.
The commit check trusts only the selected BobSim path for that Git command,
so a container can read a host-owned mount. It reports Git's diagnostic if
the checkout is missing or unreadable. It does not change global Git settings.

## Tests and limitations

```bash
colcon test --packages-select lhr_sim_fmu lhr_demo --return-code-on-test-failure
# Opt in to the actual compiled-model test; no substitute model is used:
LHR_TEST_FMU=/opt/lhr/simulation/out/fmu/OrionFMI.fmu \
  colcon test --packages-select lhr_sim_fmu --return-code-on-test-failure
# With the ROS and workspace setup sourced, check the aero test on its own:
LHR_TEST_FMU=/opt/lhr/simulation/out/fmu/OrionFMI.fmu \
  python3 -m pytest src/lhr_sim_fmu/test/test_native_fmu.py -k aero -q
```

CI runs interface/error/provenance tests without building a large native FMU.
The native test skips unless `LHR_TEST_FMU` is set; if set, a missing or stale
artifact fails. It covers waiting, launch, both steering directions, stopping and
relaunch. It also checks that compiled aero references and front brake bias match our
car rather than the BobLib defaults. Launch tests evaluate flags and start the node with an injected
plant to check types and EKF routing without pretending to test physics.

Any rejected FMI step or nonfinite state fails the process. Native resources
are released, and that step publishes neither odometry nor clock. A failed
plant exits the launch; the headless runner cannot count an incomplete row
as a pass.

BobLib [issue 32](https://github.com/BobDyn/BobLib/issues/32) still documents
motor-latch chatter on some brake-hold/relaunch commands. Our tested drive
profile passing does not resolve that issue. OpenModelica can report a failed
nonlinear iteration and recover with a smaller internal step; an accepted
FMI step is the runtime boundary. Arm64 FMPy may also print that its optional
logger proxy is unavailable; native solver messages are still visible.

This is a Linux/CPU-specific development plant, not hardware correlation or
proof of the full MVP gate. The MVP still selects Gazebo, EKF and noisy LiDAR.
