"""Replays the recorded UTA autocross run the way the board health and fault demos do.

Every payload goes through the decoder ingest's consumers use
(`decode_updates`) into board_health's state tracker with the deployed 0.5 s
threshold, and through events_faults' own record handler. Time comes from a
fake clock that advances by the recorded gaps, so 110 s of driving replays
instantly with the same timing.
"""

import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from analysis.database.artemis_replay import BOARDS, Dropout, Fault, load_recording, replay, start_at
from classifier import BoardStateTracker
import faults
from faults import BOOLEAN_FAULTS
from stack.ingest.sensor_decode import decode_updates

# events_faults' handler lives in main.py, like board_health's; load it by path
# so the two processors' `main` modules cannot shadow each other.
_spec = importlib.util.spec_from_file_location(
    "events_faults_main", os.path.join(os.path.dirname(faults.__file__), "main.py"))
events_faults_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(events_faults_main)
process_record = events_faults_main.process_record

RECORDING = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "database", "data", "autox_uta_2026-10-17.pb.gz")
STALE_AFTER_S = 0.5
START_NS = 1_800_000_000_000_000_000  # 2027-01-15, well past BEVO's clock sanity floor
NEVER_REPORTED = {"csm", "tsm", "usm"}  # -1 throughout this recording


class FakeClock:
    def __init__(self):
        self.ns = START_NS

    def sleep(self, seconds):
        self.ns += round(seconds * 1e9)

    def now_ns(self):
        return self.ns


def publish(recording, dropout=None, faults=()):
    clock, payloads = FakeClock(), []
    replay(recording, payloads.append, dropout=dropout, faults=faults, loop=False,
           sleep=clock.sleep, now_ns=clock.now_ns)
    return payloads


def fault_events(payloads):
    """Feeds every payload to events_faults as Kafka records; returns (kind, fault, seconds in)."""
    diagnostics, events = {}, []
    for payload in payloads:
        record = SimpleNamespace(headers=[("car_type", b"Artemis"), ("payload_format", b"artemis_updates_v1")],
                                 value=payload)
        events += process_record(record, Mock(), diagnostics, "fault_events")
    return [(e["kind"], e["fault"], (e["t_ms"] * 1_000_000 - START_NS) / 1e9) for e in events]


def run(recording, dropout=None):
    """Replays once and feeds board_health. Returns (decoded updates, transitions)."""
    payloads = publish(recording, dropout)

    tracker = BoardStateTracker(STALE_AFTER_S, 10, {b: b + "_last_seen_s" for b in BOARDS})
    updates, transitions = [], []
    for payload in payloads:
        (update,) = decode_updates(payload)
        updates.append(update)
        elapsed_s = (update["sample_time_ns"] - START_NS) / 1e9
        for board, (previous, current) in tracker.observe(update["board_status"], elapsed_s).items():
            transitions.append((board, previous, current, elapsed_s))
    return updates, transitions


class ArtemisReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recording = load_recording(RECORDING)

    def test_clean_replay_keeps_every_reporting_board_live(self):
        updates, transitions = run(self.recording)

        self.assertEqual(len(updates), len(self.recording))
        self.assertEqual([u["time"] for u in updates], [u["sample_time_ns"] // 1_000_000 for u in updates])
        self.assertTrue(all(u.get("dynamics") and u.get("pack") for u in updates), "driving data went missing")
        final = {board: current for board, _, current, _ in transitions}
        for board in BOARDS:
            expected = "NEVER_SEEN" if board in NEVER_REPORTED else "LIVE"
            self.assertEqual(final.get(board), expected, board)
        # Real CAN timing never crosses the threshold, so the demo's STALE is the dropout alone.
        self.assertFalse([t for t in transitions if t[2] == "STALE"])

    def test_dropout_goes_stale_and_recovers(self):
        _, transitions = run(self.recording, dropout=Dropout("dui", after_s=20, for_s=5))

        dui = [(previous, current, t) for board, previous, current, t in transitions if board == "dui"]
        self.assertEqual([(p, c) for p, c, _ in dui],
                         [("UNKNOWN", "LIVE"), ("LIVE", "STALE"), ("STALE", "LIVE")])
        went_stale, recovered = dui[1][2], dui[2][2]
        # Crosses 0.5 s once the climb plus the DUI's own ~0.1 s report gap gets there.
        self.assertGreater(went_stale, 20.3)
        self.assertLess(went_stale, 20.6)
        self.assertGreaterEqual(recovered, 25)
        self.assertLess(recovered, 25.2)
        others = [t for t in transitions if t[0] != "dui" and t[2] == "STALE"]
        self.assertFalse(others, "only the dropped board should go stale")

    def test_demo_command_repeats_the_dropout_and_faults(self):
        # What `publish_demo -- --replay --dropout-board dui --fault apps_mismatch
        # --fault 'run_faults[2]' --start 60 --repeat-every 45` builds.
        recording = start_at(self.recording, 60)
        dropout = Dropout("dui", 20, 10, every_s=45)
        faults = [Fault(name, 5, 5, every_s=45) for name in ("apps_mismatch", "run_faults[2]")]
        payloads = publish(recording, dropout, faults)

        events = fault_events(payloads)
        injected = [(kind, round(t)) for kind, fault, t in events if fault == "apps_mismatch"]
        self.assertEqual(injected, [("fault_raised", 5), ("fault_cleared", 10), ("fault_raised", 50),
                                    ("fault_cleared", 55), ("fault_raised", 95), ("fault_cleared", 100)])
        self.assertEqual([(k, round(t)) for k, f, t in events if f == "run_faults[2]"], injected)
        # Starting 60 s in brings the run's own brake-over-throttle blip forward from 72.6 s.
        stomp = [(kind, t) for kind, fault, t in events if fault == "stomp_fault"]
        self.assertEqual([kind for kind, _ in stomp], ["fault_raised", "fault_cleared"])
        self.assertGreater(stomp[0][1], 12)
        self.assertLess(stomp[0][1], 13.5)
        self.assertEqual({fault for _, fault, _ in events}, {"apps_mismatch", "run_faults[2]", "stomp_fault"})

        tracker = BoardStateTracker(STALE_AFTER_S, 10, {b: b + "_last_seen_s" for b in BOARDS})
        stale = []
        for payload in payloads:
            (update,) = decode_updates(payload)
            elapsed_s = (update["sample_time_ns"] - START_NS) / 1e9
            for board, (_, current) in tracker.observe(update["board_status"], elapsed_s).items():
                if current == "STALE":
                    stale.append((board, elapsed_s))
        # The dropout opens 20 s into each 45 s cycle; DUI crosses 0.5 s about half a second later.
        self.assertEqual([board for board, _ in stale], ["dui", "dui"])
        for (_, t), opened in zip(stale, (20, 65)):
            self.assertGreater(t, opened + 0.3)
            self.assertLess(t, opened + 0.7)

    def test_every_watched_fault_can_be_injected(self):
        recording = self.recording[:200]  # 10 s
        for name in BOOLEAN_FAULTS:
            with self.subTest(fault=name):
                events = fault_events(publish(recording, faults=[Fault(name, after_s=2, for_s=3)]))
                self.assertEqual([(kind, fault, round(t)) for kind, fault, t in events],
                                 [("fault_raised", name, 2), ("fault_cleared", name, 5)])

    def test_bad_demo_windows_are_rejected(self):
        for build in (lambda: Fault("not_a_fault", 5, 5), lambda: start_at(self.recording, 500),
                      lambda: Dropout("dui", 40, 10, every_s=45),
                      # Mask bits above 23 do not survive the float32 the masks travel as.
                      lambda: Fault("run_faults[24]", 0, 1)):
            with self.assertRaises(ValueError):
                build()

    def test_loop_seam_keeps_time_moving_forward(self):
        frames = len(self.recording) + 50
        clock, payloads = FakeClock(), []
        replay(self.recording, payloads.append, loop=True, should_stop=lambda: len(payloads) >= frames,
               sleep=clock.sleep, now_ns=clock.now_ns)

        updates = [decode_updates(p)[0] for p in payloads]
        self.assertEqual(len(updates), frames)
        stamps = [u["sample_time_ns"] for u in updates]
        self.assertTrue(all(b >= a for a, b in zip(stamps, stamps[1:])), "time went backwards")
        seam = len(self.recording)
        self.assertGreater(stamps[seam], stamps[seam - 1], "the loop seam stalled")


if __name__ == "__main__":
    unittest.main()
