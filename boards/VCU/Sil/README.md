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
does: through the car's own LHAL and CAN interfaces, so the App cannot tell
it is in a simulation.

1. Advance an `lhal::host::TestClock` to `round(time_s × 1000)` ms. This is
   virtual time with no scheduler, so runs are deterministic and as fast as
   the simulator calls it.
2. Put the plant state on a fake bus (`lhal::host::CanNetwork`) as the
   frames the other boards would send. Today that is `HvcPackStatus` from a
   stand-in HVC, at its spec rate.
3. Call `App::Step()` once.
4. Decode what the App sent with the generated `lib/spec` bindings. The last
   value is held until the App sends the next frame.

## What the ABI carries today

| Signal                                 | Dir | Mapping                                         |
| -------------------------------------- | --- | ----------------------------------------------- |
| `pack_voltage_v`, `pack_current_a`     | in  | `HvcPackStatus`, saturated to what the frame can encode |
| `accel_pedal`, `brake_pedal`, `motor_speed_rad_s` | in | Checked, then unused: the App has no pedal or motor-speed input yet |
| `motor_torque_nm`                      | out | `VcuStatus.torque_request`, a stand-in until `lib/spec` has an inverter command |
| `regen_limit_nm`                       | out | Always 0: there is no regen signal yet          |

`App::SendStatus` hard-codes zero torque, so today the simulated car does
not move. `CommandsZeroTorqueWhileAppHasNoControlLaw` pins that. When the
control law lands (pedals through an LHAL ADC interface, an inverter command
in `lib/spec`), only the mapping in `vcu_sil.cpp` and that test change. The
ABI and the Modelica wrapper stay as they are.

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
| `VCU_SIL_ERR_NO_STATUS_FROM_APP`  | The App has not sent a `VcuStatus` yet, so there is no command to report |

## Gotchas

- **Call it only at sample events.** A variable-step solver evaluates
  continuous equations at trial times and then rolls back. The App's clock
  cannot go back, which is why the wrapper calls it from
  `when sample(0, Ts)`.
- **The command updates at the App's send rate.** `VcuStatus` goes out every
  100 ms, so the torque is a 10 Hz staircase, however small the simulator's
  `Ts` is.
- **Time is quantized to 1 ms**, because the App only sees `Millis()`.
- **`Step()` is not the RTOS task path.** On the car the App runs as
  FreeRTOS tasks. Here, as in `:vcu_app_test`, it runs the scheduler-less
  `Step()`, which drains CAN on a different cadence. Logic that lives only
  in a task body is invisible to the simulation.
- **One thread.** Handles are independent of each other (tested), but none
  is thread-safe. OMC simulations are single-threaded.
- **Changing the ABI** means changing `simulation/modelica/LhreVcu/package.mo`
  in the same PR. OMC writes its own prototype from the Modelica
  declaration, so a mismatch compiles and then misbehaves.
