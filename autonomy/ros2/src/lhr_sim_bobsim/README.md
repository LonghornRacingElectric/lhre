# lhr_sim_bobsim

Run BobSim's public `engines.dynpy` 3 DOF model as the ROS vehicle plant. The model
imports the pinned submodule; no BobSim source is copied here. The existing
kinematic model remains the default.

## Run

Initialize `simulation/bobsim` and its submodules from the repository root:

```bash
git submodule update --init --recursive simulation/bobsim
```

Docker Compose mounts the repository read-only at `/opt/lhr`, including
Git metadata needed to identify the BobSim submodule. Recreate the
container after updating Compose, build the ROS workspace, then launch:

```bash
ros2 launch lhr_demo mvs_demo.launch.py plant:=bobsim perception:=lidar
```

For native Ubuntu, set `BOBSIM_ROOT` to the absolute `simulation/bobsim`
checkout and `BOBSIM_VEHICLE` to the absolute `vehicle/vehicle.yml`. Install
BobSim's runtime Python dependencies in the ROS environment (NumPy, SciPy,
PyYAML and pandas). This uses the existing pinned model and does not require
an FMU or OpenModelica.

## Commands and frames

The input remains `/lhr/vehicle/cmd` (`AckermannDriveStamped`). Speed is a
forward target, not an immediate velocity change. A proportional speed loop
(default gain 2 /s) requests acceleration, limited by `max_accel` (2 m/s²)
and `max_decel` (3 m/s²). Drive force also respects BobSim's peak driveline
force and power. Wheel torque distribution and brake bias come from the
canonical dynamics vehicle file. Road-wheel steering follows the autonomy
vehicle file's angle and rate limits. Reverse targets become zero.

The plant brakes after commands stop arriving for `command_timeout_sec`
(default 0.5 simulated seconds). The planar model has no parking brake;
the adapter holds it at rest below 0.03 m/s when the target is zero.
Once held, it skips integration until a forward speed is requested. This
prevents numerical creep from the tire model at zero speed.
Actuator delays, hydraulic braking and motor response remain uncalibrated.

BobSim integrates at the center of mass. The adapter converts position and
body velocity to the rear-axle `base_link`, including the yaw-rate contribution
to lateral velocity. `/clock`, `/lhr/vehicle/odom` and TF retain the existing
interface, so the LiDAR follows the resulting motion. The plant uses a wall
timer with fixed 20 ms simulated steps; slow computation slows simulation
rather than changing the physics timestep. Solver failures stop the node.

BobSim reads `vehicle/vehicle.yml` for masses, tires and dynamics. Autonomy
reads its existing YAML for geometry, mounts and actuator limits. Startup
checks wheelbase and wheel radii agree. These files must stay consistent;
this change does not consolidate the vehicle schemas.

`/lhr/sim/provenance` is a latched JSON string containing the BobSim commit,
dynamics vehicle hash and autonomy vehicle hash. Bags record it; metrics
record the selected plant. The model remains planar: no roll, pitch,
suspension travel, terrain response or physical cone impacts. Real-car
validation and full-lap regression studies remain required.

Run `colcon test --packages-select lhr_sim_bobsim lhr_sim_kinematic lhr_demo`.
Functional BobSim tests need the mounted checkout and vehicle file.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

## Upstream compatibility

This adapter uses BobSim `22c5056` through its public `engines.dynpy` API.
It imports the upstream MF5.2 tires, KinPy geometry, aero and algebraic
load/torque closure unchanged. Solver failures end the run. Old benchmark
and lap results at `4da577a` are historical; they do not validate this pin.
The latest upstream model may reject commands outside its tire-force support.

Autonomy CI runs when the BobSim pin or canonical vehicle changes. It marks
the checked-out repository safe before initializing the dynamics submodule
inside the ROS container.

The MF5.2 model is slower than the old reduced model. Run time depends on
commands and hardware. Use a larger `--wall-timeout` for driving studies;
the fixed 20 ms simulation step stays unchanged. A short smoke run does not
establish full-lap performance or real-car accuracy.
