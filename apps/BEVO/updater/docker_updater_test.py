"""Exercise running server images and updater activation with isolated Docker state."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import uuid

from apps.BEVO.updater.oci import fetch
from apps.BEVO.updater.updater import EXECUTABLES, MAX_RELEASE_BYTES, METADATA_FILES, unpack
from apps.telemetry.schema_registry.deployment import DockerDeployment
from apps.telemetry.schema_registry.server import create_server


class DockerUpdaterTest(unittest.TestCase):
    def docker(self, *arguments, **kwargs):
        return subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=40, **kwargs)

    def checked(self, *arguments, **kwargs):
        result = self.docker(*arguments, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cleanup(self):
        for name in reversed(self.containers):
            self.docker("rm", "--force", "--volumes", name)
        self.docker("network", "rm", self.network)

    def start(self, service, image, environment=None, extra=()):
        name = self.network + "-" + service
        self.containers.append(name)
        command = ["run", "--detach", "--platform", "linux/amd64", "--name", name,
                   "--network", self.network, "--network-alias", service]
        for key, value in (environment or {}).items():
            command.extend(["--env", key + "=" + value])
        self.checked(*command, *extra, image)
        return name

    def ready(self, container, command=None, marker=None):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.checked("inspect", "--format", "{{.State.Running}}", container) != "true":
                logs = self.docker("logs", container)
                self.fail(logs.stdout + logs.stderr)
            result = self.docker("exec", container, *command) if command else self.docker("logs", container)
            if (command and result.returncode == 0) or (marker and marker in result.stdout + result.stderr):
                return
            time.sleep(1)
        self.fail("Service did not become ready: " + container + "\n" + self.docker("logs", container).stderr)

    def test_deployed_schema_drives_reconciliation_and_failure_preserves_release(self):
        for name in ("kafka_load", "postgres_load", "mqtt_load", "bridge_load", "ingest_load"):
            subprocess.run([str(ARTIFACTS[name])], check=True, timeout=180)
        self.network = "lhre-schema-test-" + uuid.uuid4().hex[:10]
        self.containers = []
        self.checked("network", "create", "--internal", self.network)
        self.addCleanup(self.cleanup)
        password = "isolated-schema-test"
        database = self.start("db", "lhre/telemetry-postgres:17.11", {
            "POSTGRES_USER": "electric", "POSTGRES_PASSWORD": password,
        }, extra=("--tmpfs", "/var/lib/postgresql/data"))
        self.ready(database, command=("pg_isready", "-h", "127.0.0.1", "-U", "electric"))
        for database_name, source in (("telemetry", "nightwatch_sql"), ("angelique", "angelique_sql"), ("orion", "sql")):
            self.checked("exec", database, "createdb", "-U", "electric", database_name)
            self.checked("exec", "--interactive", database, "psql", "-v", "ON_ERROR_STOP=1", "-U", "electric", "-d", database_name,
                         input=ARTIFACTS[source].read_text())
        kafka = self.start("kafka", "lhre/telemetry-kafka:4.1.0", {
            "KAFKA_NODE_ID": "1", "KAFKA_PROCESS_ROLES": "broker,controller",
            "KAFKA_LISTENERS": "PLAINTEXT://:9092,CONTROLLER://:9093",
            "KAFKA_ADVERTISED_LISTENERS": "PLAINTEXT://kafka:9092",
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT",
            "KAFKA_INTER_BROKER_LISTENER_NAME": "PLAINTEXT", "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
            "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka:9093", "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
        })
        self.ready(kafka, marker="Kafka Server started")
        mqtt = self.start("mosquitto", "lhre/telemetry-mqtt:2.0.18", extra=(
            "--mount", f"type=bind,src={ARTIFACTS['mosquitto_conf']},dst=/mosquitto/config/mosquitto.conf,readonly",
        ))
        self.ready(mqtt, marker="running")
        bridge = self.start("kafka-bridge", "lhre/telemetry-kafka-bridge:dev", {
            "IN_DOCKER": "1", "KAFKA_BROKER": "kafka:9092", "BRIDGE_ADDR": ":50051",
        })
        self.ready(bridge, marker="gRPC server listening")
        ingest = self.start("ingest", "lhre/telemetry-ingest:dev", {
            "IN_DOCKER": "1", "ELECTRIC_PWD": password, "GRAFANA_PWD": password,
            "ANALYSIS_PWD": password, "LOGLEVEL": "INFO",
        })
        self.ready(ingest, marker="connected to Mosquitto Broker")
        deployment = DockerDeployment((ingest, bridge))
        initial, _ = deployment.snapshot({"releases": []})
        self.assertEqual({item["name"] for item in initial["deployment"]["consumers"]}, {ingest, bridge})
        authority = create_server(lambda: deployment.snapshot({"releases": []}), ("127.0.0.1", 0))
        thread = threading.Thread(target=authority.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="bevo-docker-install-") as temporary:
                root = Path(temporary)
                bootstrap = root / "releases/bootstrap"
                (bootstrap / "bin").mkdir(parents=True)
                files = unpack(ARTIFACTS["metadata"].read_bytes(), METADATA_FILES, 2 * 1024 * 1024)
                files.update(unpack(ARTIFACTS["executables"].read_bytes(), {"bin/" + name for name in EXECUTABLES}, MAX_RELEASE_BYTES))
                for name, data in files.items():
                    (bootstrap / name).write_bytes(data)
                (root / "current").symlink_to("releases/bootstrap")
                url = f"http://127.0.0.1:{authority.server_address[1]}/profile.json"
                command = [sys.executable, "-I", "-S", str(ARTIFACTS["updater"]), "update", "--root", str(root), "--server-url", url]
                updated = subprocess.run(command, capture_output=True, text=True, timeout=120)
                self.assertEqual(updated.returncode, 0, updated.stderr)
                self.assertEqual(json.loads(updated.stdout)["action"], "reconcile")
                active = (root / "current").resolve()
                plan = json.loads((active / "reconciliation.json").read_bytes())
                self.assertEqual(plan["server"]["schema_id"], initial["schema"]["schema_id"])
                for name, data in files.items():
                    self.assertEqual((active / name).read_bytes(), data)
                kept = subprocess.run(command, capture_output=True, text=True, timeout=120)
                self.assertEqual(kept.returncode, 0, kept.stderr)
                self.assertEqual(json.loads(kept.stdout)["action"], "keep")
                self.checked("stop", "--time", "5", bridge)
                with self.assertRaises(urllib.error.HTTPError) as unavailable:
                    fetch(url, 2 * 1024 * 1024)
                self.assertEqual(unavailable.exception.code, 503)
                failed = subprocess.run(command, capture_output=True, text=True, timeout=120)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual((root / "current").resolve(), active)
                self.checked("start", bridge)
                self.ready(bridge, marker="gRPC server listening")
                recovered = subprocess.run(command, capture_output=True, text=True, timeout=120)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(json.loads(recovered.stdout)["action"], "keep")
                print("PASS: actual ingest/bridge schema -> HTTP -> updater reconcile/keep; stopped bridge -> 503 without activation; restart -> recovery")
        finally:
            authority.shutdown()
            authority.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    ARTIFACTS = dict(zip(("ingest_load", "bridge_load", "kafka_load", "postgres_load", "mqtt_load", "metadata", "executables",
                          "sql", "nightwatch_sql", "angelique_sql", "mosquitto_conf", "updater"),
                         (Path(argument).resolve() for argument in sys.argv[1:])))
    unittest.main(argv=[sys.argv[0]])
