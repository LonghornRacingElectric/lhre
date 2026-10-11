# Perception tests

Run `colcon test --packages-select lhr_perception` from the ROS workspace,
then `colcon test-result --verbose`. Requires ROS 2 Jazzy and package dependencies.

Checks cover full mount rotation, ground and body rejection, delayed clouds
with an older vehicle and mount pose, missing transforms, and invalid stamps.
The [demo package](../../lhr_demo/README.md) also tests vendor beams through
this detector, boundary planning, and control. These are deterministic tests;
they do not need a display or Gazebo.

`test_stacking.py` checks sparse independent scans under vehicle motion,
duplicate timestamps, empty-scan expiration, replay resets and bounded
history. These tests establish registration behavior, not hardware recall.

`test_recording_evaluator.py` checks complete-frame boundaries, empty
intervals and stationary gravity alignment. Sparse graph clustering also
has a transitive-neighbor check, independent of the seed-radius algorithm.
