# Combined FMU integration review, 2026-11-16

The combined changes from PRs #221 and #222 build together on main
`1a99e037`. The affected ROS suites report **555 tests, zero failures or
errors, three existing native-artifact skips**. This is integration and
launch validation, not a production FMU driving or Sim v2 acceptance result.

## Changes checked

- The coordinator waits for FMU provenance, services and Gazebo's first
  clock before stepping. Startup has a 60-second total deadline, independent
  of the 5-second service-response and 30-second FMU-step deadlines. Injected
  time tests cover delayed readiness, missing readiness and stalled steps;
  invalid/nonfinite deadlines fail at construction.
- Launch evaluation and real ROS node parameter readback cover defaults and
  overrides. Fault-routing tests cover steering freeze and invalid/spiking
  odometry under truth/EKF and Gazebo/Mid-360 clouds with stereo fusion.
- In FMU mode, IMU dropout intercepts the interim `inertial_sim` publisher,
  rather than the unused Gazebo IMU bridge. Its regression fails before the fix.
- Pose-age diagnostics subscribe to the rendered cloud. With Mid-360, the
  observer's `--lidar-source mid360` requires `/lhr/lidar/gazebo_points`;
  a synthetic caster stream alone cannot pass timing acceptance.
- Spawn, fractional physics-step and conflicting clock configurations have
  rejection tests. Generated-model documentation now lists all four variants.

## Method and limits

Built all 16 ROS packages in the local Ubuntu 24.04/Jazzy development image,
using an isolated container with a read-only source mount and separate build,
install and test directories. Ran:

```bash
colcon build --symlink-install
colcon test --packages-select lhr_gazebo lhr_sim_fmu lhr_demo
colcon test-result --verbose
```

The three skips are the existing native FMU drive/relaunch, compiled aero
reference and brake-bias checks, which require `LHR_TEST_FMU`. No production
FMU was supplied; these results do not establish solver reliability, braking,
native IMU/wheel fidelity or default-timeout full-stack driving.

A separate bounded Gazebo Harmonic probe started `autocross_seed1.sdf`
paused, with the ROS clock bridge, in an isolated transport partition.
The ROS observer received clock `(0, 0)` without unpausing. This checks that
waiting for the initial clock can bootstrap on a real world; it does not
exercise a native FMU or measure pose age while driving.

The new startup, timeout and rendered-cloud tests were run against the
pre-fix implementation and failed. The affected suites also run their ROS
flake8 and pep257 checks. The repository-configured Ruff check and strict
MkDocs build pass. The production dashboard and worker were not changed.

## Remaining acceptance

Pin and rebuild the production FMU after its upstream solver fix; validate
pull-away, braking/hold and relaunch. Native IMU/wheel outputs remain #205.
Then run the FMU/Mid-360/stereo/EKF/safety stack at the default LiDAR timeout,
retain timing reports and execute the gate and fault matrices. The default
plant remains Gazebo until that evidence is available.
