# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "mcap", "mcap-ros2-support"]
# ///
"""Convert a capture (.npz from livox_mid360.py / livox_live.py) into a ROS 2 bag (MCAP, CDR) that the
autonomy stack replays unchanged. No ROS install needed to write it.

Topics:
  /lhr/lidar/points    sensor_msgs/PointCloud2, one message per 0.1 s frame, frame livox_frame.
                       Fields x, y, z, intensity (float32, reflectivity 0-255), tag (uint8, Livox
                       confidence bits), timestamp (float64, ns). Shots with no return are dropped.
  /livox/imu           sensor_msgs/Imu, 200 Hz. Angular velocity rad/s, acceleration m/s^2 (SI;
                       livox_ros_driver2 publishes g, so this differs from a bag recorded with it).
  /lhr/vehicle/odom    nav_msgs/Odometry, 20 Hz, identity pose: the sensor stood still, and the
                       cone detector does not process clouds until it has odometry.
  /tf_static           base_link -> livox_frame at the tape-measured sensor height.

Timestamps are wall clock: the recording's end time minus its duration, plus the sensor clock offset.
Replay with `ros2 bag play -s mcap <file>.mcap`.

  uv run ros2_bag.py capture.npz out.mcap --end "2026-10-04 15:32:50" --height 0.58
"""
import argparse, datetime, math, struct

import numpy as np
from mcap_ros2.writer import Writer

SEP = "\n================================================================================\n"
TIME = "MSG: builtin_interfaces/Time\nint32 sec\nuint32 nanosec"
HEADER = "MSG: std_msgs/Header\nbuiltin_interfaces/Time stamp\nstring frame_id"
VEC3 = "MSG: geometry_msgs/Vector3\nfloat64 x\nfloat64 y\nfloat64 z"
QUAT = "MSG: geometry_msgs/Quaternion\nfloat64 x 0\nfloat64 y 0\nfloat64 z 0\nfloat64 w 1"
POINT = "MSG: geometry_msgs/Point\nfloat64 x\nfloat64 y\nfloat64 z"
POINTFIELD = ("MSG: sensor_msgs/PointField\nuint8 INT8=1\nuint8 UINT8=2\nuint8 INT16=3\nuint8 UINT16=4\nuint8 INT32=5\n"
              "uint8 UINT32=6\nuint8 FLOAT32=7\nuint8 FLOAT64=8\nstring name\nuint32 offset\nuint8 datatype\nuint32 count")
DEFS = {
    "sensor_msgs/msg/PointCloud2": SEP.join([
        "std_msgs/Header header\nuint32 height\nuint32 width\nsensor_msgs/PointField[] fields\nbool is_bigendian\n"
        "uint32 point_step\nuint32 row_step\nuint8[] data\nbool is_dense", HEADER, TIME, POINTFIELD]),
    "sensor_msgs/msg/Imu": SEP.join([
        "std_msgs/Header header\ngeometry_msgs/Quaternion orientation\nfloat64[9] orientation_covariance\n"
        "geometry_msgs/Vector3 angular_velocity\nfloat64[9] angular_velocity_covariance\n"
        "geometry_msgs/Vector3 linear_acceleration\nfloat64[9] linear_acceleration_covariance", HEADER, TIME, QUAT, VEC3]),
    "nav_msgs/msg/Odometry": SEP.join([
        "std_msgs/Header header\nstring child_frame_id\ngeometry_msgs/PoseWithCovariance pose\n"
        "geometry_msgs/TwistWithCovariance twist", HEADER, TIME,
        "MSG: geometry_msgs/PoseWithCovariance\ngeometry_msgs/Pose pose\nfloat64[36] covariance",
        "MSG: geometry_msgs/Pose\ngeometry_msgs/Point position\ngeometry_msgs/Quaternion orientation", POINT, QUAT,
        "MSG: geometry_msgs/TwistWithCovariance\ngeometry_msgs/Twist twist\nfloat64[36] covariance",
        "MSG: geometry_msgs/Twist\ngeometry_msgs/Vector3 linear\ngeometry_msgs/Vector3 angular", VEC3]),
    "tf2_msgs/msg/TFMessage": SEP.join([
        "geometry_msgs/TransformStamped[] transforms",
        "MSG: geometry_msgs/TransformStamped\nstd_msgs/Header header\nstring child_frame_id\ngeometry_msgs/Transform transform",
        HEADER, TIME, "MSG: geometry_msgs/Transform\ngeometry_msgs/Vector3 translation\ngeometry_msgs/Quaternion rotation",
        VEC3, QUAT]),
}
FIELDS = [dict(name="x", offset=0, datatype=7, count=1), dict(name="y", offset=4, datatype=7, count=1),
          dict(name="z", offset=8, datatype=7, count=1), dict(name="intensity", offset=12, datatype=7, count=1),
          dict(name="tag", offset=16, datatype=2, count=1), dict(name="timestamp", offset=17, datatype=8, count=1)]
