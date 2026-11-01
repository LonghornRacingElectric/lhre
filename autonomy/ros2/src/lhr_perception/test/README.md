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
history. A transitive-neighbor check confirms clustering joins a chain of
points, not only those within the seed point's radius. These tests
establish registration behavior, not hardware recall.

`test_recording_evaluator.py` checks complete-frame boundaries, empty
intervals, out-of-order timestamps and stationary gravity alignment.

`test_pose_history.py` covers scan-time pose interpolation through angle
wrap, simulation clock resets and sharp-turn reprojection.
`test_cone_map.py` covers nearest-cone, one-to-one map association.
`test_cone_side_classifier.py` covers side voting, boundary gap bridging,
rejected cross-track links and label locking. `test_unclassified_mode.py`
checks that `classify_sides:=false` still deduplicates and publishes every
cone. `test_cone_color.py` covers the camera color regions, thresholds and
image decoding; `test_camera_color_classifier.py` checks that the node keeps
the image header and drops malformed frames.

Camera fusion adds ROS-independent projection, region-association and vote tests
in `test_camera_lidar_geometry.py`. `test_camera_lidar_fusion.py` uses actual
ROS camera/map messages and TF to check timing, resets, marker lifecycle, and
late-subscriber behavior. Its ROS checks skip when `rclpy` is unavailable;
a skipped test does not verify integration. Gazebo's
`test_camera_lidar_fusion_launch.py` evaluates defaults/overrides and starts
fusion with those parameters in truth and EKF modes.

Fallback checks distinguish persistent physical-color history from current
camera eligibility. Pure tests cover priority, timeout boundaries, latest-frame
visibility/conflicts and ID generations; ROS tests check the separate classified
output, deletion markers and timer expiration with no new map.

Delayed-image regression checks preserve the map, votes, confirmed-orange
exclusion, and dynamic TF for both small and large image timestamp regressions,
and verify that subsequent fresh frames still process. Map/clock regression
checks separately verify that actual epoch changes discard previous evidence
and dynamic poses while retaining the static mount.
