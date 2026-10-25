# lhr_sim_fmu

Run Orion's full BobLib Modelica vehicle through FMI 2.0 co-simulation.
The upstream CVODE solver advances the model. The existing ROS simulator
interface publishes `/clock`, rear-axle odometry and TF after accepted steps.
The default plant remains kinematic. Gazebo keeps its own dynamics; this
package is selected only by the MVS launcher.

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
| `fmu_path` | empty | Artifact path; adjacent `provenance.json` is required |
| `pedal_gain` | 0.1 | Pedal demand per m/s of speed error |
| `command_timeout_sec` | 0.5 | Brake toward zero speed after commands expire |

The MVS launcher exposes `fmu_path` and `fmu_pedal_gain` with typed startup
checks in both perception and estimator modes. It records `fmu_pedal_gain`
in metrics. Shared initial pose, clock, odometry and EKF remappings match the
other plants. Changing parameters is a startup operation, not live tuning.

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
The simulation clock starts at ROS time 1 s, while the FMU starts at 0 s;
accepted step increments are identical.

The loader rejects altered artifact/vehicle/tire bytes and a different BobSim
calibration commit. It publishes the complete manifest plus the autonomy
vehicle hash on transient-local `/lhr/sim/provenance`. Keep that message or
the manifest with study outputs; CSV alone does not carry the FMU hash.

## Tests and limitations

```bash
colcon test --packages-select lhr_sim_fmu lhr_demo --return-code-on-test-failure
# Opt in to the actual compiled-model test; no substitute model is used:
LHR_TEST_FMU=/opt/lhr/simulation/out/fmu/OrionFMI.fmu \
  colcon test --packages-select lhr_sim_fmu --return-code-on-test-failure
```

CI runs interface/error/provenance tests without building a large native FMU.
The native test skips unless `LHR_TEST_FMU` is set; if set, a missing or stale
artifact fails. It covers waiting, launch, both steering directions, stopping and
relaunch. It also checks that compiled aero references match our car rather
than the different BobLib defaults. Launch tests evaluate flags and start the node with an injected
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
