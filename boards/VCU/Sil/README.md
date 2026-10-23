# VCU software-in-the-loop

The unmodified `vcu::App` behind a small C ABI, built as a host shared
library. It is the same `:vcu_app` library that the firmware and
`:vcu_app_test` link.
[simulation/modelica/LhreVcu](../../../simulation/modelica/LhreVcu/README.md)
loads it into the BobSim full-vehicle model as that model's VCU torque
command. A change to `App/` therefore shows up in a vehicle simulation
without touching BobSim or rebuilding the model. Why it is a shared library
and not a copy, an FMU or a separate process:
[ADR-014](../../../docs/architecture/014-simulation-consumes-bazel-built-vcu.md).

## Targets

| Target           | What                                                          |
| ---------------- | ------------------------------------------------------------- |
| `:libvcu_sil.so` | The library. Exports only `vcu_sil_*`. libc++ and FreeRTOS are linked in statically, so it needs only glibc ≥ 2.17. |
| `:vcu_sil_test`  | ABI tests, linked against the `.so` itself so the exported symbol set is under test too. |
| `:vcu_sil_api`   | The header, [`vcu_sil.h`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/vcu_sil.h). |

```bash
bazel build //boards/VCU:libvcu_sil.so   # → bazel-bin/boards/VCU/libvcu_sil.so
bazel test //boards/VCU:vcu_sil_test
make -C simulation vcu-sil               # build, then install into the Modelica package
```

All three are Linux-only (`target_compatible_with`), so `bazel test //...`
skips them on other hosts. On Windows, use WSL.

## How a step works

`vcu_sil_step` drives the App the way
[`App/vcu_app_test.cpp`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/App/vcu_app_test.cpp)
does. The pack state goes in through the car's own CAN interface. The
accelerator and motor speed have no LHAL or CAN path into the App yet, so
they go in through `App::SetDriveInputs`, a seam that only the SIL calls.
For those two inputs this departs from ADR-014's "same LHAL and CAN
boundary as on the car" until the car has them.

1. Advance an `lhal::host::TestClock` to `round(time_s × 1000)` ms. This is
   virtual time with no scheduler, so runs are deterministic and as fast as
   the simulator calls it.
2. Put the pack state on a fake bus (`lhal::host::CanNetwork`) as the
   `HvcPackStatus` frames that a stand-in HVC sends at its spec rate.
3. Give the accelerator and motor speed to `App::SetDriveInputs`.
4. Call `App::Step()` once. It runs the control step (driver demand, then
   traction control) if at least `App::kControlPeriodMs` has passed.
5. Report `App::torque_command_nm()`, the command of the latest control
   step.

## What the ABI carries today

| Signal                                 | Dir | Mapping                                         |
| -------------------------------------- | --- | ----------------------------------------------- |
| `pack_voltage_v`, `pack_current_a`     | in  | `HvcPackStatus`, saturated to what the frame can encode |
| `accel_pedal`, `motor_speed_rad_s`     | in  | `App::SetDriveInputs`. The App clamps the accelerator to 0..1 |
| `brake_pedal`                          | in  | Checked, then unused: the App has no brake input yet |
| `motor_torque_nm`                      | out | `App::torque_command_nm()`                      |
| `regen_limit_nm`                       | out | Always 0: there is no regen signal yet          |

The App's control law is a linear pedal map (full accelerator asks for
`App::kMaxDriveTorqueNm`, 230 N·m) and then
[traction control](../README.md#traction-control), which only removes
torque. `vcu_sil_create(0)` turns traction control off for a baseline run.
`CommandsDriverDemandFromTheAccelerator` and the two `TractionControl…`
tests in `:vcu_sil_test` pin the mapping. On the car nothing calls
`SetDriveInputs` yet, so the car commands 0 N·m. When the pedals get an
LHAL ADC interface and `lib/spec` gets inverter feedback, those inputs move
to LHAL fakes and CAN frames here and the seam goes away. The ABI and the
Modelica wrapper need not change.

## Errors

`vcu_sil_step` returns `VCU_SIL_OK` (0) or a negative code from `vcu_sil.h`,
and writes its outputs only on success. The Modelica wrapper asserts on any
non-zero code, so a bad call stops the simulation instead of feeding it a
stale command.

| Code                              | Cause                                         |
| --------------------------------- | --------------------------------------------- |
| `VCU_SIL_ERR_NULL_ARGUMENT`       | Null handle or output pointer                 |
| `VCU_SIL_ERR_TIME_WENT_BACKWARDS` | `time_s` earlier than the last call, or negative |
| `VCU_SIL_ERR_NON_FINITE_INPUT`    | Any input is NaN or ±inf                      |

## Gotchas

- **Call it only at sample events.** A variable-step solver evaluates
  continuous equations at trial times and then rolls back. The App's clock
  cannot go back, which is why the wrapper calls it from
  `when sample(0, Ts)`.
- **The command updates once per control step.** `Step()` runs the control
  step over the measured time since the last one, at most every 3 ms. With
  the wrapper's `Ts` of 10 ms that is once per call: 100 Hz, not the car's
  333 Hz. Traction control differentiates the motor speed over that step,
  so a smaller `Ts` changes what it sees.
- **Time is quantized to 1 ms**, because the App only sees `Millis()`.
- **`Step()` is not the RTOS task path.** On the car the App runs as
  FreeRTOS tasks. Here, as in `:vcu_app_test`, it runs the scheduler-less
  `Step()`, which drains CAN and runs the control step on a different
  cadence. Logic that lives only in a task body is invisible to the
  simulation.
- **One thread.** Handles are independent of each other (tested), but none
  is thread-safe. OMC simulations are single-threaded.
- **Changing the ABI** means changing `simulation/modelica/LhreVcu/package.mo`
  in the same PR. OMC writes its own prototype from the Modelica
  declaration, so a mismatch compiles and then misbehaves.
