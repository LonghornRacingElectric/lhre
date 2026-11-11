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
import os
import copy
from unittest.mock import patch
from sqlalchemy import create_engine, MetaData, Table, select, insert, delete
from paho.mqtt.publish import single
from stack.ingest.protobuf import artemis_pb2
from lib.codegen.telemetry.storage import update_sql
from apps.telemetry.schema_registry.catalog import Catalog, atomic_json
from apps.telemetry.schema_registry.bundle import FILES
from apps.telemetry.schema_registry.prepare import prepare
from apps.telemetry.schema_registry.server import storage_ready

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
        for name in ("kafka_load", "postgres_load", "mqtt_load", "bridge_load", "ingest_load", "writer_load"):
            subprocess.run([str(ARTIFACTS[name])], check=True, timeout=180)
        self.network = "lhre-schema-test-" + uuid.uuid4().hex[:10]
        self.containers = []
        self.checked("network", "create", self.network)
        self.addCleanup(self.cleanup)
        password = "isolated-schema-test"
        database = self.start("db", "lhre/telemetry-postgres:2.31.0-pg17", {
            "POSTGRES_USER": "electric", "POSTGRES_PASSWORD": password,
        }, extra=("--tmpfs", "/var/lib/postgresql/data", "--publish", "127.0.0.1::5432"))
        self.ready(database, command=("pg_isready", "-h", "127.0.0.1", "-U", "electric"))
        storage = json.loads(ARTIFACTS["storage"].read_bytes())
        baseline = copy.deepcopy(storage)
        pool = next(pool for pool in baseline["pools"] if pool["table"] == "controls_333hz")
        pool["fields"] = [field for field in pool["fields"] if field["name"] != "enable"]
        baseline["storage_id"] = "fixture-before-enable"
        for database_name, sql in (("telemetry", ARTIFACTS["metadata_sql"].read_text()), ("artemis", update_sql(baseline))):
            self.checked("exec", database, "createdb", "-U", "electric", database_name)
            self.checked("exec", "--interactive", database, "psql", "-v", "ON_ERROR_STOP=1", "-U", "electric", "-d", database_name,
                         input=sql)
        port = json.loads(self.checked("inspect", database))[0]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        database_url = f"postgresql+psycopg2://electric:{password}@127.0.0.1:{port}/artemis"
        engine = create_engine(database_url)
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            sessions = Table("recording_session", MetaData(), schema="public", autoload_with=connection)
            raw = Table("controls_333hz", MetaData(), schema="public", autoload_with=connection)
            connection.execute(insert(sessions).values(session_start_ms=1000, start_time=1000, end_time=1000))
            connection.execute(insert(raw).values(session_start_ms=1000, time=1000, sample_time_ns=1000000000, apps1_travel=12))
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
            "--publish", "127.0.0.1::1883",
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
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        catalog = Catalog({"cache": cache.name})
        reference = catalog.repository + "@sha256:" + "a" * 64
        directory = catalog.directory(reference)
        directory.mkdir()
        bundle = unpack(ARTIFACTS["bundle"].read_bytes(), FILES, 8 * 1024 * 1024)
        release = json.loads(bundle["release.json"])
        release["revision"] = "a" * 40
        bundle["release.json"] = json.dumps(release).encode()
        for name, value in bundle.items():
            (directory / name).write_bytes(value)
        atomic_json(Path(cache.name) / "index.json", {"releases": [reference]})
        before = DockerDeployment((ingest, bridge))
        with patch.dict(os.environ, ARTEMIS_DATABASE_URL=database_url):
            result = prepare(catalog, reference, ARTIFACTS["storage"].parent, before)
            self.assertIn("Add nullable controls_333hz.enable", result["changes"])
            self.assertEqual(prepare(catalog, reference, ARTIFACTS["storage"].parent, before)["changes"], [])
        with engine.connect() as connection:
            raw = Table("controls_333hz", MetaData(), schema="public", autoload_with=connection)
            old = connection.execute(select(raw.c.apps1_travel, raw.c.enable)).one()
            self.assertEqual(tuple(old), (12, None))
        writer = self.start("db-writer", "lhre/telemetry-db-writer:dev", {
            "IN_DOCKER": "1", "ELECTRIC_PWD": password, "GRAFANA_PWD": password, "ANALYSIS_PWD": password,
        })
        self.ready(writer, marker="Consuming sensor_data")
        stamp = int(time.time() * 1000)
        values = artemis_pb2.ArtemisSensorData(time=stamp)
        values.controls.apps1_travel = 0
        values.controls.enable = False
        group = values.DESCRIPTOR.fields_by_name["controls"]
        payload = artemis_pb2.TelemetryBatch(format_version=1, updates=[artemis_pb2.TelemetryUpdate(
            sample_time_ns=stamp * 1000000, values=values, updated_fields=[artemis_pb2.FieldPath(
                group=group.number, field=group.message_type.fields_by_name[name].number) for name in ("apps1_travel", "enable")])]).SerializeToString()
        mqtt_port = json.loads(self.checked("inspect", mqtt))[0]["NetworkSettings"]["Ports"]["1883/tcp"][0]["HostPort"]
        single("artemis/data", payload, qos=1, hostname="127.0.0.1", port=int(mqtt_port))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                observed = connection.execute(select(raw.c.apps1_travel, raw.c.enable).where(raw.c.time == stamp)).first()
            if observed:
                break
            time.sleep(.2)
        self.assertIsNotNone(observed, self.docker("logs", writer).stderr)
        self.assertEqual(tuple(observed), (0, False))
        with engine.begin() as connection:
            connection.execute(delete(raw))
            connection.execute(delete(sessions))
        tested = subprocess.run([str(ARTIFACTS["pool_test"])], env=dict(os.environ, POOL_TEST_DATABASE_URL=database_url),
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
        deployment = DockerDeployment((ingest, bridge), network=self.network)
        initial, _ = deployment.snapshot({"releases": []})
        self.assertEqual({item["name"] for item in initial["deployment"]["consumers"]}, {ingest, bridge, writer})
        def provider():
            profile, descriptor = deployment.snapshot({"releases": []})
            storage_ready(profile["storage_id"], database_url)
            return catalog.augment(profile), descriptor
        authority = create_server(provider, ("127.0.0.1", 0))
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
                with engine.begin() as connection:
                    migrations = Table("storage_migration", MetaData(), schema="public", autoload_with=connection)
                    applied = dict(connection.execute(select(migrations).where(
                        migrations.c.storage_id == storage["storage_id"])).mappings().one())
                    connection.execute(delete(migrations).where(migrations.c.storage_id == storage["storage_id"]))
                try:
                    unavailable = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    self.assertNotEqual(unavailable.returncode, 0)
                    self.assertEqual((root / "current").resolve(), active)
                finally:
                    with engine.begin() as connection:
                        connection.execute(insert(migrations).values(**applied))
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
                          "metadata_sql", "mosquitto_conf", "updater", "writer_load", "bundle", "storage", "pool_test"),
                         (Path(argument).resolve() for argument in sys.argv[1:])))
    unittest.main(argv=[sys.argv[0]])
