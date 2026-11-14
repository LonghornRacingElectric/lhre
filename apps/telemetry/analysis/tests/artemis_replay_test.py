"""Replays the recorded UTA autocross run the way the board health demo does.

Every payload goes through the decoder ingest's consumers use
(`decode_updates`) and into board_health's state tracker with the deployed
0.5 s threshold. Time comes from a fake clock that advances by the recorded
gaps, so 110 s of driving replays instantly with the same timing.
"""

import os
import unittest

from analysis.database.artemis_replay import BOARDS, Dropout, load_recording, replay
from classifier import BoardStateTracker
from stack.ingest.sensor_decode import decode_updates

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


def run(recording, dropout=None):
    """Replays once and feeds board_health. Returns (decoded updates, transitions)."""
    clock, payloads = FakeClock(), []
    replay(recording, payloads.append, dropout=dropout, loop=False,
           sleep=clock.sleep, now_ns=clock.now_ns)

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
