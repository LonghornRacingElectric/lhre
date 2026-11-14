"""The decode router behind the real ingest path, in throwaway containers.

Starts Kafka, Mosquitto, the kafka bridge and ingest from Bazel-built images on
a private Docker network, publishes the demo's cases over MQTT exactly as
`decode_router_demo --send` does, then routes what lands in sensor_data:

- plain artemis/data gets the bridge's deployed schema_id and decodes
- artemis/data/<unknown> and artemis/data/<unapproved> are quarantined
- only the deployed-schema batch reaches the live grafana_data_artemis feed
"""

import io
import json
import socket
import subprocess
import sys
import time
import unittest
import uuid

from kafka import KafkaConsumer, TopicPartition
from runfiles import Runfiles
from stack.ingest import decode_router_demo as demo
from stack.ingest.decode_router import FINGERPRINT_HEADER, Quarantined, route

LOADERS = sys.argv[1:5]
del sys.argv[1:5]
PLATFORM = "linux/amd64"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class DecodeRouterIntegrationTest(unittest.TestCase):
    def docker(self, *args):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)

    def checked(self, *args):
        result = self.docker(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cleanup(self):
        for container in reversed(self.containers):
            self.docker("rm", "--force", "--volumes", container)
        self.docker("network", "rm", self.network)

    def logs(self):
        return "\n".join(f"--- {c}\n" + (lambda r: r.stdout + r.stderr)(self.docker("logs", "--tail", "40", c))
                         for c in self.containers)

    def wait(self, condition, what, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.3)
        self.fail(f"Timed out waiting for {what}:\n{self.logs()}")

    def create(self, service, image, environment=None, *extra):
        name = f"{self.network}-{service}"
        self.containers.append(name)
        args = ["create", "--platform", PLATFORM, "--name", name, "--network", self.network, "--network-alias", service]
        for key, value in (environment or {}).items():
            args += ["--env", f"{key}={value}"]
        self.checked(*args, *extra, image)
        return name

    def logged(self, container, text):
        result = self.docker("logs", container)
        return text in result.stdout + result.stderr

    def test_mqtt_publishes_are_routed_by_schema_fingerprint(self):
        files = Runfiles.Create()
        for loader in LOADERS:
            loaded = subprocess.run([files.Rlocation(loader)], capture_output=True, text=True, timeout=300)
            self.assertEqual(loaded.returncode, 0, loaded.stdout + loaded.stderr)
        table = demo.load_table()

        self.network = "lhre-router-test-" + uuid.uuid4().hex[:10]
        self.containers = []
        self.addCleanup(self.cleanup)
        self.checked("network", "create", self.network)

        kafka_port, mqtt_port = free_port(), free_port()
        kafka = self.create("kafka", "lhre/telemetry-kafka:4.1.0", {
            "KAFKA_NODE_ID": "1", "KAFKA_PROCESS_ROLES": "broker,controller",
            "KAFKA_LISTENERS": "INTERNAL://:9092,EXTERNAL://:29092,CONTROLLER://:9093",
            "KAFKA_ADVERTISED_LISTENERS": f"INTERNAL://kafka:9092,EXTERNAL://127.0.0.1:{kafka_port}",
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT",
            "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL",
            "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
            "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka:9093",
            "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
        }, "--publish", f"127.0.0.1:{kafka_port}:29092")
        self.checked("start", kafka)
        self.wait(lambda: self.logged(kafka, "Kafka Server started"), "Kafka")

        # Same broker config as the stack's compose file.
        mosquitto = self.create("mosquitto", "lhre/telemetry-mqtt:2.0.18", None,
                                "--publish", f"127.0.0.1:{mqtt_port}:1883")
        # -L: the runfile is a symlink, and docker cp would copy the link itself.
        self.checked("cp", "-L", files.Rlocation("_main/apps/telemetry/stack/ingest/mosquitto.conf"),
                     f"{mosquitto}:/mosquitto/config/mosquitto.conf")
        self.checked("start", mosquitto)
        self.wait(lambda: self.logged(mosquitto, "running"), "Mosquitto")

        bridge = self.create("kafka-bridge", "lhre/telemetry-kafka-bridge:dev",
                             {"IN_DOCKER": "1", "KAFKA_BROKER": "kafka:9092", "BRIDGE_ADDR": ":50051"})
        self.checked("start", bridge)
        self.wait(lambda: self.logged(bridge, "gRPC server listening"), "the bridge")

        ingest = self.create("ingest", "lhre/telemetry-ingest:dev",
                             {"IN_DOCKER": "1", "SERVER_TARGET": "LOCAL", "LOGLEVEL": "INFO"})
        self.checked("start", ingest)
        self.wait(lambda: self.logged(ingest, "connected to Mosquitto Broker"), "ingest to connect")

        bootstrap = f"127.0.0.1:{kafka_port}"
        consumer = KafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False)
        self.addCleanup(consumer.close)
        raw, live = TopicPartition("sensor_data", 0), TopicPartition("grafana_data_artemis", 0)
        consumer.assign([raw, live])
        consumer.seek_to_beginning(raw, live)
        records = {"sensor_data": [], "grafana_data_artemis": []}

        def drain():
            for partition, batch in consumer.poll(timeout_ms=200).items():
                records[partition.topic].extend(batch)

        # Ingest subscribes right after connecting; resend until the first batch lands.
        def sent_and_landed():
            demo.send(f"127.0.0.1:{mqtt_port}", table)
            drain()
            return len(records["sensor_data"]) >= 3
        self.wait(sent_and_landed, "the demo batches to reach sensor_data", timeout=60)
        time.sleep(2)  # let the live feed catch up before checking what it got
        drain()

        outcomes = []
        for record in records["sensor_data"]:
            fingerprint = dict(record.headers).get(FINGERPRINT_HEADER, b"").decode()
            try:
                result = route(record.headers, record.value, table)
                outcomes.append((fingerprint, "decoded", result["updates"][0]["time"]))
            except Quarantined as q:
                outcomes.append((fingerprint, q.reason, None))

        # Every send round produced the same three outcomes, in publish order.
        decoded_times = {t for _, verdict, t in outcomes if verdict == "decoded"}
        self.assertTrue(decoded_times, outcomes)
        self.assertEqual({(fp, verdict) for fp, verdict, _ in outcomes}, {
            (table.deployed, "decoded"),
            (demo.UNKNOWN_SCHEMA, "unknown_schema"),
            (demo.UNAPPROVED_SCHEMA, "unapproved_bundle"),
        }, outcomes)

        # The live feed only carries batches in the bridge's own schema. Each
        # round's quarantined batches are stamped 1 and 2 ms after its good one.
        live_times = set()
        for record in records["grafana_data_artemis"]:
            message = json.loads(record.value)
            live_times.add(int(message.get("time", 0)))
        quarantined_times = {t + offset for t in decoded_times for offset in (1, 2)}
        self.assertTrue(decoded_times <= live_times, (decoded_times, live_times))
        self.assertFalse(quarantined_times & live_times, (quarantined_times, live_times))

        # And the demo's own tail prints the same verdicts.
        out = io.StringIO()
        demo.tail(bootstrap, table, from_beginning=True, limit=3, out=out)
        printed = out.getvalue()
        self.assertIn("DECODED", printed)
        self.assertIn("unknown_schema", printed)
        self.assertIn("unapproved_bundle", printed)


if __name__ == "__main__":
    unittest.main()
