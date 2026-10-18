# ZED 2i Stereo Simulation

Status: design approved by Celina on 2026-10-11. Implementation is on branch `celina/zed2i-stereo`; runtime evidence is recorded
in the [Gazebo README](../../ros2/src/lhr_gazebo/README.md#stereo-validation).
See the [implementation plan](zed2i-stereo-implementation.md).

## Outcome

The real car will have **one Stereolabs ZED 2i with the 2.1 mm wide lens**,
confirmed by Celina on 2026-10-11. The camera contains two imaging sensors;
the simulator models one unit with a left and right view separated by 0.12 m.

The first milestone delivers stereo images and calibration visible in
ROS 2 Jazzy / RViz from the existing Gazebo Harmonic vehicle. Success means
both views show the rendered track, correspond to the published calibration
and TF, and can be paired by simulation timestamp. This provides the input
interface for later [camera fusion](camera-fusion.md).

## Approach

Use two native Gazebo `camera` sensors on the same parent link. Reuse the
existing Ogre2 Sensors system and `ros_gz_bridge`; the vehicle model remains
generated from the SDF template and vehicle configuration.

This approach fits the existing simulation and its ROS sensor consumers.
An SDK-backed simulator would exercise more Stereolabs processing but expand
the environment and dependency scope. A single-camera model would support
color experiments but would omit the stereo interface selected for this
milestone.

## Geometry and optical assumptions

| Setting | Proposed value | Evidence or status |
| --- | --- | --- |
| Camera units | 1 | User decision |
| Lens | 2.1 mm wide | User decision |
| Stereo baseline | 0.12 m | Manufacturer specification |
| Image size per view | 1280 x 720 | Proposed simulation default; supported hardware mode |
| Update rate | 30 Hz in simulation time | Proposed simulation default; supported hardware mode |
| Horizontal FOV | 110 degrees | Initial idealized wide-lens approximation |
| Mount center in `base_link` | `[1.8, 0.0, 0.65]` m | ASSUMED front mount for simulation |
| Mount roll/pitch/yaw | `[0.0, 0.0, 0.0]` rad | ASSUMED forward-facing mount |

Mount position and orientation require measurement on the real car. The
proposed location places the lens above the simulated body; runtime visual
inspection must confirm the body does not occlude the image.

Use an ideal pinhole stereo pair with identical intrinsics, parallel optical
axes, zero distortion, and identity rectification. Derive focal length in
pixels from image width and horizontal FOV, with square pixels and a centered
principal point. Publish the calibration used to render the images.

The physical 2.1 mm focal length identifies the chosen lens; it is not a
pixel focal length. The brochure's horizontal/vertical FOV values are not
device calibration. A square-pixel 16:9 pinhole with 110-degree horizontal
FOV has about 77.6-degree vertical FOV, so this first model deliberately
approximates the wide lens rather than claiming its advertised 70-degree
vertical FOV. Replace the approximation with measured rectified intrinsics
when the physical unit's calibration is available.

Store the camera configuration in `lhr_vehicle/config/vehicle.yaml`, alongside
the existing sensor mounts. Extend `load_vehicle()` to expose the stereo
geometry and validate finite positive baseline, dimensions/rate, valid FOV,
and finite mount pose. The generator and ROS transforms must consume that
same configuration. Configuration changes require regeneration and rebuild;
the existing model freshness test enforces this.

## ROS interface

These are proposed application topics; physical-driver integration will map
the real ZED streams to the same consumer interface.

| ROS topic | Type | Meaning |
| --- | --- | --- |
| `/lhr/sensor/zed2i/left/image_rect` | `sensor_msgs/msg/Image` | Ideal rectified left RGB view |
| `/lhr/sensor/zed2i/right/image_rect` | `sensor_msgs/msg/Image` | Ideal rectified right RGB view |
| `/lhr/sensor/zed2i/left/camera_info` | `sensor_msgs/msg/CameraInfo` | Left calibration |
| `/lhr/sensor/zed2i/right/camera_info` | `sensor_msgs/msg/CameraInfo` | Right stereo calibration |

Name Gazebo topics explicitly and include `left` / `right` so Harmonic's
stereo camera handling can identify the pair independently of sensor creation
order. Both camera sensors share a parent link and equal update rates. Bridge
each stream in the Gazebo-to-ROS direction with compatible publisher and
subscriber QoS. Keep source simulation timestamps; do not restamp on arrival.

`CameraInfo` dimensions and intrinsics must match the rendered view. Left
projection translation is zero; right `P[3]` is `-fx * 0.12`, so
`-P_right[3] / P_right[0]` recovers the physical baseline. Harmonic's Sensors
system supports this for sibling cameras, but the running installation must
be tested: two independent monocular calibration messages are insufficient.

## Frames

Publish a static chain from `base_link` to `zed2i_link` at the configured
mount center, then to the left/right camera frames at `y = +0.06 / -0.06` m
in the rig frame. Their optical frames use ROS camera axes: x right, y down,
z forward. The rig uses the vehicle convention: x forward, y left, z up.

Image and calibration headers use the corresponding optical frame IDs.
SDF sensor poses and TF must agree, including the conversion from the
vehicle reference point to the chassis link origin. The static transforms
attach to `base_link` under both truth and EKF state-estimation modes;
they do not introduce a second publisher for the moving vehicle transform.

## Launch and visualization

Expose `enable_stereo_camera`, default `false`, independently of `perception`
mode. Enabling it starts the camera bridges and static TF publishers in
both `sim` and `lidar` modes, with truth or EKF odometry. When disabled,
there are no camera bridges/subscribers or camera TF publishers. Lazy
rendering avoids image work when no camera subscribers are connected;
verify this behavior on the installed Gazebo version.

Add left/right RViz Image displays using the proposed topics. The disabled
camera mode must retain the current RViz configuration; select a stereo
configuration only when enabled. Camera geometry and rendering settings
remain in the generated model configuration rather than launch overrides
that could disagree with calibration.

## Validation and failure handling

Automated checks cover configuration validation; generated sensor count,
poses, baseline, optics, and freshness; bridge topic/type/frame agreement;
and launch defaults plus enabled/disabled behavior across perception and
estimator modes. Reuse the existing package test conventions.

Runtime acceptance in ROS 2 Jazzy / Gazebo Harmonic:

1. Both images are nonempty RGB views of the track at the configured size.
2. Image timestamps increase with simulation time; collect at least 100
   exact-timestamp left/right pairs without timestamp rewriting. Each view's
   calibration can be associated with its image at the same timestamp.
3. Both optical transforms are reachable from `base_link` and `map`; header
   frame IDs agree between each image and its calibration.
4. The right projection matrix recovers the 0.12 m baseline, while left
   projection translation remains zero.
5. A stationary textured target at a known distance has horizontal disparity
   consistent with `disparity = fx * baseline / depth`, within two pixels
   after accounting for target localization. Matching features have vertical
   disparity within two pixels. This checks rendered geometry as well as metadata.
6. RViz displays both views and the scene is not occluded by vehicle geometry.
7. Record wall-clock throughput and real-time factor with camera subscribers
   active. The 30 Hz target is simulation-time frequency; slower software
   rendering must not be reported as 30 wall-clock frames per second.
8. Existing perception, state estimation, and model tests pass; the strict
   documentation build passes.

Invalid camera settings fail early with a specific configuration error.
Missing image streams, unusable TF, mismatched calibration, or unsynchronized
views fail camera acceptance and require investigation. ROS/Gazebo runtime
checks must be reported as unverified when the environment is unavailable.

## Implementation scope

Expected code changes are confined to `lhr_vehicle` configuration/loader/tests
and `lhr_gazebo` template/generator, regenerated model, bridge/launch/RViz
configuration, package dependencies, and tests. Update their adjacent READMEs,
the workspace reference, and the camera-fusion plan in the same change.

This milestone publishes camera inputs. SDK depth estimation, visual odometry,
ZED IMU modeling, cone detection/color classification, and LiDAR fusion are
later work. Rendering with ideal zero distortion also omits rolling shutter,
exposure effects, and the physical camera's calibration residuals.

## References

- [Stereolabs ZED 2i specifications](https://docs.stereolabs.com/docs/products/cameras/zed/specifications)
- [Harmonic stereo baseline handling](https://github.com/gazebosim/gz-sim/blob/gz-sim8/src/systems/sensors/Sensors.cc)
- [Gazebo camera calibration publication](https://github.com/gazebosim/gz-sensors/blob/gz-sensors8/src/CameraSensor.cc)
- [ROS 2 CameraInfo contract](https://github.com/ros2/common_interfaces/blob/jazzy/sensor_msgs/msg/CameraInfo.msg)
- [Team ZED 2i request](https://app.notion.com/p/3b1e26707bba804cab45f067a4f7ead6) (does not specify lens or mount)
- [Vehicle parameters](../../ros2/src/lhr_vehicle/README.md)
- [On-car architecture](on-car-architecture.md)
