# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "foxglove-sdk"]
# ///
"""Stream a Mid-360 to Foxglove live, log health once a second, optionally record MCAP.

Foxglove: open a connection to ws://localhost:8765. Topics:
  /livox/points  foxglove.PointCloud, one frame per --frame seconds (x, y, z m; intensity = reflectivity)
  /livox/imu     JSON at 200 Hz: gyro_dps x/y/z, accel_g x/y/z
  /livox/health  JSON at 1 Hz: points/s, lost packets, IMU rate, core temp, work state
  /tf            identity world -> livox_frame at 1 Hz, so the 3D panel has a display frame

The health lines also go to stdout and to --health CSV, which is the record for the
60-minute endurance test. Stop with Ctrl-C or SIGTERM; the MCAP is closed cleanly.

Triggered recording: write "<name> [seconds]" to rec.txt next to this script. The streamer
records that window to bags/<name>.mcap (everything published, for the ROS side) and
bags/<name>.npz (raw points and IMU, same layout as livox_mid360.py capture).
"""
import argparse, csv, json, pathlib, selectors, signal, struct, threading, time

import foxglove
import numpy as np
from foxglove.messages import (FrameTransform, PackedElementField, PackedElementFieldNumericType as NT, PointCloud, Pose,
                               Quaternion, Timestamp, Vector3)

from livox_mid360 import P_IMU_H, P_PTS_H, P_PUSH_H, PT_HDR, WORK, Lidar, set_mode, udp_sock

