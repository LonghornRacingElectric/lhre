import datetime
import os
import logging
import json
import csv
import sys
import time
import queue
import threading
import numpy as np
import copy
import grpc
from paho.mqtt import client as mqtt_client
from google.protobuf.json_format import MessageToDict
from pathlib import Path
from sqlalchemy import text, select

sys.path.append(str(Path(__file__).parents[2]))

from analysis.sql_utils.db_session import get_db, engine_orion
from analysis.sql_utils.query_builder import QueryBuilder
from analysis.sql_utils.models import OrionPacket, ArtemisTransportState, BaseOrion, ORION_MODELS
from stack.ingest.partition_manager import PartitionManager
from stack.ingest.pool_storage import write_gps
from stack.ingest.sensor_decode import decode_sensor, CAR_MESSAGES
from stack.ingest.packet_ids import retained_packet_high_water
from stack.ingest.protobuf import can_packets_pb2, bridge_pb2, bridge_pb2_grpc

# Determine path to net_configs.json based on execution context
if os.getenv("IN_DOCKER"):
    # Inside docker, path is absolute from the /app directory
    net_config_path = "/app/net_configs.json"
else:
    # For local execution, construct path relative to this file's location
    net_config_path = Path(__file__).parents[2] / "net_configs.json"

with open(net_config_path, "r") as file:
    global_target = json.load(file)

class MQTTTarget:
    @staticmethod
    def get():
        aws = os.getenv("AWS_MQTT_IP")
        if aws and aws.strip():
            return aws.strip()
        return 'mosquitto' if os.environ.get("IN_DOCKER") else global_target["TARGETS"][global_target["SERVER_TARGET"]]


