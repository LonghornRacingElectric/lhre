import json
import socket
import subprocess
import sys
import time
import unittest
import uuid

from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import KafkaError
from runfiles import Runfiles
from stack.ingest.protobuf import artemis_pb2


LOADERS = sys.argv[1:4]
del sys.argv[1:4]


def update(t_ms, **groups):
    values = artemis_pb2.ArtemisSensorData(time=t_ms)
    paths = []
    for group, fields in groups.items():
        descriptor = values.DESCRIPTOR.fields_by_name[group]
        for field, value in fields.items():
            setattr(getattr(values, group), field, value)
            paths.append(artemis_pb2.FieldPath(
                group=descriptor.number, field=descriptor.message_type.fields_by_name[field].number,
            ))
    return artemis_pb2.TelemetryUpdate(
        sample_time_ns=t_ms * 1_000_000, values=values, updated_fields=paths,
    )


class KafkaIntegrationTest(unittest.TestCase):
    def docker(self, *args):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=45)

    def checked(self, *args):
        result = self.docker(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cleanup(self):
        for container in reversed(self.containers):
            self.docker("rm", "--force", "--volumes", container)
        self.docker("network", "rm", self.network)

    def wait(self, condition, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.2)
        logs = []
        for container in self.containers:
            result = self.docker("logs", "--tail", "40", container)
            logs.append(container + "\n" + result.stdout + result.stderr)
        self.fail("Timed out waiting for Kafka processing:\n" + "\n".join(logs))

    def start(self, service, image, environment, *extra):
        name = self.network + "-" + service
        self.containers.append(name)
        args = ["run", "--detach", "--platform", "linux/amd64", "--name", name,
                "--network", self.network, "--network-alias", service]
        for key, value in environment.items():
            args.extend(["--env", key + "=" + value])
        self.checked(*args, *extra, image)
        return name

    def test_artemis_batches_produce_fault_and_board_events(self):
        files = Runfiles.Create()
        for loader in LOADERS:
            loaded = subprocess.run([files.Rlocation(loader)], capture_output=True, text=True, timeout=180)
            self.assertEqual(loaded.returncode, 0, loaded.stdout + loaded.stderr)
        self.network = "lhre-fault-test-" + uuid.uuid4().hex[:10]
        self.containers = []
        self.addCleanup(self.cleanup)
        self.checked("network", "create", self.network)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        broker = self.start("kafka", "lhre/telemetry-kafka:4.1.0", {
            "KAFKA_NODE_ID": "1", "KAFKA_PROCESS_ROLES": "broker,controller",
            "KAFKA_LISTENERS": "INTERNAL://:9092,EXTERNAL://:29092,CONTROLLER://:9093",
            "KAFKA_ADVERTISED_LISTENERS": f"INTERNAL://kafka:9092,EXTERNAL://127.0.0.1:{port}",
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT",
            "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL",
            "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
            "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka:9093",
            "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
        }, "--publish", f"127.0.0.1:{port}:29092")
        def broker_ready():
            logs = self.docker("logs", broker)
            return "Kafka Server started" in logs.stdout + logs.stderr
        self.wait(broker_ready, timeout=90)
        bootstrap = f"127.0.0.1:{port}"
        admin = KafkaAdminClient(bootstrap_servers=bootstrap)
        self.addCleanup(admin.close)
        admin.create_topics([NewTopic(topic, 1, 1) for topic in
                             ("sensor_data", "fault_events", "board_health")])
        producer = KafkaProducer(bootstrap_servers=bootstrap, acks="all")
        self.addCleanup(producer.close)
        consumer = KafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False)
        self.addCleanup(consumer.close)
        consumer.assign([TopicPartition(topic, 0) for topic in ("fault_events", "board_health")])
        consumer.seek_to_beginning()
        groups = {"faults": "faults-test", "health": "health-test"}
        for service, image in (("faults", "lhre/telemetry-events-faults:dev"),
                               ("health", "lhre/telemetry-board-health:dev")):
            self.start(service, image, {
                "KAFKA_BOOTSTRAP_SERVERS": "kafka:9092", "KAFKA_GROUP_ID": groups[service],
            })
        age_field = next(field.name for field in artemis_pb2.ArtemisSensorData.DESCRIPTOR
                         .fields_by_name["board_status"].message_type.fields
                         if field.name.endswith("_last_seen_s"))
        board = age_field.removesuffix("_last_seen_s")
        headers = [("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1")]
        def send(updates):
            payload = artemis_pb2.TelemetryBatch(format_version=1, updates=updates).SerializeToString()
            return producer.send("sensor_data", value=payload, headers=headers).get(timeout=10)
        def committed(offset):
            try:
                for group in groups.values():
                    position = admin.list_consumer_group_offsets(group).get(TopicPartition("sensor_data", 0))
                    if position is None or position.offset < offset:
                        return False
                return True
            except KafkaError:
                return False
        def readers_ready():
            send([update(int(time.time() * 1000), board_status={age_field: 0},
                         diagnostics_high={"stomp_fault": False, "run_faults": 0})])
            return committed(1)
        self.wait(readers_ready)
        baseline = []
        def initial_board_event():
            for records in consumer.poll(timeout_ms=500).values():
                baseline.extend(json.loads(record.value) for record in records if record.topic == "board_health")
            return any(event["board"] == board and event["state"] == "LIVE" for event in baseline)
        self.wait(initial_board_event)
        t_ms = int(time.time() * 1000)
        sent = send([
            update(t_ms, board_status={age_field: 6}, diagnostics_high={"stomp_fault": True}),
            update(t_ms + 1, controls={"apps1_v": 0}),
            update(t_ms + 2, diagnostics_high={"run_faults": 4}),
            update(t_ms + 3, diagnostics_high={"stomp_fault": False}),
            update(t_ms + 4, board_status={age_field: 0}, diagnostics_high={"run_faults": 0}),
            update(t_ms + 5, board_status={age_field: 0}, diagnostics_high={"run_faults": 0}),
        ])
        events = {"fault_events": [], "board_health": []}
        def transitions_received():
            for records in consumer.poll(timeout_ms=500).values():
                for record in records:
                    events[record.topic].append(json.loads(record.value))
            return (len(events["fault_events"]) >= 4
                    and any(event["cause"] == "telemetry_timeout" for event in events["board_health"]))
        self.wait(transitions_received, timeout=30)
        self.wait(lambda: committed(sent.offset + 1))
        self.assertEqual(events["fault_events"], [
            {"kind": kind, "fault": fault, "car": "artemis", "t_ms": t_ms + delta}
            for kind, fault, delta in (
                ("fault_raised", "stomp_fault", 0), ("fault_raised", "run_faults[2]", 2),
                ("fault_cleared", "stomp_fault", 3), ("fault_cleared", "run_faults[2]", 4),
            )
        ])
        health = events["board_health"]
        self.assertEqual([(event["previous"], event["state"], event["cause"]) for event in health], [
            ("LIVE", "STALE", "frame"), ("STALE", "LIVE", "frame"),
            ("LIVE", "UNKNOWN", "telemetry_timeout"),
        ])
        self.assertTrue(all(event["car"] == "artemis" and event["board"] == board for event in health))
        self.assertEqual([event["t_ms"] for event in health[:2]], [t_ms, t_ms + 4])
        self.assertEqual([event["t_source"] for event in health], ["car", "car", "server"])


if __name__ == "__main__":
    unittest.main()
