# LhreVcu

A Modelica package that runs the VCU firmware as the VCU of the BobLib
full-vehicle model (`VehicleSim`). The firmware is the Bazel-built
[`libvcu_sil.so`](../../../boards/VCU/Sil/README.md). BobSim does not
change. The reason for this design is in
[ADR-014](../../../docs/architecture/014-simulation-consumes-bazel-built-vcu.md).

## Run it

Run these from `simulation/`. `make vcu-sil` runs Bazel on the host. The
other two targets run in the container.

```bash
make vcu-sil              # build //boards/VCU:libvcu_sil.so and copy it into Resources/Library/
make bobsim-fw            # build LhreVcu.VehicleSimFw with OMC into out/build/VehicleSimFw/
make sil                  # run the coast track and check it
make sil TRACK=launch     # run another track
make sil TRACK=launch TC=off  # the same track with traction control off
```

Set `BAZEL_ARGS=--config=local` when you have no BuildBuddy key.

After a firmware change, run `make vcu-sil sil`. The OMC executable loads
the library when it starts, so you do not run `make bobsim-fw` again. Run
`make bobsim-fw` again when `package.mo`, `vehicle/vehicle.yml`, the
BobSim pin or `tools/build_vehicle_sim_fw.mos` changes. A new or changed
track does not need `make bobsim-fw`, because `make sil` writes the trace
file at run time.

## What is in `package.mo`

| Class | What it does |
| ----- | ------------ |
| `VcuSil` | An `ExternalObject`. It holds one firmware instance from `vcu_sil_create(tractionControl)` and frees it with `vcu_sil_destroy`. |
| `vcuSilStep` | An `impure` external C function that calls `vcu_sil_step`. |
| `FwVCU` | Extends `BobLib.Controllers.StandardVCU`. It reads the pedals from a driver trace file. It sets the drive torque request and the regen limit request from the firmware. |
| `VehicleSimFw` | Extends `BobLib.Experiments.Standards.VehicleSim` and replaces its `vcu` with `FwVCU`. |

### `FwVCU`

`StandardVCU` has three parts:

1. The driver: a steer maneuver, and a speed PI controller that makes the
   pedal commands. `FwVCU` keeps the steer maneuver. It replaces the pedal
   commands with a driver trace. The speed PI still runs, but its output
   goes only to `VCUCore` inputs that `VCUCore` does not use.
2. The torque request from the pedals. `FwVCU` replaces it with the
   firmware output.
3. `VCUCore`: it applies the torque, power and regen limits and sends the
   command to the inverter. `FwVCU` keeps it.

The driver trace is a `CombiTimeTable` that reads the file
`driverTraceFile` (default `driver.csv`). The file has a header row and
three columns: the time (s), the accelerator and the brake (0 to 1). The
table interpolates linearly and holds the last row after the last time.
The accelerator and the brake go to the firmware. The brake also goes to
the mechanical brakes as brake × `mechanicalBrakeTorqueLimit`, because
`vcu_sil_step` has no brake output.

At each `sample(0, Ts)` event, `FwVCU` sends these values to the firmware:

- the time;
- the accelerator and the brake from the driver trace. `accel_fw` holds
  the accelerator that the firmware got;
- the motor speed, with `motorSpeedSign` applied;
- the HV bus voltage and current.

