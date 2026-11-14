"""Board communication-state processor.

Decodes Artemis native updates, tracks per-board communication states,
publishes one event per state change, and publishes a snapshot of every
board's state on any change and on a heartbeat. Health-packet interpretation
is future work. See README.md.
"""

import json
import logging
import os
import signal
import time

from kafka import KafkaConsumer, KafkaProducer
from stack.ingest.protobuf import artemis_pb2
from stack.ingest.sensor_decode import decode_updates, payload_format
from google.protobuf.message import DecodeError

from classifier import (BoardStateTracker, SnapshotTimer, change_events,
                        event_time_ms, snapshot_event)

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logging.getLogger("kafka").setLevel(logging.WARNING)

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
INPUT_TOPIC = os.getenv("KAFKA_INPUT_TOPIC", "sensor_data")
OUTPUT_TOPIC = os.getenv("KAFKA_OUTPUT_TOPIC", "board_health")
STATE_TOPIC = os.getenv("KAFKA_STATE_TOPIC", "board_health_state")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "board-health-group")
SENSOR_TYPE = artemis_pb2.ArtemisSensorData
AGE_SUFFIX = "_last_seen_s"
BOARD_FIELDS = {
    field.name.removesuffix(AGE_SUFFIX): field.name
    for field in SENSOR_TYPE.DESCRIPTOR.fields_by_name["board_status"].message_type.fields
    if field.name.endswith(AGE_SUFFIX)
}
CAR = SENSOR_TYPE.DESCRIPTOR.file.package

# 0.5 s is about 4.5x the slowest healthy gap in the UTA autocross recording
# (DUI, 0.111 s). One global value until per-board thresholds come from the spec.
STALE_AFTER_S = float(os.getenv("BOARD_STALE_AFTER_S", "0.5"))
TELEMETRY_TIMEOUT_S = float(os.getenv("BOARD_TELEMETRY_TIMEOUT_S", "10"))
SNAPSHOT_INTERVAL_S = float(os.getenv("BOARD_SNAPSHOT_INTERVAL_S", "2"))

stop = False


def _request_stop(signum, _frame):
    # Docker sends SIGTERM on `stop`; without a handler PID 1 ignores it and the
    # container is killed after the timeout.
    global stop
    stop = True


def _car_type(headers):
    for key, value in headers or []:
        key = key.decode("utf-8") if isinstance(key, bytes) else key
        if key.lower() == "car_type":
            value = value.decode("utf-8") if isinstance(value, bytes) else value
            return value.strip().lower()
    return None


def _server_now_ms():
    return int(time.time() * 1000)


def _publish(producer, changes, board_status, cause, t_ms, t_source):
    for event in change_events(changes, board_status, cause, t_ms, t_source, car=CAR):
        logging.info("board=%s state=%s -> %s (%s)",
                     event["board"], event["previous"], event["state"], cause)
        # Keyed by board so one board's events stay ordered on a multi-partition topic.
        # .get() waits for Kafka to acknowledge the event. A failed send raises and
        # stops the processor before this batch's offsets are committed.
        producer.send(OUTPUT_TOPIC, key=event["board"].encode(), value=event).get(timeout=10)


def _publish_snapshot(producer, states, t_ms, t_source):
    # Keyed by car so snapshots stay ordered on a multi-partition topic.
    snapshot = snapshot_event(states, t_ms, t_source, car=CAR)
    producer.send(STATE_TOPIC, key=CAR.encode(), value=snapshot).get(timeout=10)


def main():
    tracker = BoardStateTracker(STALE_AFTER_S, TELEMETRY_TIMEOUT_S, BOARD_FIELDS)
    snapshots = SnapshotTimer(SNAPSHOT_INTERVAL_S)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=BOOTSTRAP,
        group_id=GROUP_ID,
        enable_auto_commit=False,
        auto_offset_reset="latest",
    )
    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
    )
    logging.info("board_health ready. in=%s out=%s state=%s stale_after=%ss",
                 INPUT_TOPIC, OUTPUT_TOPIC, STATE_TOPIC, STALE_AFTER_S)

    seen = 0
    try:
        while not stop:
            batch = consumer.poll(timeout_ms=1000)
            changed = False
            # Time of the newest update in this poll; server time when none arrived.
            snapshot_time = None
            for records in batch.values():
                for record in records:
                    seen += 1
                    if seen % 100 == 1:  # sensor_data is fast; don't log every frame
                        logging.info("frame #%d: %d bytes, headers=%s", seen, len(record.value), record.headers)

                    car = _car_type(record.headers)
                    if car != CAR:
                        continue
                    try:
                        payload_format(record.headers, "Artemis")
                        updates = decode_updates(record.value)
                    except (DecodeError, ValueError) as error:
                        logging.warning("Invalid Artemis sensor payload: %s", error)
                        continue

                    for update in updates:
                        board_status = update.get("board_status")
                        if not board_status:
                            continue
                        changes = tracker.observe(board_status, time.monotonic())
                        t_ms, t_source = event_time_ms(update["time"], _server_now_ms())
                        _publish(producer, changes, board_status, "frame", t_ms, t_source)
                        changed = changed or bool(changes)
                        snapshot_time = (t_ms, t_source)

            # A dead feed produces no records, so this is the only place a car
            # going silent is noticed. It runs after every poll, empty or not.
            # No frame stands behind a timeout, so it carries server time.
            expired = tracker.expire(time.monotonic())
            _publish(producer, expired, None, "telemetry_timeout", _server_now_ms(), "server")
            changed = changed or bool(expired)

            # One snapshot per poll at most: after any change, and on the heartbeat
            # so a live client always has every board's current state.
            now = time.monotonic()
            if snapshots.due(now, changed):
                t_ms, t_source = snapshot_time or (_server_now_ms(), "server")
                _publish_snapshot(producer, tracker.states, t_ms, t_source)
                snapshots.sent(now)

            # Commit only after every event from this batch is acknowledged, so a
            # crash or failed send replays the batch instead of losing events.
            if batch:
                consumer.commit()
    finally:
        # Timeout events are often the last thing sent before a stop; don't drop them.
        producer.flush()
        producer.close()
        consumer.close(autocommit=False)


if __name__ == "__main__":
    main()