class MQTTHandler:
    '''
    This class handles MQTT payloads: connecting to MQTT broker and publishing or subscribing to topics
    '''

    def __init__(self, name='ingest', target=None, db_sessions=None, on_message=None, cache_enable = False):
        '''
        :param name:    str         determining name of client to self-report to MQTT broker
        :param target:  MQTTTarget  MQTT target server
        '''
        self.target = target
        self.sessions = db_sessions
        self.client = mqtt_client.Client(client_id = name)
        # logging state for Orion csv output
        self.orion_log_path: Path | None = None
        self.orion_cols: list[str] = []
        self.orion_csv_config = None
        self.client.username_pw_set(name)
        self.client.user_data_set(self)  # Pass self to callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = on_message if on_message else self.on_message
        self.scalar_or_list = lambda val, scalar: val.tolist()[0] if scalar else val.tolist()
        self.cache = []
        self.cache_enable = cache_enable
        self._shutdown = False
        self.partition_manager = PartitionManager(gap_seconds=300)
        self.partition_enabled_cars = {"Orion"}
        self.table_specs = {car: QueryBuilder(car).get_table_column_specs() for car in CAR_MESSAGES}

        # Non-blocking producer/consumer queues.
        # MQTT callback enqueues jobs, workers perform blocking DB and CSV IO.
        self.db_queues = {car: queue.Queue(maxsize=30000) for car in CAR_MESSAGES}
        self.db_workers = {
            car: threading.Thread(target=self._db_worker, args=(car,), daemon=True)
            for car in self.db_queues
        }
        for worker in self.db_workers.values():
            worker.start()

        # Single CSV worker preserves append order for Orion log output.
        self.csv_queue = queue.Queue(maxsize=15000)
        self.csv_worker = threading.Thread(target=self._csv_worker, daemon=True)
        self.csv_worker.start()
        
        # gRPC bridge client instead of Kafka. A single startup channel used to
        # wedge silently after an MQTT reconnect (DB kept flowing, realtime feed
        # stalled), so the channel now uses keepalive and is recreated on error by
        # a dedicated worker thread. Sends are queued off the paho MQTT loop so a
        # bad channel can never block that thread (which previously tripped the
        # keepalive timeout -> rc16 disconnects).
        self.bridge_addr = 'kafka-bridge:50051' if os.getenv("IN_DOCKER") else 'localhost:50051'
        self.grpc_channel = self._make_bridge_channel()
        self.bridge_stub = bridge_pb2_grpc.BridgeServiceStub(self.grpc_channel)
        self.bridge_queue = queue.Queue(maxsize=30000)
        self.bridge_worker = threading.Thread(target=self._bridge_worker, daemon=True)
        self.bridge_worker.start()
        # Kafka scans and DB lookups belong to the control plane, off paho's loop.
        self.control_queue = queue.Queue(maxsize=32)
        self.control_pending = set()
        self.control_lock = threading.Lock()
        self.control_worker = threading.Thread(target=self._control_worker, daemon=True)
        self.control_worker.start()

    @staticmethod
    def on_connect(client: mqtt_client.Client, userdata, flags: dict, rc: int):
        '''
        Function called when MQTT client connects or fails to connect.
        '''
        if rc:
            logging.error(f'Failed to connect to Mosquitto Broker, return code {rc}\n')
        else:
            logging.info(f'\t\t{client._client_id} connected to Mosquitto Broker')
            # Subscribe to client connection announcements
            userdata.client.subscribe('client-connections')

    @staticmethod
    def _model_key(table_name: str) -> str:
        return ''.join(part.capitalize() for part in table_name.split('_'))

    @staticmethod
    def on_disconnect(client: mqtt_client.Client, userdata, rc: int):
        '''
        Function called when MQTT client disconnects.
        '''
        if rc != 0:
            logging.error(f'Unexpected MQTT disconnection. Return code: {rc}')

    def connect(self, ip=None):
        '''
        Connect to the MQTT broker. Priority in this order: argument --> class variable --> local.

        :param ip:      str indicating IP of MQTT broker

        :return:        mqtt_client.Client object
        '''
        self.client.connect(ip if ip else self.target if self.target else 'mosquitto' if os.getenv('IN_DOCKER') else 'localhost')
        return self.client

    def disconnect(self):
        self.client.disconnect()

    def __enter__(self):
        '''
        Enables 'with MQTTHandler(<name>, MQTTTarget.LOCAL) as mqtt:' logic which auto disconnects created client
        irrespective of errors. Class target preferred to local
        '''
        self.client.connect(self.target if self.target else 'mosquitto' if os.getenv('IN_DOCKER') else 'localhost')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._shutdown_workers()
        self.client.disconnect()

    def subscribe(self, topic: str = '#'):
        self.client.subscribe(topic)
        logging.info(f"Topic: {topic}")
        self.client.loop_forever()

    def publish(self, *args, **kwargs):
        self.client.publish(*args, **kwargs)

    def _shutdown_workers(self):
        if self._shutdown:
            return
        self._shutdown = True

        for car in self.db_queues:
            try:
                self.db_queues[car].put_nowait(None)
            except queue.Full:
                pass
        try:
            self.csv_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.bridge_queue.put_nowait(None)
        except queue.Full:
            pass

    def _db_worker(self, car: str):
        jobs = []
        pending_acks = 0

        def flush_batch(session_obj, timeout_flush: bool = False):
            nonlocal jobs
            if not jobs:
                return

            try:
                # Tier 1 throughput: don't block this commit on a WAL fsync. Durability tradeoff is
                # acceptable — car-side CSV is the source of truth and gaps are backfillable.
                session_obj.execute(text("SET LOCAL synchronous_commit = off"))
                if car == "Artemis":
                    gps = [row for table, row in jobs if table == "gps_precise"]
                    if len(gps) != len(jobs):
                        raise ValueError("Orion measurements must enter through the Kafka pool writer")
                    write_gps(session_obj, gps)
                else:
                    QueryBuilder.execute_insert(session_obj, jobs, self.table_specs[car], car, commit=False)

                packet_times = sorted({
                    int(row["time"])
                    for table, row in jobs
                    if table == "packet" and isinstance(row, dict) and row.get("time") is not None
                })
                if car in self.partition_enabled_cars and packet_times:
                    # Create partitions only at boundaries/gaps; avoid per-packet updates.
                    prev_time = packet_times[0]
                    self.partition_manager.record_packet_time(session_obj, prev_time)

                    for packet_time in packet_times[1:]:
                        if packet_time - prev_time > self.partition_manager.partition_gap_ms:
                            self.partition_manager.record_packet_time(session_obj, packet_time)
                        prev_time = packet_time

                    # On timeout flush, explicitly advance end_time to the latest packet in this batch.
                    if timeout_flush:
                        self.partition_manager.set_latest_end_time(session_obj, packet_times[-1])
                    else:
                        self.partition_manager.set_latest_end_time(session_obj, packet_times[-1])

                session_obj.commit()
            except Exception as e:
                session_obj.rollback()
                logging.error(f"DB worker batch insert failed for {car}: {e}")
            finally:
                jobs.clear()

        with get_db(car) as session:
            while True:
                try:
                    job = self.db_queues[car].get(block=True, timeout=5)
                except queue.Empty:
                    if jobs:
                        flush_batch(session, timeout_flush=True)
                        while pending_acks > 0:
                            self.db_queues[car].task_done()
                            pending_acks -= 1
                    continue

                if job is None:
                    if jobs:
                        flush_batch(session)
                    while pending_acks > 0:
                        self.db_queues[car].task_done()
                        pending_acks -= 1
                    self.db_queues[car].task_done()
                    break

                if not isinstance(job, list):
                    logging.error(f"DB worker for {car} received malformed job type: {type(job)}")
                    self.db_queues[car].task_done()
                    continue

                jobs.extend(job)
                pending_acks += 1

                # Tier 1 throughput: larger batches mean far fewer commits. Each packet contributes
                # ~8 job-tuples, so ~1000 jobs ≈ 125 packets/commit (~1-2s of data at live rates).
                # The 5s queue-timeout flush above bounds worst-case latency for low-rate streams.
                # Single FIFO worker + ordered jobs list => packet_ids still insert strictly in order.
                if len(jobs) >= 1000:
                    flush_batch(session)
                    while pending_acks > 0:
                        self.db_queues[car].task_done()
                        pending_acks -= 1

    def _csv_worker(self):
        while True:
            item = self.csv_queue.get()
            if item is None:
                self.csv_queue.task_done()
                break

            try:
                payload, car, file_path, columns = item
                self._write_proto_csv(payload, car, file_path, columns)
            except Exception as e:
                logging.error(f"CSV worker write failed: {e}")
            finally:
                self.csv_queue.task_done()

    def on_message(self, client: mqtt_client.Client, userdata, msg):
        if msg.topic == 'client-connections':
            with self.control_lock:
                if msg.payload in self.control_pending:
                    return
                try:
                    self.control_queue.put_nowait(msg)
                    self.control_pending.add(msg.payload)
                except queue.Full:
                    logging.error("Control queue full; client must retry welcome request")
            return
        if msg.topic == 'server-communication':
            logging.debug('Ignoring server-communication control message on ingest subscriber')
            return
        # Handle Start & End Event
        if msg.topic == 'config/flask':
            self._flask_handler(msg.payload.decode())
        # Handle Time Handshake
        elif msg.topic == 'config/car':
            os.environ['RTC_START'] = str(datetime.datetime.strptime(msg.payload.decode(), "%Y-%m-%dT%H:%M:%S.%f").timestamp() * 1000)
        elif msg.topic in ("orion/gps", "artemis/gps"):
            self._gps_ingest(msg.payload, car=msg.topic.split("/")[0].capitalize())
        elif msg.topic == "data" or msg.topic.split("/")[0] in ("orion", "artemis"):
            car = "Orion" if msg.topic == "data" else msg.topic.split("/")[0].capitalize()
            self._send_to_bridge(msg.payload, car)
            self._proto_ingest(msg.payload, car=car)
        else:
            logging.warning("Unsupported telemetry topic: %s", msg.topic)

    def _control_worker(self):
        while not self._shutdown:
            try:
                msg = self.control_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._client_connected(msg)
            except Exception:
                logging.exception("Welcome lookup failed; waiting for client retry")
            finally:
                with self.control_lock:
                    self.control_pending.discard(msg.payload)
                self.control_queue.task_done()

    def _client_connected(self, msg):
        identity = msg.payload.decode().strip().replace("_", "-").lower()
        car = {"bevo-orion": "Orion", "bevo-artemis": "Artemis"}.get(identity)
        if car is None:
            return
        with get_db(car) as session:
            if car == "Orion":
                latest = session.scalar(select(OrionPacket.packet_id).order_by(OrionPacket.packet_id.desc()).limit(1)) or 0
            else:
                latest = session.scalar(select(ArtemisTransportState.last_packet_id).where(ArtemisTransportState.singleton)) or 0
        self.bridge_queue.join()
        next_id = retained_packet_high_water(car, int(latest)) + 1
        self.client.publish("server-communication", json.dumps({"packet_id": next_id, "car_type": car}))
        if car == "Orion":
            log_base = Path(os.getenv("LOG_BASE_DIR", str(Path(__file__).parents[2] / "logs"))) / "orion"
            log_base.mkdir(parents=True, exist_ok=True)
            filename = log_base / f"orion_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv"
            descriptor = can_packets_pb2.OrionSensorData.DESCRIPTOR
            columns = [field.name for field in descriptor.fields if field.message_type is None]
            columns += [f"{group.name}.{field.name}" for group in descriptor.fields if group.message_type for field in group.message_type.fields]
            with filename.open("w", newline="") as output:
                csv.writer(output).writerow(columns)
            self.orion_csv_config = (filename, tuple(columns))

    def _gps_ingest(self, payload: bytes, car="Orion"):
        '''
        publishd's GPS-only feed (orion/gps): one small JSON object per receiver fix at up to
        PUBLISHD_GPS_HZ, beside the full frames on 'orion'. Queued for the gps_precise table only:
        the bridge decodes only car frames, and the live map reads gps_precise from those. No packet
        row: the full-frame tables keep their own rate. Values are typed here because the row shares
        a transaction with packet rows, and one bad value would roll the whole batch back.
        '''
        spec = self.table_specs[car]["gps_precise"]
        try:
            row = {k: int(v) if k in ("time", "packet_id", "gps_src") else float(v)
                   for k, v in json.loads(payload).items() if k in spec and k != "id" and v is not None}
            if not (all(-2**63 <= row.get(k, 0) < 2**63 for k in ("time", "packet_id"))
                    and -2**15 <= row.get("gps_src", 0) < 2**15):
                raise ValueError("integer out of column range")
        except (AttributeError, TypeError, ValueError, OverflowError):
            logging.warning(f'orion/gps payload is not a JSON object of in-range numbers; dropped: {payload[:200]!r}')
            return
        if "time" not in row:
            logging.warning('orion/gps payload has no time; dropped')
            return
        try:
            self.db_queues[car].put_nowait([("gps_precise", row)])
        except queue.Full:
            logging.error('Orion DB queue full; dropping gps_precise row')

    def _make_bridge_channel(self):
        '''
        Create a keepalive-enabled gRPC channel to the bridge. Keepalive keeps an
        idle channel healthy; the worker recreates the channel on RpcError so a
        dropped connection self-heals instead of silently stalling the realtime feed.
        '''
        return grpc.insecure_channel(self.bridge_addr, options=[
            ('grpc.keepalive_time_ms', 10000),
            ('grpc.keepalive_timeout_ms', 5000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.max_reconnect_backoff_ms', 5000),
        ])

    def _send_to_bridge(self, payload: bytes, car: str):
        '''
        Hand the protobuf payload to the bridge-forward worker. Non-blocking: this
        runs on the paho MQTT thread, so it must never do network I/O — a wedged
        channel here previously stalled forwarding AND tripped MQTT keepalive (rc16).

        :param payload:     bytes       protobuf encoded payload
        :param car:         str         car type ("Orion" / "Artemis")
        '''
        try:
            self.bridge_queue.put_nowait((payload, car))
        except queue.Full:
            logging.error(f'Bridge queue full; dropping realtime forward for {car}')

    def _bridge_worker(self):
        '''
        Dedicated thread that drains the bridge queue and forwards to the Go bridge
        over gRPC. On RpcError it recreates the channel/stub so the realtime path
        self-heals (the prior fire-and-forget futures wedged silently on reconnect,
        leaving Grafana with no data while DB ingest kept flowing).
        '''
        while True:
            item = self.bridge_queue.get()
            if item is None:
                self.bridge_queue.task_done()
                break
            payload, car = item
            try:
                while not self._shutdown:
                    try:
                        request = bridge_pb2.SensorDataRequest(payload=payload, car_type=car)
                        response = self.bridge_stub.SendSensorData(request, timeout=10)
                        if not response.success:
                            raise RuntimeError(response.message)
                        break
                    except Exception:
                        logging.exception("Raw Kafka forward failed; retaining packet for retry")
                        self.grpc_channel.close()
                        self.grpc_channel = self._make_bridge_channel()
                        self.bridge_stub = bridge_pb2_grpc.BridgeServiceStub(self.grpc_channel)
                        time.sleep(1)
            finally:
                self.bridge_queue.task_done()

    def _flask_handler(self, payload):
        '''
        This function oversees the decoding and handling of Flask messages usually related to configuration or metadata.

        :param payload:     str         payload string containing configuration information
        '''
        try:
            event_id = json.loads(payload)['event_id']
            logging.info(f'\tNow logging data for event: {event_id}...')
            os.environ['EVENT_ID'] = str(event_id)
        except json.JSONDecodeError:
            if payload == 'end_event':
                event_id = os.environ['EVENT_ID']
                del os.environ['EVENT_ID']
                try:
                    del os.environ['RTC_START']
                    logging.info(f'\tEnding logging for event {event_id}...')
                except KeyError:
                    logging.info(f'\tEnding logging for event {event_id} despite RTC_START never being set...')
            else:
                logging.error(f'\tUnexpected payload received: {payload}')

    def cache_flush(self, car):
        if len(self.cache) > 0:
            try:
                self.db_queues[car].put_nowait(list(self.cache))
                self.cache.clear()
            except queue.Full:
                logging.error(f"DB queue full for {car}; dropping cached batch")

    
    def _proto_ingest(self, payload: bytes, cache_enable=False, car="Orion"):
        # Protobuf persistence is exclusively owned by db_writer. CSV is best effort.
        config = self.orion_csv_config
        if car == "Orion" and config is not None:
            try:
                self.csv_queue.put_nowait((payload, car, *config))
            except queue.Full:
                logging.error("CSV queue full for Orion log; dropping row")

    def _write_proto_csv(self, payload, car, file_path, columns):
        message_dict = self._proto_decode(payload=payload, car=car)

        if car == "Orion":
            flat = {key: value for key, value in message_dict.items() if not isinstance(value, dict)}
            for group, values in message_dict.items():
                if isinstance(values, dict):
                    flat.update({f"{group}.{name}": value for name, value in values.items()})
            row_vals = []
            for col in columns:
                val = flat.get(col, "")
                if isinstance(val, (list, dict)):
                    val = json.dumps(val)
                row_vals.append(val)
            with open(file_path, "a", newline="") as output:
                csv.writer(output).writerow(row_vals)


    def _proto_decode(self, payload: str, car = "Orion") -> dict:
        # logging.debug('Data Received via Protobuf')
        return decode_sensor(payload, car)




def reconcile_orm_schema():
    # Create any ORM-mapped tables that don't yet exist in their target DB.
    # Why: orion_db_init.sql only runs on first volume init, so existing volumes
    # miss tables added after their initial bring-up (e.g. board_status).
    for name, base, engine in (("Orion", BaseOrion, engine_orion),):
        try:
            base.metadata.create_all(engine, checkfirst=True)
        except Exception as exc:
            logging.warning("Schema reconcile for %s skipped: %s", name, exc)


def main():
    '''
    This is the runner script for the subscribe-side MQTT script which uploads data to the database.
    '''

    reconcile_orm_schema()

    with get_db("Orion") as orion_session, get_db("Artemis") as artemis_session:
        db_sessions = {"Orion": orion_session, "Artemis": artemis_session}
        with MQTTHandler('ingest', db_sessions=db_sessions, target=MQTTTarget.get()) as mqtt:
            mqtt.subscribe(topic='#')

if __name__ == '__main__':
    logging.basicConfig(level=os.getenv('LOGLEVEL', 'DEBUG'))
    logging.getLogger("grpc").setLevel(logging.WARNING)

    if logging.root.level == logging.DEBUG:
        time.sleep(3)
        logging.debug('-' * 40 + '\n\n\t\tYOU ARE IN DEBUGGING MODE\n\n ' + '-' * 50)
        os.environ['RTC_START'] = "-99999"
        os.environ['EVENT_ID'] = "-99999"
    main()
