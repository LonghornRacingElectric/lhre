"""Database model integration checks."""
import unittest

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



if __name__ == "__main__":
    unittest.main()
