# VCU SIL scenarios

Scripted test drives, one per `.yml` file, run with `run.sh --fmu PATH --scenario <name>` and
checked with `run.sh --check`. Each says under `expect:` what the firmware must do, and every
run is held to that and to the invariants. The format, the checks and how to write one are in
the [SIL README](../README.md#scenarios), and the checks after a run under
[Checks after the run](../README.md#checks-after-the-run).
