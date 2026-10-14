# lhr_sim_bobsim

Run BobSim's public `dyn_py` 3 DOF model (`plant:=bobsim`), or Orion's full
vehicle model compiled to an FMU (`plant:=fmu`, see
[below](#full-vehicle-fmu-plantfmu)), as the ROS vehicle plant. The 3 DOF
model imports the pinned submodule; no BobSim source is copied here. The
existing kinematic model remains the default.

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

## Full-vehicle FMU (`plant:=fmu`)

BobLib's full vehicle model: MultiBody suspension from the hardpoints, MF5.2
tires and the EV powertrain, with the car from `vehicle/vehicle.yml`. Build it
on the host, then select it:

```bash
make -C simulation fmu        # about 6 minutes, see simulation/tools/fmu
ros2 launch lhr_demo mvs_demo.launch.py plant:=fmu
```

`fmu_node` subclasses the kinematic node, like `sim_node`, so `/clock`,
odometry, TF and the command timeout behave the same. It publishes
`/lhr/sim/provenance` with the FMU's hash. `FmuPlant` (`fmu_plant.py`) finds
the FMU at `$ORION_FMU`, in the checkout, or at `/opt/lhr` in the container,
and needs `fmpy` (in the image).

The FMU's inputs are driver controls, so `FmuPlant` adds the driver:

- the road-wheel steering command times 4.1 is the steering-wheel angle,
  held inside 0.6 rad: past about 0.7 rad the FMU's steering linkage has no
  solution;
- a PI loop on speed works the accelerator and brake pedals. A pedal is
  pressed at most 2 per second and released at once. At a standstill it
  holds the brake at 0.3, since BobLib's brakes have no static friction;
- the battery starts at 90 %: BobLib switches regen off at the voltage of
  the full pack, and its battery equations have no solution at that switch;
- BobLib's motor pushes the way its shaft last turned, and the driveline
  rings backwards when the rear wheels stop. While the motor is reversed the
  driver stays off the accelerator. If the car is stopped and told to go,
  `FmuPlant` resets the FMU at the same pose; it starts forward;
- if an FMU step fails, it starts a new FMU where the car is and logs a
  warning. A command that is not a number is a stop.

The node also publishes the car and the force on each tire as markers on
`/lhr/vehicle/forces`, in `base_link` (red: force in the road plane, green:
vertical load, 1 m per kN; the label gives the load and the friction used).

In the headless runner (`./scripts/run_headless.sh plant:=fmu`, autocross
seed 1, Docker on an Apple Silicon laptop) three runs each completed a lap
with no failed FMU step: mean cross-track error 0.59 to 0.61 m against 0.50
to 0.54 m for the kinematic plant. The FMU car runs wide in the tightest
corner at the planner's speed, 2.2 to 3.4 m off the path, where the
kinematic car stays within 1.6 m: real tires have a grip limit and the
bicycle model has none. The FMU is not validated against Orion data.
