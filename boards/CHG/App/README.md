# CHG application

`chg::App` follows the VCU's injected-peripheral pattern. Supply a `lhal::Clock`,
a dedicated `lhal::Uart` and controller callbacks; call `Step()` frequently from
one task. It consumes at most 128 bytes per pass, polls reads without waiting,
and bounds each frame write to 10 ms. Callback context is forwarded unchanged
to the controller implementation. The App must outlive its callbacks and must
not be copied.

A target UART should have an interrupt/DMA receive queue large enough for the
worst communications-task delay. This adapter's byte polling demonstrates the
interface; it is not a substitute for sizing the STM32 UART buffers. Do not
call `Step()` or controller callbacks from interrupt context.

The colocated `chg_app_test.cpp` drives the real display protocol through LHAL
host UART fakes. It covers accepted actions, missing action hooks, fresh
controller rejection, link timeout/no restart and transmit-failure latching.
The tests use ordinary `main()` plus active assertions and run under Bazel or
the shared library's native test runner.

See the [board README](../README.md) for targets and hardware completion.
