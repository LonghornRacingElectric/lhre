from datetime import timezone
import unittest

from pilot import percentile, start_time


class ReportMathTest(unittest.TestCase):
    def test_percentiles_use_all_sorted_measurements(self):
        values = [50, 10, 40, 20, 30]
        self.assertAlmostEqual(percentile(values, .95), 48)
        self.assertAlmostEqual(percentile(values, .99), 49.6)
        self.assertEqual(percentile([7], .99), 7)

    def test_empty_latency_measurements_are_not_a_zero_result(self):
        with self.assertRaises(ValueError):
            percentile([], .95)

    def test_explicit_offset_is_normalized_without_losing_fraction(self):
        result = start_time('2026-01-01T06:00:00.500000+06:00', 1)
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.microsecond, 500000)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_naive_start_is_rejected(self):
        with self.assertRaises(ValueError):
            start_time('2026-01-01T00:00:00', 1)


if __name__ == '__main__':
    unittest.main()
