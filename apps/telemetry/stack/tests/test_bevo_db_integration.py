"""Supported-car database and BEVO control-plane integration checks."""
import json
import time
import unittest

from paho.mqtt import client as mqtt_client
from sqlalchemy import select, func

from telemetry.analysis.sql_utils.db_session import get_db
from telemetry.analysis.sql_utils.models import OrionPacket, ArtemisControls333hz
from telemetry.stack.tests.test_utils import TelemetryConfig


class TestBevoAndDatabaseIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = TelemetryConfig.from_env()

    def test_supported_car_models(self):
        for car, model in (("Orion", OrionPacket), ("Artemis", ArtemisControls333hz)):
            with self.subTest(car=car), get_db(car) as session:
                self.assertIsInstance(session.scalar(select(func.count()).select_from(model)), int)

    def test_orion_welcome_preserves_packet_protocol(self):
        received = []
        client = mqtt_client.Client(client_id="orion-welcome-integration")
        def connected(client, userdata, flags, rc):
            client.subscribe("server-communication")
        def message(client, userdata, msg):
            payload = json.loads(msg.payload)
            if payload.get("car_type") == "Orion":
                received.append(payload)
        client.on_connect = connected
        client.on_message = message
        client.connect(self.config.mqtt_host, self.config.mqtt_port, 60)
        client.loop_start()
        try:
            deadline = time.monotonic() + 30
            while not received and time.monotonic() < deadline:
                client.publish("client-connections", "BEVO-Orion", qos=1)
                time.sleep(0.5)
            self.assertTrue(received, "Orion welcome response was not received")
            self.assertGreater(received[0]["packet_id"], 0)
        finally:
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    unittest.main()
