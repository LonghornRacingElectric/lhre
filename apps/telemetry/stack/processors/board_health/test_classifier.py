import json
import unittest

from classifier import BOARDS, BoardStateTracker, change_events, classify


class BoardStateTests(unittest.TestCase):
    def test_boundaries_and_invalid_values(self):
        for value, expected in [
            (-1, "NEVER_SEEN"), (0, "LIVE"), (4.999, "LIVE"),
            (5, "STALE"), (9, "STALE"), (-0.5, "UNKNOWN"),
            (-2, "UNKNOWN"), (None, "UNKNOWN"), ("0", "UNKNOWN"),
            (True, "UNKNOWN"), (float("nan"), "UNKNOWN"),
            (float("inf"), "UNKNOWN"), (float("-inf"), "UNKNOWN"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(classify(value, 5), expected)

    def test_dropout_recovery_and_duplicate_suppression(self):
        tracker = BoardStateTracker(5, 10)
        expected = ["LIVE", "STALE", None, "LIVE", "STALE"]
        for now, (value, state) in enumerate(zip([0.2, 9, 10, 0, 5], expected)):
            changes = tracker.observe({"vcu_last_seen_s": value}, now)
            if state is None:
                self.assertEqual(changes, {})
            else:
                self.assertEqual(changes["vcu"][1], state)

    def test_independent_and_simultaneous_boards(self):
        tracker = BoardStateTracker(5, 10)
        frame = {f"{board}_last_seen_s": 0 for board in BOARDS}
        tracker.observe(frame, 0)
        frame["vcu_last_seen_s"] = 5
        self.assertEqual(tracker.observe(frame, 1), {"vcu": ("LIVE", "STALE")})
        frame.update(hvc_last_seen_s=6, csm_last_seen_s=7)
        self.assertEqual(set(tracker.observe(frame, 2)), {"hvc", "csm"})
        self.assertEqual(tracker.states["dui"], "LIVE")

    def test_initial_stale_and_reader_reset(self):
        tracker = BoardStateTracker(5, 10)
        self.assertEqual(tracker.observe({"vcu_last_seen_s": 9}, 0),
                         {"vcu": ("UNKNOWN", "STALE")})
        self.assertEqual(tracker.observe({"vcu_last_seen_s": -1}, 1),
                         {"vcu": ("STALE", "NEVER_SEEN")})
        self.assertEqual(tracker.observe({"vcu_last_seen_s": -1}, 100), {})

    def test_missing_status_and_timeout(self):
        tracker = BoardStateTracker(5, 10)
        self.assertEqual(tracker.expire(100), {})
        tracker.observe({"vcu_last_seen_s": 0}, 101)
        self.assertEqual(tracker.expire(110.9), {})
        self.assertEqual(tracker.expire(111), {"vcu": ("LIVE", "UNKNOWN")})
        self.assertEqual(tracker.expire(112), {})
        tracker.observe({"vcu_last_seen_s": 0}, 113)
        self.assertEqual(tracker.observe(None, 114), {"vcu": ("LIVE", "UNKNOWN")})
        self.assertEqual(tracker.observe([], 115), {})

    def test_invalid_configuration(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                BoardStateTracker(value, 10)
            with self.assertRaises(ValueError):
                BoardStateTracker(5, value)


class ChangeEventTests(unittest.TestCase):
    def test_frame_change_event(self):
        events = change_events({"vcu": ("LIVE", "STALE")},
                               {"vcu_last_seen_s": 9.0}, "frame", 1000)
        self.assertEqual(events, [{
            "car": "orion", "board": "vcu", "state": "STALE",
            "previous": "LIVE", "last_seen_s": 9.0, "cause": "frame", "t_ms": 1000,
        }])

    def test_no_changes_no_events(self):
        self.assertEqual(change_events({}, {"vcu_last_seen_s": 0}, "frame", 0), [])

    def test_timeout_events_have_no_last_seen(self):
        tracker = BoardStateTracker(5, 10)
        tracker.observe({f"{board}_last_seen_s": 0 for board in BOARDS}, 0)
        events = change_events(tracker.expire(10), None, "telemetry_timeout", 0)
        self.assertEqual([e["board"] for e in events], sorted(BOARDS))
        for event in events:
            self.assertEqual((event["previous"], event["state"]), ("LIVE", "UNKNOWN"))
            self.assertIsNone(event["last_seen_s"])
            self.assertEqual(event["cause"], "telemetry_timeout")

    def test_invalid_last_seen_is_null_and_json_safe(self):
        for value in ("NaN", "Infinity", float("nan"), True, None):
            with self.subTest(value=value):
                [event] = change_events({"vcu": ("LIVE", "UNKNOWN")},
                                        {"vcu_last_seen_s": value}, "frame", 0)
                self.assertIsNone(event["last_seen_s"])
                json.dumps(event, allow_nan=False)

    def test_missing_status_frame(self):
        [event] = change_events({"vcu": ("LIVE", "UNKNOWN")}, None, "frame", 0)
        self.assertIsNone(event["last_seen_s"])


if __name__ == "__main__":
    unittest.main()
