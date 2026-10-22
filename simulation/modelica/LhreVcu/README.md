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
make vcu-sil      # build //boards/VCU:libvcu_sil.so and copy it into Resources/Library/
make bobsim-fw    # build LhreVcu.VehicleSimFw with OMC into out/build/VehicleSimFw/
make sil          # run a 5 s straight-line case and check it
```

Set `BAZEL_ARGS=--config=local` when you have no BuildBuddy key.

After a firmware change, run `make vcu-sil sil`. The OMC executable loads
the library when it starts, so you do not run `make bobsim-fw` again. Run
`make bobsim-fw` again when `package.mo`, `vehicle/vehicle.yml`, the
BobSim pin or `tools/build_vehicle_sim_fw.mos` changes.

## What is in `package.mo`

| Class | What it does |
| ----- | ------------ |
| `VcuSil` | An `ExternalObject`. It holds one firmware instance from `vcu_sil_create` and frees it with `vcu_sil_destroy`. |
| `vcuSilStep` | An `impure` external C function that calls `vcu_sil_step`. |
| `FwVCU` | Extends `BobLib.Controllers.StandardVCU`. It sets the drive torque request and the regen limit request from the firmware. |
| `VehicleSimFw` | Extends `BobLib.Experiments.Standards.VehicleSim` and replaces its `vcu` with `FwVCU`. |

### `FwVCU`

`StandardVCU` has three parts. `FwVCU` replaces only the second:

1. The driver: a speed PI controller and a steer maneuver. It makes the
   accelerator and brake pedal commands. `FwVCU` keeps it.
2. The torque request from the pedals. `FwVCU` replaces it with the
   firmware output.
3. `VCUCore`: it applies the torque, power and regen limits and sends the
   command to the inverter. `FwVCU` keeps it.

At each `sample(0, Ts)` event, `FwVCU` sends these values to the firmware:

- the time;
- the accelerator and brake pedal commands;
- the motor speed, with `motorSpeedSign` applied;
- the HV bus voltage and current.

It then holds the motor torque and the regen limit from the firmware until
the next event. If `vcu_sil_step` returns an error code, an `assert` stops
the simulation. The codes are in the
[SIL README](../../../boards/VCU/Sil/README.md#errors).

The firmware is called only at sample events, because its clock cannot go
back. DASSL evaluates continuous equations at trial times and then goes
back.

`Ts` is 0.1 s. That is the period of the App's `VcuStatus`, so a smaller
`Ts` does not change the command today. Each sample event restarts DASSL
and costs time. In a 5 s run, `Ts = 0.01` s took 232 s and `Ts = 0.1` s
took 48 s. The final speeds were the same to 4e-8 m/s. Make `Ts` smaller
when the App sends its command faster.

### `VehicleSimFw`

A `redeclare` does not keep the modifiers of the component it replaces. So
`VehicleSimFw` writes the nine `vcu(...)` bindings of
`BaseVehicleSim.mo` again. If BobLib adds a binding to `vcu`, add it here
too. Do this check after each BobSim pin bump.

## `make sil`

[`tools/vcu_sil.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/simulation/tools/vcu_sil.py)
runs the executable in `out/build/VehicleSimFw/` for 5 s with these
overrides: `vcu.useMode=1` and `vcu.steerAmp=0`. The car then goes
straight from 15 m/s. Mode 1 is a sine steer, and an amplitude of 0 makes
it straight. The step mode (2) is not used, because its termination
monitor stops a run with no steer early.

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

It writes `out/vcu-sil/VehicleSimFw.csv`, `out/vcu-sil/run.log` and
`out/vcu-sil/report.pdf`. It fails when:

- the executable exits with an error, or `run.log` shows an override that
  the model did not use;
- the result does not have a variable in `VARIABLES`;
- the run stops before 5 s;
- the torque that reaches `VCUCore` is more than the firmware command. This
  means BobLib's own torque request still reaches the inverter;
- the firmware commands a torque that is not 0;
- the car speeds up.

The last two checks are true only while the App has no control law
(`App::SendStatus` sends zero torque). Change them when the control law
lands.

### `report.pdf`

The report shows what the sim sent to the firmware, what the firmware
did and what the car did. It has one Letter page for each section below.
`make sil` writes it also when a check fails. It does not write it when
the executable fails or a variable is missing.

The top of page 1 gives OK or FAIL and each failure. Below that are the
case, the lhre, BobSim and BobLib commits and the first 12 hex digits of
the SHA-256 of `libvcu_sil.so`. A commit gets `+dirty` when its checkout
has changes. Each section has a table and plots against time:

| Section | Shows |
| ------- | ----- |
| Sim to VCU | The firmware inputs: the accelerator and brake pedal commands, the motor speed (rpm) and the pack voltage and current. Also the steer command, which does not go to the firmware. |
| VCU to plant | The motor torque at three points: the firmware command (`vcu.tau_fw`), after the `VCUCore` limits (`vcu.tau_cmd_limited`) and at the motor (`motor.motor.tau_act`, a first-order lag). Also the firmware regen limit. |
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
- The firmware command updates at the App's send rate, which is 10 Hz
  today, not at `Ts`. See the
  [SIL README](../../../boards/VCU/Sil/README.md#gotchas).