HERE = pathlib.Path(__file__).resolve().parent
REC_TRIGGER, BAG_DIR = HERE / "rec.txt", HERE / "bags"
PTDT = np.dtype([("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("r", "u1"), ("tag", "u1")])
# Each JSON topic gets its own named schema. Left schemaless, the SDK advertises both under one
# generated name and Foxglove applies one topic's fields to the other, so plot paths go blank.
def json_channel(topic, name, props):
    schema = {"type": "object", "properties": props}
    return foxglove.Channel(topic, schema=foxglove.Schema(name=name, encoding="jsonschema", data=json.dumps(schema).encode()),
                            message_encoding="json")


NUM = {"type": "number"}
VEC3 = {"type": "object", "properties": {"x": NUM, "y": NUM, "z": NUM}}
FIELDS = [PackedElementField(name=n, offset=4 * i, type=NT.Float32) for i, n in enumerate(("x", "y", "z", "intensity"))]


def stamp(ns):
    return Timestamp(ns // 1_000_000_000, ns % 1_000_000_000)


def tf_msg():
    return FrameTransform(timestamp=stamp(time.time_ns()), parent_frame_id="world", child_frame_id="livox_frame",
                          translation=Vector3(), rotation=Quaternion(w=1.0))


def save_npz(path, rec, secs):
    P = np.concatenate(rec["pts"]) if rec["pts"] else np.zeros(0, PTDT)
    T = (np.concatenate(rec["ts"]) if rec["ts"] else np.zeros(0)) * 1e-9
    xyz = np.stack([P["x"], P["y"], P["z"]], 1).astype(np.float32) / 1000.0
    np.savez(path, xyz=xyz, refl=P["r"], tag=P["tag"], t=T, imu=np.array(rec["imu"]).reshape(-1, 6),
             imu_t=np.array(rec["imu_t"], dtype=np.float64) * 1e-9, dur=secs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", type=float, default=0.1, help="seconds of points per published cloud")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until stopped)")
    ap.add_argument("--record", default=None, help="MCAP path to record everything published")
    ap.add_argument("--health", default=None, help="CSV path for the once-a-second health log")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--idle-on-exit", action="store_true", help="put the sensor back in WAKEUP (motor off) on exit")
    a = ap.parse_args()

    stop = {"flag": False}
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.__setitem__("flag", True))

    socks = {"pts": udp_sock(P_PTS_H), "imu": udp_sock(P_IMU_H), "push": udp_sock(P_PUSH_H, 1 << 20)}
    L = Lidar()
    if L.state() != 1:
        ret, err, st, dt = set_mode(L, 1)
        print(f"spin-up: ret={ret} state={st} after {dt:.1f} s", flush=True)

    server = foxglove.start_server(name="mid360", host=a.host, port=8765)
    imu_ch = json_channel("/livox/imu", "livox.Imu", {"gyro_dps": VEC3, "accel_g": VEC3})
    health_ch = json_channel("/livox/health", "livox.Health", {
        "pts_per_s": NUM, "packets": NUM, "lost_1s": NUM, "lost_total": NUM, "imu_hz": NUM,
        "core_temp_c": NUM, "work_state": {"type": "string"}, "zero_return_pct": NUM, "uptime_s": NUM})
    mcap = foxglove.open_mcap(a.record, allow_overwrite=True) if a.record else None
    hfile = open(a.health, "a", newline="") if a.health else None
    hcsv = csv.writer(hfile) if hfile else None
    if hcsv and hfile.tell() == 0:
        hcsv.writerow(["wall_time", "uptime_s", "pts_per_s", "packets", "lost_1s", "lost_total", "imu_hz", "core_temp_c", "work_state", "zero_return_pct"])

    sel = selectors.DefaultSelector()
    for k, s in socks.items():
        sel.register(s, selectors.EVENT_READ, k)

    t_start = time.time()
    frame, frame_t0 = [], time.time_ns()
    last_udp = None
    lost_total = 0
    win = dict(pts=0, pkts=0, lost=0, imu=0, zero=0)
    t_health = time.time() + 1.0
    temp, state = float("nan"), "?"
    t_poll = 0.0
    rec, t_trig = None, 0.0
    REC_TRIGGER.unlink(missing_ok=True)
    print(f"streaming to ws://{a.host}:8765" + (f", recording {a.record}" if a.record else ""), flush=True)

    while not stop["flag"] and (a.seconds <= 0 or time.time() - t_start < a.seconds):
        for key, _ in sel.select(0.02):
            s = key.fileobj
            try:
                while True:
                    b = s.recv(2048)
                    if key.data == "pts":
                        h = PT_HDR.unpack_from(b)
                        if last_udp is not None:
                            gap = (h[4] - last_udp - 1) % 65536
                            win["lost"] += gap
                            lost_total += gap
                        last_udp = h[4]
                        win["pkts"] += 1
                        if h[6] == 1:
                            arr = np.frombuffer(b, dtype=PTDT, count=h[3], offset=36)
                            frame.append(arr)
                            win["pts"] += h[3]
                            if rec:
                                rec["pts"].append(arr)
                                rec["ts"].append(h[10] + np.arange(h[3]) * (h[2] * 100.0 / max(h[3], 1)))
                    elif key.data == "imu":
                        g = struct.unpack_from("<6f", b, 36)
                        win["imu"] += 1
                        if rec:
                            rec["imu"].append(g)
                            rec["imu_t"].append(PT_HDR.unpack_from(b)[10])
                        d = np.degrees(g[:3])
                        imu_ch.log({"gyro_dps": {"x": float(d[0]), "y": float(d[1]), "z": float(d[2])},
                                    "accel_g": {"x": g[3], "y": g[4], "z": g[5]}})
            except BlockingIOError:
                pass

        now = time.time_ns()
        if frame and now - frame_t0 >= a.frame * 1e9:
            p = np.concatenate(frame)
            frame, frame_t0 = [], now
            nz = (p["x"] != 0) | (p["y"] != 0) | (p["z"] != 0)
            win["zero"] += int((~nz).sum())
            p = p[nz]
            arr = np.empty((len(p), 4), np.float32)
            arr[:, 0], arr[:, 1], arr[:, 2] = p["x"] / 1000.0, p["y"] / 1000.0, p["z"] / 1000.0
            arr[:, 3] = p["r"]
            foxglove.log("/livox/points", PointCloud(
                timestamp=stamp(now), frame_id="livox_frame",
                pose=Pose(position=Vector3(), orientation=Quaternion(w=1.0)),
                point_stride=16, fields=FIELDS, data=arr.tobytes()))

        if time.time() >= t_poll:  # off the hot path: ack comes back in a few ms
            try:
                _, kv, _ = L.get([0x8006, 0x8007])
                state = WORK.get(kv[0x8006][0], kv[0x8006][0])
                temp = struct.unpack("<i", kv[0x8007])[0] / 100
            except Exception as e:
                state = f"poll failed: {e}"
            t_poll = time.time() + 5.0

        if time.time() >= t_trig:
            t_trig = time.time() + 0.5
            if rec is None and REC_TRIGGER.exists():
                parts = REC_TRIGGER.read_text().split()
                REC_TRIGGER.unlink()
                name, secs = parts[0], float(parts[1]) if len(parts) > 1 else 10.0
                BAG_DIR.mkdir(exist_ok=True)
                rec = dict(name=name, secs=secs, until=time.time() + secs, lost0=lost_total, pts=[], ts=[], imu=[], imu_t=[],
                           writer=foxglove.open_mcap(str(BAG_DIR / f"{name}.mcap"), allow_overwrite=True))
                foxglove.log("/tf", tf_msg())
                print(f"REC start {name} ({secs:.0f} s)", flush=True)
            elif rec and time.time() >= rec["until"]:
                rec["writer"].close()
                n = sum(len(x) for x in rec["pts"])
                threading.Thread(target=save_npz, args=(BAG_DIR / f"{rec['name']}.npz", rec, rec["secs"])).start()
                print(f"REC done {rec['name']}: {n:,} points, lost {lost_total - rec['lost0']} packets -> bags/{rec['name']}.mcap + .npz", flush=True)
                rec = None

        if time.time() >= t_health:
            up = time.time() - t_start
            zero_pct = 100.0 * win["zero"] / max(win["pts"], 1)
            row = dict(pts_per_s=win["pts"], packets=win["pkts"], lost_1s=win["lost"], lost_total=lost_total,
                       imu_hz=win["imu"], core_temp_c=None if temp != temp else temp, work_state=str(state), zero_return_pct=round(zero_pct, 1), uptime_s=round(up))
            health_ch.log(row)
            foxglove.log("/tf", tf_msg())
            print(f"{time.strftime('%H:%M:%S')} up {up:6.0f}s  {win['pts']:7d} pts/s  lost {win['lost']} (total {lost_total})  "
                  f"imu {win['imu']} Hz  {temp:.1f} C  {state}", flush=True)
            if hcsv:
                hcsv.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), round(up), win["pts"], win["pkts"], win["lost"], lost_total,
                               win["imu"], temp, state, round(zero_pct, 1)])
                hfile.flush()
            win = dict(pts=0, pkts=0, lost=0, imu=0, zero=0)
            t_health += 1.0

    if rec:
        rec["writer"].close()
        save_npz(BAG_DIR / f"{rec['name']}.npz", rec, rec["secs"])
    if mcap:
        mcap.close()
        print(f"closed {a.record}", flush=True)
    if hfile:
        hfile.close()
    server.stop()
    if a.idle_on_exit:
        ret, err, st, dt = set_mode(L, 2)
        print(f"idle: state={st} after {dt:.1f} s", flush=True)


if __name__ == "__main__":
    main()
