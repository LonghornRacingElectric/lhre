# ADR-009: Autonomy (ROS 2) builds with colcon, outside Bazel

- **Status:** Accepted
- **Date:** 2026-08

## Context

The driverless stack in
[`autonomy/`](https://github.com/LonghornRacingElectric/lhre/tree/main/autonomy)
is a ROS 2 Jazzy workspace: Python nodes on rclpy, Gazebo Harmonic for
simulation, built with colcon on Ubuntu 24.04 (a Docker image covers
macOS). It moved here from `lhre-2026` in August 2026 so the current car
has one repo ([ADR-001](001-monorepo.md)). Everything else in this repo
builds with Bazel ([ADR-002](002-bazel.md)), and presubmit runs
`bazel test //...` on Linux and Windows.

## Decision

`autonomy/` is not a Bazel package tree. It is listed in `.bazelignore`,
contains no `BUILD.bazel` files, and builds with colcon per its own
[getting-started guide](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/GETTING-STARTED.md).
Its CI is a separate, path-filtered workflow (follow-up). Its docs stay
next to the code and join the published site in a follow-up. Agents get
their own instructions in
[`autonomy/AGENTS.md`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/AGENTS.md).

## Alternatives considered

- **`rules_ros2` (build ROS 2 from source under Bazel).** Linux-only, an
  hours-long first build, no Gazebo, and every target would need platform
  gating to survive the Windows presubmit job. Nobody on the team maintains
  Bazel-plus-ROS, and the ROS ecosystem (apt packages, launch files, rviz,
  Gazebo plugins) assumes colcon.
- **Bazel `py_test` for algorithm code only.** Every package module imports
  rclpy today; there is nothing to test without a ROS environment. Worth
  revisiting if a lane produces rclpy-free libraries (an EKF, a planner).
- **Leave autonomy in `lhre-2026`.** ADR-001 says one repo for the current
  car; the split repo was already drifting from the CAN layer the drive-by-wire
  bridge will consume.

## Consequences

- `bazel test //...` neither builds nor tests autonomy. Its checks come from
  its own workflow. Bazel earns its keep on things CI must reproduce
  hermetically; the in-flight telemetry migration (ADR-005) draws the same
  line for the BEVO frontend and deploy scripts.
- The supported autonomy dev environment is Ubuntu 24.04 native (Docker on
  macOS), not a hermetic toolchain. That is a documented install, not
  `git clone && bazel test`.
- If autonomy ever ships C++ ROS nodes, the repo `.clang-format` and
  `//tools/format:check` apply to them automatically (the formatter
  enumerates every tracked C/C++ file).
- Root `AGENTS.md` build instructions do not apply inside `autonomy/`;
  `autonomy/AGENTS.md` does.