It then holds the motor torque and the regen limit from the firmware until
the next event. If `vcu_sil_step` returns an error code, an `assert` stops
the simulation. The codes are in the
[SIL README](../../../boards/VCU/Sil/README.md#errors).

The firmware is called only at sample events, because its clock cannot go
back. DASSL evaluates continuous equations at trial times and then goes
back.

`Ts` is 0.01 s. The App runs its control step on each call, over the
measured time since the last call. Traction control differentiates the
motor speed over that step, so `Ts` sets what it sees. The car runs the
control step every 3 ms. Each sample event restarts DASSL and costs time.
With zero torque on `coast` (5 s), `Ts = 0.01` s took 232 s and
`Ts = 0.1` s took 48 s. A `Ts` of 0.003 s would match the car but cost
more again.

`tractionControl` (default `true`) goes to `vcu_sil_create`. `false` runs
the App with traction control off, for a baseline.

### `VehicleSimFw`

A `redeclare` does not keep the modifiers of the component it replaces. So
`VehicleSimFw` writes the nine `vcu(...)` bindings of
`BaseVehicleSim.mo` again. If BobLib adds a binding to `vcu`, add it here
too. Do this check after each BobSim pin bump.

## `make sil`

`make sil TRACK=<name>` runs one track. The default is `coast`.
`TC=off` runs it with traction control off. The tracks are in
[`tools/vcu_sil_tracks.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/simulation/tools/vcu_sil_tracks.py).
An unknown name fails and lists the names.

| Track | Start | Stop | Steer | Accelerator |
| ----- | ----- | ---- | ----- | ----------- |
| `coast` | 15 m/s | 5 s | Straight | 0 |
| `launch` | 0 m/s | 3 s | Straight | 0 to 100 % from 0.5 to 0.6 s |
| `corner-exit` | 10 m/s | 5 s | Sine, 20° handwheel, 0.1 Hz from 0.5 s, peak at 3 s | 0 to 100 % from 3.0 to 3.1 s |

No track uses the brake yet. Results with BobSim `22c5056`, BobLib
`1bd6d78`, vehicle `orion-baseline-1` and `Ts = 0.01` s:

| Track | TC | Speed at stop | Peak rear κ | Cut rows |
| ----- | -- | ------------- | ----------- | -------- |
| `coast` | on | 12.227 m/s | 0.000 | — |
| `launch` | on | 25.84 m/s | 0.046 | 202 of 407 |
| `launch` | off | 27.82 m/s | 0.055 | 0 of 407 |
| `corner-exit` | on | 27.70 m/s | 0.065 | 113 of 193 |
| `corner-exit` | off | 28.95 m/s | 0.073 | 0 of 193 |

Cut rows are the result rows at full accelerator where the firmware
commands less than 230 Nm.

- `coast` commands 0 Nm. Its speed is the same as before traction
  control.
- With TC off, the peak rear Fx/Fz is 2.0 on `launch` and 2.3 on
  `corner-exit`. Fx/Fz still increases with κ, so the rear tires may
  still be below their peak slip.
- TC cuts torque on these runs anyway. The #298 threshold is 30 rad/s²
  at the motor shaft. With no slip, this is 30 × 0.2045 m / 3.31 =
  1.85 m/s² of vehicle acceleration. At full accelerator, the shaft
  accelerates at 150 to 185 rad/s² on average.
- The trim gets to its 50 Nm limit in one control step. It resets when
  one step measures less than 30 rad/s². The motor speed oscillates, so
  the command switches between 180 and 230 Nm. Page 2 of the report
  shows this.
- TC lowers the speed at the stop time by 1.98 m/s on `launch` and by
  1.25 m/s on `corner-exit`.

Retune traction control before you use these results for the car.

### Add a track

Add an entry to `TRACKS`. A track has:

- a start speed, which sets `initialVel`;
- a stop time;
- overrides for the `StandardVCU` steer maneuver. Use mode 1, the sine
  steer. `vcu.steerAmp=0` makes it straight. Modes 0 and 2 have
  termination monitors that can stop a run early. Mode 3 assumes that the
  speed PI holds the speed, and in `FwVCU` the PI does not drive the car;
- pedal points: the time (s), the accelerator and the brake. The times
  start at 0 s and increase. The pedals are from 0 to 1. For a step, put
  two points 0.1 s apart.

`make sil` checks the pedal points before it runs. `make unit` checks
every track.

### What it runs

[`tools/vcu_sil.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/simulation/tools/vcu_sil.py)
writes the pedal points to `out/vcu-sil/<track>/driver.csv`. It then runs
the executable in `out/build/VehicleSimFw/` with these overrides:
`initialVel`, the steer overrides of the track, `vcu.driverTraceFile` and
`vcu.tractionControl`. With `TC=off`, the folder is
`out/vcu-sil/<track>--tc-off/`, so a baseline does not replace the run
with traction control.

The stop time is the `-stopTime` flag, not an override. OMC 1.27 does not
find `stopTime` in `-override`, so it runs to the build's stop time (10 s).

The run also gets `-emit_protected` and `-variableFilter`. The filter
lists `VARIABLES` in
[`tools/vcu_sil_report.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/simulation/tools/vcu_sil_report.py).
BobLib marks the tire, motor and `vcu.sens_*` variables as protected, so
OMC does not write them without `-emit_protected`. The filter in
`tools/build_vehicle_sim_fw.mos` is only the default for a run without
these flags. When you change `VARIABLES`, you do not run `make bobsim-fw`
again.

It writes `driver.csv`, `VehicleSimFw.csv`, `run.log` and `report.pdf`
into that folder. It fails when:

- the pedal points of the track break a rule in [Add a track](#add-a-track);
- the executable exits with an error, or `run.log` shows an override that
  the model did not use;
- the result does not have a variable in `VARIABLES`;
- the run stops before the stop time of the track;
- the torque that reaches `VCUCore` is more than the firmware command. This
  means BobLib's own torque request still reaches the inverter;
- the firmware command is less than 0 or more than the driver demand,
  `vcu.accel_fw` × 230 Nm. Traction control must only remove torque
  (TC-01 in the ELC traction control requirements).

`MAX_DRIVE_TORQUE_NM` in `tools/vcu_sil_report.py` is the 230 Nm. Keep it
equal to `App::kMaxDriveTorqueNm` in `boards/VCU/App/vcu_app.hpp`.

### `report.pdf`

The report shows what the sim sent to the firmware, what the firmware
did and what the car did. It has one Letter page for each section below.
`make sil` writes it also when a check fails. It does not write it when
the executable fails or a variable is missing.

The top of page 1 gives OK or FAIL and each failure. Below that are the
track and its description, traction control on or off, the lhre, BobSim and BobLib commits and the first 12 hex digits of
the SHA-256 of `libvcu_sil.so`. A commit gets `+dirty` when its checkout
has changes. Each section has a table and plots against time:

| Section | Shows |
| ------- | ----- |
| Sim to VCU | The firmware inputs: the accelerator and the brake from the driver trace, the motor speed (rpm) and the pack voltage and current. Also the steer command, which does not go to the firmware. |
| VCU to plant | The motor torque at four points: the driver demand (`vcu.accel_fw` × 230 Nm), the firmware command after traction control (`vcu.tau_fw`), after the `VCUCore` limits (`vcu.tau_cmd_limited`) and at the motor (`motor.motor.tau_act`, a first-order lag). Also the firmware regen limit. |
| Tire slip | For each tire: κ, R0·ω − Vx, Fx and Fz. The table gives these values at the time of the largest \|κ\| of each tire. |
| Vehicle | The speed, the longitudinal acceleration and the yaw rate. |

Gotchas:

- The commits come from the checkout at run time. They do not prove that
  `out/build/VehicleSimFw/` was built from that checkout.
- A plot of a signal that changes by less than 1 % of its magnitude shows
  a flat line. The table gives the exact start, end, minimum and maximum.
- The report reads BobLib variable names, for example
  `chassis.detailedChassis.rrAxleDW.leftTire.kappa`. If a BobSim pin bump
  renames one, `make sil` names it. Then change `TIRES` or `VARIABLES`.

## Limits

- The library is a Linux `.so`. On Windows, use WSL.
- OMC does not put the library directory in the executable's run-time
  search path. `make sil` sets `LD_LIBRARY_PATH`.
- `make sil` runs the executable directly. It does not use BobSim's
  `ModelicaRunner`, because that name is private
  ([bobsim-boundary](../../docs/skills/bobsim-boundary/SKILL.md), rule 3).
- `make bobsim-fw` runs `make records` first and puts the BobLib files
  back after, also when the build fails.
- The executable links code that OMC generates from BobLib (GPL-3.0). Do
  not distribute it or the `out/build/` folder. Distribute the source.
- The firmware command updates once per `Ts` (100 Hz), not at the car's
  333 Hz. See the
  [SIL README](../../../boards/VCU/Sil/README.md#gotchas).
