# FMU tests

Run `colcon test --packages-select lhr_sim_fmu --return-code-on-test-failure`
from the built ROS workspace. Contract cases cover rejected/nonfinite native
steps, resource release, map transforms, bounded driver pedals, provenance
mismatches and incompatible FMI versions/types/units. A ROS failure case
checks that rejected physics cannot publish time or odometry.

`test_native_fmu.py` skips unless `LHR_TEST_FMU` names the built artifact.
When set, it executes the actual model through wait, launch, both turn
directions, braking and relaunch. It checks the axle midpoint and compiled
canonical aero references, which differ from the upstream default car.
There is no fallback or substitute native model. See the
[package guide](../README.md) for the artifact build and known upstream limits.
