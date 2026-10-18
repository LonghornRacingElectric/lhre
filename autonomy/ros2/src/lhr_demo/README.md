# lhr_demo

`mvs_demo.launch.py` starts the kinematic autonomy stack. It can also record
the ROS topic contract to MCAP or serve the live graph to Foxglove. Both are
off by default; neither changes the vehicle plant or cone perception.

`lidar:=true` adds a synthetic Mid-360 cloud on `/lhr/lidar/points`, with a
moving `base_link -> lidar` transform. It is a preview: the car still drives
from the simplified cone sensor. `estimator:=ekf` keeps rendering the physical
scene from truth odometry while the controller uses the estimate.

```bash
ros2 launch lhr_demo mvs_demo.launch.py foxglove:=true
ros2 launch lhr_demo mvs_demo.launch.py record:=true run_id:=trial_001
ros2 launch lhr_demo mvs_demo.launch.py foxglove:=true lidar:=true
```

Connect Foxglove to `ws://localhost:8765`. This requires the
`ros-jazzy-foxglove-bridge` package and a separate Foxglove app or browser
session. The launch command does not open a window. `foxglove_port` changes the
bridge port.

Recording requires `rosbag2_storage_mcap`. A bag goes to
`data/bags/<run_id>` by default; `bag_dir` changes its parent directory.
`run_id` is also passed to `lhr_metrics`, whose CSV defaults to
`data/metrics.csv` and can be moved with `output_csv`. Bag metadata carries
the run ID and Git revision, with `-dirty` when the source checkout has local
changes. Use a new run ID for each recording so an existing bag is never
overwritten. The default ID uses a date and time with a `T` separator to
avoid YAML interpreting it as a number.

The recorder selects the inputs and outputs needed to inspect the stack:
transforms, cones, centerline, odometry, commands, mission status, and sensor
topics. It does not record every visualization topic. Topics absent from a
particular run are omitted from the bag. When `lidar:=true`, the cloud and
its `/tf` mount transform are recorded. Cone detections still come from
`lhr_sensor_sim`. `mount_pitch_rad`, `elevation_profile`, `return_profile`,
`motion_distortion`, and `clutter_profile` control the preview; see the
[sensor model](../lhr_lidar_sim/README.md) for their assumptions.
Use `ros2 bag info data/bags/<run_id>` to inspect a completed bag. With
`enable_metrics:=true`, a metrics row is written after a lap or when a run
with odometry stops.

This viewer and recorder integration does not add a pass/fail gate. A visible
moving car or a bag file alone does not establish a successful LiDAR lap.
