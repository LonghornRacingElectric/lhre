"""Write Orion sensor_data protobuf frames as schema-ordered CSV rows."""
import csv
import datetime
import json
import logging
import os
from pathlib import Path
import signal

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from kafka import KafkaConsumer
from stack.ingest.protobuf import can_packets_pb2


def main():
    logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
    stopped = False

    def stop(*_args):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    message = can_packets_pb2.OrionSensorData()
    columns = []
    for field in message.DESCRIPTOR.fields:
        if field.message_type:
            columns.extend((field.name, child.name) for child in field.message_type.fields)
        else:
            columns.append(("packet", field.name))
    directory = Path(os.getenv("LOG_BASE_DIR", "/app/logs")) / "orion"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"orion_log_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}.csv"
    consumer = KafkaConsumer(
        os.getenv("KAFKA_INPUT_TOPIC", "sensor_data"),
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092").split(","),
        group_id=os.getenv("KAFKA_GROUP_ID", "telemetry-csv-logger-v1"),
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=500,
    )
    try:
        with path.open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(f"{group}.{field}" for group, field in columns)
            logging.info("Writing Orion CSV logs to %s", path)
            while not stopped:
                batch = consumer.poll(timeout_ms=1000)
                if not batch:
                    continue
                for records in batch.values():
                    for record in records:
                        if dict(record.headers or []).get("car_type") != b"Orion":
                            continue
                        # A bad frame would otherwise crash-loop the logger on
                        # replay, opening a fresh CSV every restart.
                        try:
                            message.ParseFromString(record.value)
                        except DecodeError:
                            logging.warning("Skipping undecodable frame at offset %d", record.offset)
                            continue
                        decoded = MessageToDict(message, preserving_proto_field_name=True,
                                                always_print_fields_with_no_presence=True)
                        row = []
                        for group, field in columns:
                            source = decoded if group == "packet" else decoded.get(group, {})
                            value = source.get(field, "")
                            row.append(json.dumps(value) if isinstance(value, (list, dict)) else value)
                        writer.writerow(row)
                output.flush()
                consumer.commit()
    finally:
        consumer.close(autocommit=False)


if __name__ == "__main__":
    main()
