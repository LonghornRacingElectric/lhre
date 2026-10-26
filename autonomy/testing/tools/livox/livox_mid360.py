# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Minimal Livox Mid-360 client (SDK2 protocol) for bench and field tests on macOS.

The ROS driver can't run in the Mac Docker container, and Livox Viewer has no macOS
build, so this speaks the wire protocol directly. Protocol details come from
Livox-SDK2 (sdk_core/comm/sdk_protocol.*, include/livox_lidar_def.h).

  uv run livox_mid360.py probe                 # read-only status and stored config
  uv run livox_mid360.py mode normal|wakeup    # spin up / idle the motor
  uv run livox_mid360.py capture --seconds 10 --out cap.npz
"""
import argparse, binascii, selectors, socket, struct, time

LIDAR_IP, HOST_IP = "192.168.1.134", "192.168.1.50"
P_DETECT, P_CMD_L, P_CMD_H = 56000, 56100, 56101
P_PUSH_H, P_PTS_H, P_IMU_H = 56201, 56301, 56401

CMD_SEARCH, CMD_SET, CMD_GET = 0x0000, 0x0100, 0x0101
WORK = {1: "NORMAL", 2: "WAKEUP", 3: "SLEEP", 4: "ERROR", 5: "SELFTEST", 6: "MOTOR_STARTING", 7: "MOTOR_STOPPING", 8: "UPGRADE"}

# Point and IMU packets share a 36 B header: ver, len, time_interval (0.1 us), dot_num,
# udp_cnt, frame_cnt, data_type, time_type, rsvd[12], crc32, timestamp (ns).
PT_HDR = struct.Struct("<BHHHHBBB12sIQ")


def crc16(b: bytes) -> int:  # CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF
    c = 0xFFFF
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def pack(cmd_id: int, data: bytes, seq: int) -> bytes:
    head = struct.pack("<BBHIHBB6s", 0xAA, 0, 24 + len(data), seq, cmd_id, 0, 0, b"\0" * 6)
    c32 = binascii.crc32(data) & 0xFFFFFFFF if data else 0
    return head + struct.pack("<HI", crc16(head), c32) + data


def unpack(pkt: bytes):
    sof, ver, length, seq, cmd_id, ctype, sender = struct.unpack_from("<BBHIHBB", pkt)
    crc_h, crc_d = struct.unpack_from("<HI", pkt, 18)
    data = pkt[24:length]
    ok = sof == 0xAA and crc_h == crc16(pkt[:18]) and crc_d == ((binascii.crc32(data) & 0xFFFFFFFF) if data else 0)
    return dict(seq=seq, cmd_id=cmd_id, type=ctype, sender=sender, data=data, crc_ok=ok)


def ip(b):
    return ".".join(str(x) for x in b)


def udp_sock(port, buf=16 << 20, blocking=False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for size in (buf, 8 << 20, 4 << 20, 1 << 20):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
            break
        except OSError:
            continue
    s.bind((HOST_IP, port))
    s.setblocking(blocking)
    return s


class Lidar:
    def __init__(self, timeout=1.0):
        self.cmd = udp_sock(P_CMD_H, 1 << 20, blocking=True)
        self.cmd.settimeout(timeout)
        self.seq = int(time.time()) & 0xFFFF

    def request(self, cmd_id, data=b""):
        self.seq += 1
        self.cmd.sendto(pack(cmd_id, data, self.seq), (LIDAR_IP, P_CMD_L))
        deadline = time.time() + self.cmd.gettimeout()
        while time.time() < deadline:
            try:
                buf, _ = self.cmd.recvfrom(2048)
            except socket.timeout:
                break
            r = unpack(buf)
            if r["cmd_id"] == cmd_id and r["type"] == 1:
                return r
        raise TimeoutError(f"no ack for cmd 0x{cmd_id:04x}")

    def get(self, keys):
        r = self.request(CMD_GET, struct.pack("<HH", len(keys), 0) + b"".join(struct.pack("<H", k) for k in keys))
        d = r["data"]
        ret, n = struct.unpack_from("<BH", d)
        out, off = {}, 3
        for _ in range(n):
            k, ln = struct.unpack_from("<HH", d, off)
            out[k] = d[off + 4: off + 4 + ln]
            off += 4 + ln
        return ret, out, r["crc_ok"]

    def set(self, items):
        data = struct.pack("<HH", len(items), 0) + b"".join(struct.pack("<HH", k, len(v)) + v for k, v in items)
        ret, err_key = struct.unpack_from("<BH", self.request(CMD_SET, data)["data"])
        return ret, err_key

    def state(self):
        _, kv, _ = self.get([0x8006])
        return kv.get(0x8006, b"\xff")[0]


def set_mode(L, mode, wait=20.0):
    ret, err = L.set([(0x001A, bytes([mode]))])
    t0 = time.time()
    st = None
    while time.time() - t0 < wait:
        st = L.state()
        if st == mode:
            break
        time.sleep(0.25)
    return ret, err, WORK.get(st, st), time.time() - t0


def probe(_a):
    L = Lidar()
    keys = [0x8000, 0x8001, 0x8002, 0x8005, 0x8006, 0x8007, 0x8008, 0x800E,
            0x0000, 0x0001, 0x0004, 0x0005, 0x0006, 0x0007, 0x001A, 0x001C, 0x0012]
    ret, kv, crc_ok = L.get(keys)
    host = lambda v: f"{ip(v[0:4])}:{struct.unpack_from('<H', v, 4)[0]}"
    fmt = {
        0x8000: ("sn", lambda v: v.split(b"\0")[0].decode()),
        0x8001: ("product", lambda v: v.split(b"\0")[0].decode(errors="replace")),
        0x8002: ("fw_app", lambda v: ".".join(str(x) for x in v)),
        0x8005: ("mac", lambda v: ":".join(f"{x:02x}" for x in v)),
        0x8006: ("work_state", lambda v: WORK.get(v[0], v[0])),
        0x8007: ("core_temp_C", lambda v: struct.unpack("<i", v)[0] / 100),
        0x8008: ("power_up_count", lambda v: struct.unpack("<I", v)[0]),
        0x800E: ("diag_status", lambda v: v.hex()),
        0x0000: ("pcl_data_type", lambda v: v[0]),
        0x0001: ("pattern_mode", lambda v: v[0]),
        0x0004: ("lidar_ip/mask/gw", lambda v: f"{ip(v[0:4])} {ip(v[4:8])} {ip(v[8:12])}"),
        0x0005: ("push_host", host),
        0x0006: ("points_host", host),
        0x0007: ("imu_host", host),
        0x001A: ("work_mode", lambda v: WORK.get(v[0], v[0])),
        0x001C: ("imu_enabled", lambda v: v[0]),
        0x0012: ("install_attitude", lambda v: struct.unpack("<fffiii", v)),
    }
    print(f"get ret={ret} crc_ok={crc_ok}")
    for k in keys:
        name, f = fmt[k]
        print(f"  {name}: {f(kv[k]) if k in kv else '(not returned)'}")


def mode_cmd(a):
    m = {"normal": 1, "wakeup": 2, "sleep": 3}[a.mode]
    ret, err, st, dt = set_mode(Lidar(), m)
    print(f"set work_mode={a.mode} ret={ret} err_key=0x{err:04x} -> state {st} after {dt:.1f} s")


def drain(sel, sink=None, until=0.0):
    while time.time() < until:
        for key, _ in sel.select(0.05):
            try:
                while True:
                    b = key.fileobj.recv(2048)
                    if sink is not None:
                        sink[key.data].append(b)
            except BlockingIOError:
                pass


def capture(a):
    import numpy as np
    socks = {"pts": udp_sock(P_PTS_H), "imu": udp_sock(P_IMU_H), "push": udp_sock(P_PUSH_H, 1 << 20)}
    L = Lidar()
    if L.state() != 1:
        ret, err, st, dt = set_mode(L, 1)
        print(f"spin-up: ret={ret} state={st} after {dt:.1f} s")
    sel = selectors.DefaultSelector()
    for k, s in socks.items():
        sel.register(s, selectors.EVENT_READ, k)
    drain(sel, None, time.time() + a.settle)  # discard spin-up transients
    raw = {"pts": [], "imu": [], "push": []}
    t0 = time.time()
    drain(sel, raw, t0 + a.seconds)
    dur = time.time() - t0

    hdrs = [PT_HDR.unpack_from(b) for b in raw["pts"]]
    udp = np.array([h[4] for h in hdrs], dtype=np.int64)
    gaps = (np.diff(udp) % 65536) - 1 if len(udp) > 1 else np.zeros(1, np.int64)
    lost = int(gaps[gaps > 0].sum())
    ptdt = np.dtype([("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("r", "u1"), ("tag", "u1")])
    arrs, ts = [], []
    for b, h in zip(raw["pts"], hdrs):
        if h[6] != 1:
            continue
        n = h[3]
        arrs.append(np.frombuffer(b, dtype=ptdt, count=n, offset=36))
        ts.append(h[10] + np.arange(n) * (h[2] * 100.0 / max(n, 1)))
    P = np.concatenate(arrs) if arrs else np.zeros(0, ptdt)
    T = (np.concatenate(ts) if ts else np.zeros(0)) * 1e-9
    xyz = np.stack([P["x"], P["y"], P["z"]], 1).astype(np.float32) / 1000.0
    rng = np.linalg.norm(xyz, axis=1)
    valid = rng > 0.05
    imu = np.array([struct.unpack_from("<6f", b, 36) for b in raw["imu"]], dtype=np.float64).reshape(-1, 6)
    imu_t = np.array([PT_HDR.unpack_from(b)[10] for b in raw["imu"]], dtype=np.float64) * 1e-9

    print(f"--- {dur:.1f} s capture")
    print(f"point packets {len(hdrs)}  data_type {sorted(set(h[6] for h in hdrs))}  dot_num {sorted(set(h[3] for h in hdrs))}  "
          f"time_interval(0.1us) {sorted(set(h[2] for h in hdrs))[:4]}")
    print(f"points {len(P):,} -> {len(P)/dur:,.0f} pts/s   lost packets (udp_cnt gaps): {lost}")
    if len(T):
        print(f"sensor time span {T[-1]-T[0]:.3f} s vs wall {dur:.3f} s")
    if valid.any():
        print(f"returns {valid.mean()*100:.1f}% non-zero   range m p5/p50/p95/max: "
              + " / ".join(f"{np.percentile(rng[valid], q):.2f}" for q in (5, 50, 95, 100)))
    if len(imu) > 1:
        g = np.degrees(imu[:, :3])
        acc = imu[:, 3:]
        dt = np.diff(imu_t)
        print(f"imu {len(imu)} samples  {(len(imu_t)-1)/(imu_t[-1]-imu_t[0]):.1f} Hz  max gap {dt.max()*1e3:.1f} ms")
        print("gyro mean deg/s x/y/z " + " / ".join(f"{v:+.2f}" for v in g.mean(0)) + "   std " + " / ".join(f"{v:.2f}" for v in g.std(0)))
        print("accel mean g x/y/z " + " / ".join(f"{v:+.3f}" for v in acc.mean(0)) + f"   |a| {np.linalg.norm(acc.mean(0)):.3f}")
    print(f"push messages {len(raw['push'])}   core temp {struct.unpack('<i', L.get([0x8007])[1][0x8007])[0]/100:.1f} C")
    if a.out:
        np.savez_compressed(a.out, xyz=xyz, refl=P["r"], tag=P["tag"], t=T, imu=imu, imu_t=imu_t, udp=udp, dur=dur)
        print(f"saved {a.out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe").set_defaults(fn=probe)
    m = sub.add_parser("mode")
    m.add_argument("mode", choices=["normal", "wakeup", "sleep"])
    m.set_defaults(fn=mode_cmd)
    c = sub.add_parser("capture")
    c.add_argument("--seconds", type=float, default=10)
    c.add_argument("--settle", type=float, default=1.0)
    c.add_argument("--out", default=None)
    c.set_defaults(fn=capture)
    a = ap.parse_args()
    a.fn(a)
