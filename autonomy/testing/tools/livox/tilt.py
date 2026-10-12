# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets"]
# ///
"""Live sensor tilt from the streamer's IMU topic, for levelling or setting a known tilt toward the cones (-x)."""
import asyncio, json, math, time

import websockets


async def main():
    async with websockets.connect("ws://127.0.0.1:8765", subprotocols=["foxglove.sdk.v1"], max_size=None) as ws:
        ch = None
        while ch is None:
            j = json.loads(await ws.recv())
            if j.get("op") == "advertise":
                ch = next((c["id"] for c in j["channels"] if c["topic"] == "/livox/imu"), None)
        await ws.send(json.dumps({"op": "subscribe", "subscriptions": [{"id": 1, "channelId": ch}]}))
        a, t_end = [], time.time() + 1.0
        while time.time() < t_end:
            m = await ws.recv()
            if isinstance(m, bytes) and m[0] == 1:
                a.append(json.loads(m[13:])["accel_g"])
        x, y, z = (sum(v[k] for v in a) / len(a) for k in "xyz")
        pitch = math.degrees(math.atan2(x, z))  # > 0: +x side high, so the -x (cone) side is low
        print(f"cone side (-x) {abs(pitch):.1f} deg {'low' if pitch > 0 else 'high'}; sideways {math.degrees(math.atan2(y, z)):+.1f} deg")


asyncio.run(main())
