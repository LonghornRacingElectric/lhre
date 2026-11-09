"""Forward Artemis MQTT telemetry to the Kafka bridge."""
import datetime
import os
import logging
import json
import sys
import time
import queue
import threading
from pathlib import Path

import grpc
from paho.mqtt import client as mqtt_client

sys.path.append(str(Path(__file__).parents[2]))
from stack.ingest.protobuf import bridge_pb2, bridge_pb2_grpc

net_config_path = "/app/net_configs.json" if os.getenv("IN_DOCKER") else Path(__file__).parents[2] / "net_configs.json"
with open(net_config_path) as file:
    global_target = json.load(file)

class MQTTTarget:
    @staticmethod
    def get():
        aws = os.getenv("AWS_MQTT_IP")
        if aws and aws.strip():
            return aws.strip()
        return 'mosquitto' if os.environ.get("IN_DOCKER") else global_target["TARGETS"][global_target["SERVER_TARGET"]]



class MQTTHandler:
    def __init__(self, name='ingest', target=None, db_sessions=None, on_message=None, cache_enable=False):
        self.target = target
        self.sessions = db_sessions
        self.client = mqtt_client.Client(client_id=name)
        self.client.username_pw_set(name)
        self.client.user_data_set(self)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = on_message if on_message else self.on_message
        self._shutdown = False
        self._subscription = None
        self.bridge_addr = 'kafka-bridge:50051' if os.getenv("IN_DOCKER") else 'localhost:50051'
        self.grpc_channel = self._make_bridge_channel()
        self.bridge_stub = bridge_pb2_grpc.BridgeServiceStub(self.grpc_channel)
        self.bridge_queue = queue.Queue(maxsize=30000)
        self.bridge_worker = threading.Thread(target=self._bridge_worker, daemon=True)
        self.bridge_worker.start()

    @staticmethod
    def on_connect(client: mqtt_client.Client, userdata, flags: dict, rc: int):
        '''
        Function called when MQTT client connects or fails to connect.
        '''
        if rc:
            logging.error(f'Failed to connect to Mosquitto Broker, return code {rc}\n')
        else:
            logging.info(f'\t\t{client._client_id} connected to Mosquitto Broker')
            if userdata._subscription is not None:
                client.subscribe(userdata._subscription)


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


    def subscribe(self, topic: str = 'artemis/data'):
        self._subscription = topic
        self.client.subscribe(topic)
        logging.info(f"Topic: {topic}")
        self.client.loop_forever()


    def publish(self, *args, **kwargs):
        self.client.publish(*args, **kwargs)


    def _shutdown_workers(self):
        self._shutdown = True
        try:
            self.bridge_queue.put_nowait(None)
        except queue.Full:
            pass

    def on_message(self, client, userdata, msg):
        if msg.topic == 'config/flask':
            self._flask_handler(msg.payload.decode())
        elif msg.topic == 'config/car':
            os.environ['RTC_START'] = str(datetime.datetime.strptime(msg.payload.decode(), "%Y-%m-%dT%H:%M:%S.%f").timestamp() * 1000)
        elif msg.topic == 'artemis/data':
            self._send_to_bridge(msg.payload)
        else:
            logging.warning("Unsupported telemetry topic: %s", msg.topic)

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


    def _send_to_bridge(self, payload):
        try:
            self.bridge_queue.put_nowait(payload)
        except queue.Full:
            logging.error("Bridge queue full; dropping Artemis telemetry")

    def _bridge_worker(self):
        """Forward batches in order, reconnecting and retrying uncertain sends."""

        while True:
            item = self.bridge_queue.get()
            if item is None:
                self.bridge_queue.task_done()
                break
            payload = item
            try:
                while not self._shutdown:
                    try:
                        request = bridge_pb2.SensorDataRequest(payload=payload, car_type="Artemis", payload_format="artemis_updates_v1")
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


    def _proto_decode(self, payload, car):
        from stack.ingest.sensor_decode import decode_sensor
        return decode_sensor(payload, car)


def main():
    with MQTTHandler('ingest', target=MQTTTarget.get()) as mqtt:
        mqtt.subscribe(topic='#')


if __name__ == '__main__':
    logging.basicConfig(level=os.getenv('LOGLEVEL', 'DEBUG'))
    logging.getLogger("grpc").setLevel(logging.WARNING)
    main()
