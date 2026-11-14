"""Bounded interval summaries; late commits stay assigned to their send interval."""
import math
import statistics


def quantiles(values):
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {"n": len(values), "p50_ms": statistics.median(ordered), "p95_ms": ordered[math.ceil(.95 * len(ordered)) - 1], "max_ms": ordered[-1]}


class Timeline:
    def __init__(self, interval=10):
        self.interval = interval
        self.buckets = {}
        self.cache = {}
        self.dirty = set()

    def bucket(self, elapsed):
        index = max(0, int(elapsed // self.interval))
        self.dirty.add(index)
        return self.buckets.setdefault(index, {"sent": 0, "latencies": []})

    def send(self, elapsed):
        self.bucket(elapsed)["sent"] += 1

    def commit(self, sent_elapsed, latency_ms):
        self.bucket(sent_elapsed)["latencies"].append(latency_ms)

    def snapshot(self):
        for i in self.dirty:
            b = self.buckets[i]
            self.cache[i] = {"start_s": i * self.interval, "end_s": (i + 1) * self.interval,
                             "sent": b["sent"], "persisted": len(b["latencies"]),
                             "unobserved": b["sent"] - len(b["latencies"]), **quantiles(b["latencies"])}
        self.dirty.clear()
        return [dict(self.cache[i]) for i in sorted(self.cache)]
