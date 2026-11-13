"""Watch the decode router pick a decoder (or quarantine) for each record.

    # tail the running stack's sensor_data and route every record
    bazel run --config=local //apps/telemetry/stack/ingest:decode_router_demo -- --from-kafka localhost:29092

    # in another terminal: publish one batch per case through MQTT -> ingest -> bridge
    bazel run --config=local //apps/telemetry/stack/ingest:decode_router_demo -- --send localhost:1883

    # no stack needed: route the same cases plus malformed records in-process
    bazel run --config=local //apps/telemetry/stack/ingest:decode_router_demo -- --samples

Read-only on Kafka: the tail uses no consumer group and commits nothing, so it
cannot affect db_writer or the processors.
"""

import argparse
import hashlib
import os
import sys
import time

from runfiles import Runfiles
from stack.ingest.decode_router import FINGERPRINT_HEADER, DecoderTable, Quarantined, deployed_schema_id, route
from stack.ingest.protobuf import artemis_pb2

UNKNOWN_SCHEMA = hashlib.sha256(b"decode-router-demo: unknown").hexdigest()
UNAPPROVED_SCHEMA = hashlib.sha256(b"decode-router-demo: unapproved").hexdigest()
ARTEMIS_HEADERS = [("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1")]


def default_paths():
    files = Runfiles.Create()
    return (files.Rlocation("_main/lib/codegen/telemetry/artemis_schema_contract.json"),
            files.Rlocation("_main/apps/telemetry/schema_registry/decoders.json"))


def load_table(contract=None, table=None):
    default_contract, default_table = default_paths()
    return DecoderTable.load(table or default_table, deployed_schema_id(contract or default_contract))


def board_batch(t_ms, last_seen_s=0.02):
    """One TelemetryBatch reporting every board as just seen."""
    values = artemis_pb2.ArtemisSensorData(time=t_ms)
    group = values.DESCRIPTOR.fields_by_name["board_status"]
    update = artemis_pb2.TelemetryUpdate(sample_time_ns=t_ms * 1_000_000)
    for field in group.message_type.fields:
        if field.name.endswith("_last_seen_s"):
            setattr(values.board_status, field.name, last_seen_s)
            update.updated_fields.add(group=group.number, field=field.number)
    update.values.CopyFrom(values)
    return artemis_pb2.TelemetryBatch(format_version=1, updates=[update]).SerializeToString()


def cases(deployed, t_ms):
    """(label, MQTT topic suffix or None, headers as the bridge writes them, payload)."""
    payload = board_batch(t_ms)
    return [
        ("deployed schema", None, ARTEMIS_HEADERS + [(FINGERPRINT_HEADER, deployed.encode())], payload),
        ("unknown schema", UNKNOWN_SCHEMA, ARTEMIS_HEADERS + [(FINGERPRINT_HEADER, UNKNOWN_SCHEMA.encode())],
         board_batch(t_ms + 1)),
        ("unapproved schema", UNAPPROVED_SCHEMA, ARTEMIS_HEADERS + [(FINGERPRINT_HEADER, UNAPPROVED_SCHEMA.encode())],
         board_batch(t_ms + 2)),
    ]


def malformed_cases(deployed, t_ms):
    """Records the bridge would never write; only a misbehaving producer could."""
    headers = ARTEMIS_HEADERS + [(FINGERPRINT_HEADER, deployed.encode())]
    return [
        ("bare ArtemisSensorData", headers, artemis_pb2.ArtemisSensorData(time=t_ms).SerializeToString()),
        ("no car_type header", [("payload_format", b"artemis_updates_v1")], board_batch(t_ms)),
        ("wrong payload_format", [("car_type", b"Artemis"), ("payload_format", b"snapshot_v1")], board_batch(t_ms)),
    ]


def describe(headers, payload, table):
    """One line saying what the router did with a record."""
    try:
        result = route(headers, payload, table)
    except Quarantined as q:
        schema = f"  schema {q.schema_id[:12]}" if q.schema_id else ""
        return f"QUARANTINED  {q.reason:<17}{schema}  {q.detail}"
    schema = f"schema {result['schema_id'][:12]}" if result["schema_id"] else "no fingerprint"
    fields = sorted({f"{group}.{name}" for update in result["updates"]
                     for group, values in update.items() if isinstance(values, dict) for name in values})
    shown = ", ".join(fields[:3]) + (f" +{len(fields) - 3} more" if len(fields) > 3 else "")
    return (f"DECODED      {result['car']:<8} {schema} ({result['source']})  "
            f"{len(result['updates'])} update(s): {shown}")


def run_samples(table):
    t_ms = int(time.time() * 1000)
    rows = [(label, headers, payload) for label, _, headers, payload in cases(table.deployed, t_ms)]
    rows += malformed_cases(table.deployed, t_ms)
    rows.append(("no fingerprint (older bridge)", ARTEMIS_HEADERS, board_batch(t_ms)))
    for label, headers, payload in rows:
        print(f"{label:<30} {describe(headers, payload, table)}")


def send(target, table):
    from paho.mqtt import client as mqtt_client

    host, _, port = target.partition(":")
    client = mqtt_client.Client(client_id="decode_router_demo")
    client.connect(host, int(port or 1883))
    client.loop_start()
    try:
        for label, suffix, _, payload in cases(table.deployed, int(time.time() * 1000)):
            topic = "artemis/data" + (f"/{suffix}" if suffix else "")
            client.publish(topic, payload, qos=1).wait_for_publish(timeout=5)
            print(f"sent {label:<18} -> {topic}")
    finally:
        client.loop_stop()
        client.disconnect()


def tail(bootstrap, table, from_beginning=False, limit=None, out=sys.stdout):
    from kafka import KafkaConsumer, TopicPartition

    consumer = KafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False)
    try:
        partitions = [TopicPartition("sensor_data", p) for p in sorted(consumer.partitions_for_topic("sensor_data") or [0])]
        consumer.assign(partitions)
        (consumer.seek_to_beginning if from_beginning else consumer.seek_to_end)(*partitions)
        print(f"routing sensor_data from {bootstrap} (deployed schema {table.deployed[:12]})", file=out, flush=True)
        seen = 0
        while limit is None or seen < limit:
            for records in consumer.poll(timeout_ms=500).values():
                for record in records:
                    print(f"offset {record.offset:<7} {describe(record.headers, record.value, table)}", file=out, flush=True)
                    seen += 1
    finally:
        consumer.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-kafka", metavar="BOOTSTRAP", help="tail sensor_data, e.g. localhost:29092")
    mode.add_argument("--send", metavar="HOST:PORT", help="publish one batch per case over MQTT, e.g. localhost:1883")
    mode.add_argument("--samples", action="store_true", help="route built-in cases in-process, no stack needed")
    parser.add_argument("--from-beginning", action="store_true", help="with --from-kafka: start at the oldest record")
    parser.add_argument("--contract", help="schema_contract.json of the deployed schema (default: this checkout's)")
    parser.add_argument("--table", help="decoder table (default: apps/telemetry/schema_registry/decoders.json)")
    args = parser.parse_args(argv)

    table = load_table(args.contract, args.table)
    if args.samples:
        run_samples(table)
    elif args.send:
        send(args.send, table)
    else:
        try:
            tail(args.from_kafka, table, args.from_beginning)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