PT = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "<f4"), ("tag", "u1"), ("timestamp", "<f8")])
G = 9.80665


def stamp(ns):
    return dict(sec=int(ns // 1_000_000_000), nanosec=int(ns % 1_000_000_000))


def convert(npz, out, end, height):
    d = np.load(npz)
    xyz, refl, tag, t = d["xyz"], d["refl"], d["tag"], d["t"]
    imu, imu_t = d["imu"], d["imu_t"]
    t0 = float(t.min())
    dur = float(t.max()) - t0
    start_ns = int((end.timestamp() - dur) * 1e9)
    to_wall = lambda ts: start_ns + int(round((ts - t0) * 1e9))
    ret = np.linalg.norm(xyz, axis=1) > 0
    frames = np.floor((t - t0) / 0.1).astype(int)

    with open(out, "wb") as f:
        w = Writer(f)
        sch = {k: w.register_msgdef(k, v) for k, v in DEFS.items()}
        ident = dict(x=0.0, y=0.0, z=0.0, w=1.0)
        w.write_message("/tf_static", sch["tf2_msgs/msg/TFMessage"], dict(transforms=[dict(
            header=dict(stamp=stamp(start_ns), frame_id="base_link"), child_frame_id="livox_frame",
            transform=dict(translation=dict(x=0.0, y=0.0, z=height), rotation=ident))]), log_time=start_ns, publish_time=start_ns)

        n_clouds = 0
        for k in range(frames.max() + 1):
            m = ret & (frames == k)
            if not m.any():
                continue
            arr = np.zeros(int(m.sum()), PT)
            arr["x"], arr["y"], arr["z"] = xyz[m, 0], xyz[m, 1], xyz[m, 2]
            arr["intensity"], arr["tag"] = refl[m], tag[m]
            arr["timestamp"] = (start_ns + (t[m] - t0) * 1e9).astype(np.float64)
            ns = to_wall(t0 + k * 0.1)
            w.write_message("/lhr/lidar/points", sch["sensor_msgs/msg/PointCloud2"], dict(
                header=dict(stamp=stamp(ns), frame_id="livox_frame"), height=1, width=len(arr), fields=FIELDS,
                is_bigendian=False, point_step=PT.itemsize, row_step=PT.itemsize * len(arr), data=arr.tobytes(), is_dense=True),
                log_time=ns, publish_time=ns)
            n_clouds += 1

        zero9 = [0.0] * 9
        for g, ts in zip(imu, imu_t):
            ns = to_wall(float(ts))
            w.write_message("/livox/imu", sch["sensor_msgs/msg/Imu"], dict(
                header=dict(stamp=stamp(ns), frame_id="livox_frame"), orientation=ident,
                orientation_covariance=[-1.0] + [0.0] * 8,  # -1: no orientation estimate
                angular_velocity=dict(x=float(g[0]), y=float(g[1]), z=float(g[2])), angular_velocity_covariance=zero9,
                linear_acceleration=dict(x=float(g[3]) * G, y=float(g[4]) * G, z=float(g[5]) * G),
                linear_acceleration_covariance=zero9), log_time=ns, publish_time=ns)

        zero36 = [0.0] * 36
        for k in range(int(dur * 20) + 1):
            ns = start_ns + k * 50_000_000
            w.write_message("/lhr/vehicle/odom", sch["nav_msgs/msg/Odometry"], dict(
                header=dict(stamp=stamp(ns), frame_id="map"), child_frame_id="base_link",
                pose=dict(pose=dict(position=dict(x=0.0, y=0.0, z=0.0), orientation=ident), covariance=zero36),
                twist=dict(twist=dict(linear=dict(x=0.0, y=0.0, z=0.0), angular=dict(x=0.0, y=0.0, z=0.0)), covariance=zero36)),
                log_time=ns, publish_time=ns)
        w.finish()
    return n_clouds, len(imu), dur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("out")
    ap.add_argument("--end", required=True, help="local wall-clock time the recording ended, 'YYYY-mm-dd HH:MM:SS'")
    ap.add_argument("--height", type=float, default=0.58)
    a = ap.parse_args()
    end = datetime.datetime.strptime(a.end, "%Y-%m-%d %H:%M:%S").astimezone()
    print(convert(a.npz, a.out, end, a.height))
