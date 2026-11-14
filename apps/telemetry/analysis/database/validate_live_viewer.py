"""Publish an Artemis demo and verify its live and historical gateway reads."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import json
import time

from analysis.query_client import GatewayClient
from analysis.database.native_demo import publish


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds")


def validate(duration=5, host="localhost", port=1883, url=None, token=None):
    with GatewayClient(url, token) as client, ThreadPoolExecutor(max_workers=1) as worker:
        catalog = client.discover()
        signals = ["controls.apps1_travel", "pack.hv_pack_v", "thermal.ambient_temp"]
        available = {s["signal"] for s in catalog["signals"]}
        if not set(signals) <= available:
            raise ValueError("Deployed schema is missing demo channels")
        ready_deadline = time.monotonic() + 30
        while not client.status()["ring"]["ready"]:
            if time.monotonic() >= ready_deadline:
                raise TimeoutError("Gateway consumer did not initialize")
            time.sleep(.2)
        started = time.time_ns() // 1_000_000
        sender = worker.submit(publish, duration, 1, 42, host, port)
        seen = set()
        live = client.stream({"signals": signals, "agg": "last", "t0": iso(started - 1000), "t1": "live", "window_ms": 5000, "bucket_ms": 100, "page_size": 1000}, timeout=duration + 30)
        try:
            for event in live:
                if sender.done():
                    sender.result()
                if event["type"] == "gap":
                    raise RuntimeError(event.get("message", "Live coverage gap"))
                seen.update(row["signal"] for row in event.get("page", {}).get("data", []))
                if seen >= set(signals):
                    break
                if time.time_ns() // 1_000_000 - started > (duration + 30) * 1000:
                    raise TimeoutError("No complete live demo window")
        finally:
            live.close()
        sent = sender.result()
        query = {"signals": signals, "agg": "raw", "t0": iso(sent["start_ms"]), "t1": iso(sent["end_ms"])}
        deadline = time.monotonic() + 30
        while True:
            pages = list(client.pages(query))
            rows = [row for page in pages for row in page["data"]]
            expected = {s["signal"]: sent["pool_samples"][s["pool"]] for s in catalog["signals"] if s["signal"] in signals}
            if Counter(row["signal"] for row in rows) == expected:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Historical query did not return every demo observation")
            time.sleep(.2)
        return {"published": sent, "live_signals": sorted(seen), "historical_observations": len(rows), "provisional": any(p["provisional"] for p in pages), "schema_version": catalog["schema_version"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=5)
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--url")
    args = parser.parse_args()
    print(json.dumps(validate(args.duration, args.mqtt_host, args.mqtt_port, args.url)))
