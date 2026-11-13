"""Benchmark-only queue telemetry around the actual ingest entry point."""
import json
import logging
import os
from pathlib import Path
import queue
import resource
import threading
import time

from stack.ingest import mqtt_handler


def main():
    original = mqtt_handler.MQTTHandler.__init__

    def measured_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        counters = {"received": 0, "disconnects": 0, "queue_overflows": {}}
        queues = dict(getattr(self, "db_queues", {}))
        for name in ("bridge_queue", "csv_queue"):
            if hasattr(self, name):
                queues[name] = getattr(self, name)
        for name, q in queues.items():
            put = q.put_nowait

            def measured_put(item, put=put, name=name):
                try:
                    return put(item)
                except queue.Full:
                    counters["queue_overflows"][name] = counters["queue_overflows"].get(name, 0) + 1
                    raise
            q.put_nowait = measured_put
        on_message, on_disconnect = self.client.on_message, self.client.on_disconnect

        def message(*args):
            counters["received"] += 1
            return on_message(*args)

        def disconnect(*args):
            counters["disconnects"] += 1
            if on_disconnect:
                return on_disconnect(*args)
        self.client.on_message, self.client.on_disconnect = message, disconnect

        def sample():
            destination = Path(os.environ["PDR_QUEUE_STATE"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            while True:
                state = {"observed_at_ms": time.time_ns() // 1000000, "pid": os.getpid(),
                         "queues": {name: {"size": q.qsize(), "capacity": q.maxsize} for name, q in queues.items()},
                         "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024, **counters}
                temp = destination.with_suffix(".tmp")
                temp.write_text(json.dumps(state))
                temp.replace(destination)
                time.sleep(1)
        threading.Thread(target=sample, daemon=True).start()
    mqtt_handler.MQTTHandler.__init__ = measured_init
    logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
    mqtt_handler.main()


if __name__ == "__main__":
    main()
