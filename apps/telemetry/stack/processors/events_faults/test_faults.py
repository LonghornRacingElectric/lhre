import unittest
from types import SimpleNamespace

from faults import BOOLEAN_FAULTS, detect_active_faults, diff_faults


class FaultDetectionTest(unittest.TestCase):
    def test_empty_diagnostics(self):
        self.assertEqual(detect_active_faults(None), set())
        self.assertEqual(detect_active_faults(SimpleNamespace()), set())

    def test_each_boolean_fault(self):
        for field in BOOLEAN_FAULTS:
            with self.subTest(field=field):
                self.assertEqual(
                    detect_active_faults(SimpleNamespace(**{field: True})), {field},
                )

    def test_unknown_polarity_fields_are_not_faults(self):
        diagnostics = SimpleNamespace(batt_fans_fuse=True, neg_hv_contactor=True)
        self.assertEqual(detect_active_faults(diagnostics), set())

    def test_bitmasks_are_separate_faults(self):
        diagnostics = SimpleNamespace(post_faults=5.0, run_faults=float(1 << 23))
        self.assertEqual(detect_active_faults(diagnostics), {
            "post_faults[0]", "post_faults[2]", "run_faults[23]",
        })

    def test_largest_safe_mask(self):
        self.assertEqual(len(detect_active_faults(
            SimpleNamespace(run_faults=float((1 << 24) - 1)),
        )), 24)

    def test_invalid_or_imprecise_mask_rejects_frame(self):
        for value in (-1, 0.5, float("nan"), float("inf"), 1 << 24, None, "bad"):
            for field in ("post_faults", "run_faults"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        detect_active_faults(SimpleNamespace(**{field: value}))


class FaultTransitionTest(unittest.TestCase):
    def test_repeated_state_produces_no_event(self):
        self.assertEqual(diff_faults({"stomp_fault"}, {"stomp_fault"}, "orion", 1), [])

    def test_clean_start_produces_no_event(self):
        self.assertEqual(diff_faults(set(), set(), "orion", 1), [])

    def test_raise_and_clear_in_one_frame(self):
        self.assertEqual(diff_faults(
            {"stomp_fault"}, {"run_faults[0]", "apps_mismatch"}, "orion", 1234,
        ), [
            {"kind": "fault_raised", "fault": "apps_mismatch", "car": "orion", "t_ms": 1234},
            {"kind": "fault_raised", "fault": "run_faults[0]", "car": "orion", "t_ms": 1234},
            {"kind": "fault_cleared", "fault": "stomp_fault", "car": "orion", "t_ms": 1234},
        ])


if __name__ == "__main__":
    unittest.main()
