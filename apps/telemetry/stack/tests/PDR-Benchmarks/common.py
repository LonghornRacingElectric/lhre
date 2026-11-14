"""Shared isolated-stack operations and bounded-memory result verification."""
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
import urllib.request

from sqlalchemy import create_engine

PASSWORD = "gateway-test-password"
ADMIN = f"postgresql+psycopg2://electric:{PASSWORD}@localhost:35432/artemis"
PLAIN = f"postgresql+psycopg2://electric:{PASSWORD}@localhost:35433/artemis"
READ = "postgresql+psycopg2://analysis:gateway-test-analysis@localhost:35432/artemis"
ROOT = "http://127.0.0.1:38090"


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def compose(*args):
    return command("docker", "compose", "-p", "pdr-benchmarks", "-f", os.environ["PDR_BASE_COMPOSE"], "-f", os.environ["PDR_COMPOSE"], "--profile", "current", "--profile", "legacy", *args)


def http(path, body=None):
    request = urllib.request.Request(ROOT + path, data=None if body is None else json.dumps(body).encode(), headers={"Authorization": "Bearer gateway-test-token", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as result:
        return json.load(result)


def wait(call, predicate=bool, timeout=600):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            result = call()
            if predicate(result):
                return result
        except Exception as exc:
            error = str(exc)
        time.sleep(.25)
    raise TimeoutError(f"Readiness timed out: {error}")


def stats():
    return http("/v1/stats")


def gateway_mode(l1, l2):
    os.environ["PDR_L1_BYTES"], os.environ["PDR_L2_BYTES"] = str(l1), str(l2)
    compose("up", "-d", "--no-deps", "--force-recreate", "query_gateway")
    wait(lambda: stats(), lambda s: s["ring"]["ready"])


def resources():
    ids = compose("ps", "-q").split()
    return command("docker", "stats", "--no-stream", "--format", "{{json .}}", *ids).splitlines() if ids else []


def quantiles(values):
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {"n": len(values), "p50_ms": statistics.median(values), "p95_ms": ordered[math.ceil(len(values) * .95) - 1], "max_ms": max(values)}


class Fingerprint:
    def __init__(self):
        self.hash = hashlib.sha256()
        self.count = 0

    def add(self, row):
        # Different aggregation orders can differ below the declared tolerance.
        value = row[4]
        value = round(float(value), 8) if isinstance(value, (float, int)) else value
        self.hash.update(json.dumps([*row[:4], value], separators=(",", ":")).encode() + b"\n")
        self.count += 1

    def result(self, sources, pages):
        return {"rows": self.count, "sha256": self.hash.hexdigest(), "sources": sorted(sources), "pages": pages}


def gateway_read(body):
    fingerprint, sources, pages = Fingerprint(), set(), 0
    request = {"car": "artemis", "page_size": 10000, **body}
    while True:
        result = http("/v1/query", request)
        for row in result["data"]:
            fingerprint.add((row["ts"], int(row["session_start_ms"]), int(row["sample_time_ns"]), row["signal"], row["value"]))
        sources.update(result["sources"])
        pages += 1
        if not result["next_page_token"]:
            return fingerprint.result(sources, pages)
        request["page_token"] = result["next_page_token"]


def save(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def engine(url):
    return create_engine(url, pool_size=4, max_overflow=0, pool_timeout=300, connect_args={"options": "-c statement_timeout=30000"})


def environment():
    info = json.loads(command("docker", "info", "--format", "{{json .}}"))
    ids = compose("ps", "-q").split()
    images = command("docker", "inspect", "--format", "{{.Image}}", *ids).splitlines()
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}
    return {"container_log": os.environ.get("PDR_LOG_FILE"), "benchmark_source_sha256": source_hashes, "revision": command("git", "rev-parse", "HEAD"), "docker": {key: info.get(key) for key in ("NCPU", "MemTotal", "Architecture", "ServerVersion")}, "images": [line for line in command("docker", "image", "inspect", "--format", '{{json .RepoTags}}|{{.Id}}|{{.Architecture}}|{{json .RepoDigests}}', *sorted(set(images))).splitlines()]}


def docker_free_bytes():
    container = compose("ps", "-q", "db")
    line = command("docker", "exec", container, "df", "-Pk", "/var/lib/postgresql/data").splitlines()[-1]
    return int(line.split()[3]) * 1024
