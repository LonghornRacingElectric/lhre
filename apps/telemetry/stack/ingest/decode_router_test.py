"""Routes sensor_data records the way they arrive in production.

Uses this checkout's real deployed schema_id and the checked-in decoder table,
and builds each record with the headers the bridge writes. No Docker.
"""

import time
import unittest

from stack.ingest import decode_router_demo as demo
from stack.ingest.decode_router import FINGERPRINT_HEADER, DecoderTable, Quarantined, route
from stack.ingest.protobuf import can_packets_pb2


def outcome(headers, payload, table):
    try:
        result = route(headers, payload, table)
    except Quarantined as q:
        return ("quarantined", q.reason)
    return ("decoded", result["source"])


class DecodeRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = demo.load_table()

    def test_checked_in_table_loads_with_the_deployed_schema(self):
        # The deployed schema is always approved; the demo's unapproved entry must stay unapproved.
        self.assertRegex(self.table.deployed, r"^[a-f0-9]{64}$")
        self.assertIn(demo.UNAPPROVED_SCHEMA, self.table.entries)
        self.assertFalse(self.table.entries[demo.UNAPPROVED_SCHEMA]["approved"])
        self.assertNotIn(demo.UNKNOWN_SCHEMA, self.table.entries)

    def test_stream_of_records_routes_like_production(self):
        t_ms = int(time.time() * 1000)
        orion = can_packets_pb2.OrionSensorData(time=t_ms, packet_id=7).SerializeToString()
        records = [(label, headers, payload) for label, _, headers, payload in demo.cases(self.table.deployed, t_ms)]
        records += demo.malformed_cases(self.table.deployed, t_ms)
        records += [
            ("older bridge, no fingerprint", demo.ARTEMIS_HEADERS, demo.board_batch(t_ms)),
            ("two fingerprints", demo.ARTEMIS_HEADERS + [(FINGERPRINT_HEADER, self.table.deployed.encode())] * 2,
             demo.board_batch(t_ms)),
            ("Orion snapshot", [("car_type", b"Orion")], orion),
            ("deployed schema id on an Orion record",
             [("car_type", b"Orion"), (FINGERPRINT_HEADER, self.table.deployed.encode())], orion),
        ]

        self.assertEqual({label: outcome(h, p, self.table) for label, h, p in records}, {
            "deployed schema": ("decoded", "deployed"),
            "unknown schema": ("quarantined", "unknown_schema"),
            "unapproved schema": ("quarantined", "unapproved_bundle"),
            "bare ArtemisSensorData": ("quarantined", "invalid_payload"),
            "no car_type header": ("quarantined", "invalid_payload"),
            "wrong payload_format": ("quarantined", "invalid_payload"),
            "older bridge, no fingerprint": ("decoded", "unlabelled"),
            "two fingerprints": ("quarantined", "invalid_payload"),
            "Orion snapshot": ("decoded", "unlabelled"),
            "deployed schema id on an Orion record": ("quarantined", "invalid_payload"),
        })

        # A decoded record carries the actual values, not just a verdict.
        label, _, headers, payload = demo.cases(self.table.deployed, t_ms)[0]
        (update,) = route(headers, payload, self.table)["updates"]
        self.assertEqual(update["time"], t_ms)
        self.assertTrue(update["board_status"])
        self.assertTrue(all(abs(v - 0.02) < 1e-6 for v in update["board_status"].values()))

    def test_approving_a_schema_in_the_table_lets_it_through(self):
        t_ms = int(time.time() * 1000)
        _, _, headers, payload = demo.cases(self.table.deployed, t_ms)[2]  # the unapproved schema
        entries = [dict(entry) for entry in self.table.entries.values()]
        for entry in entries:
            if entry["schema_id"] == demo.UNAPPROVED_SCHEMA:
                entry["approved"] = True
        approved = DecoderTable(entries, self.table.deployed)
        self.assertEqual(outcome(headers, payload, self.table), ("quarantined", "unapproved_bundle"))
        self.assertEqual(outcome(headers, payload, approved), ("decoded", "table"))


if __name__ == "__main__":
    unittest.main()
