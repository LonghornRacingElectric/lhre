"""Database model integration checks."""
import unittest

from sqlalchemy import select, func

from telemetry.analysis.sql_utils.db_session import get_db
from telemetry.analysis.sql_utils.models import ArtemisControls333hz
from telemetry.stack.tests.test_utils import TelemetryConfig


class TestBevoAndDatabaseIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = TelemetryConfig.from_env()

    def test_artemis_pool_query(self):
        with get_db("Artemis") as session:
            self.assertIsInstance(session.scalar(select(func.count()).select_from(ArtemisControls333hz)), int)



if __name__ == "__main__":
    unittest.main()
