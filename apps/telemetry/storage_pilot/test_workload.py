from collections import Counter
from datetime import datetime, timezone
import unittest

from workload import batches, observations, signals, snapshots

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class WorkloadTest(unittest.TestCase):
    def test_independent_mixed_rate_counts(self):
        rows = list(observations('session', START, 2, signals()))
        self.assertEqual(Counter(row.signal_id for row in rows), {1: 200, 2: 2, 3: 20})
        self.assertEqual(rows, sorted(rows, key=lambda row: (row.ts, row.signal_id)))

    def test_unchanged_values_are_new_observations(self):
        speed = [row for row in observations('s', START, 1, signals()) if row.signal_id == 1]
        self.assertEqual(speed[0].value, speed[1].value)
        self.assertNotEqual(speed[0].sample_id, speed[1].sample_id)
        self.assertNotEqual(speed[0].ts, speed[1].ts)

    def test_reproducible_identity_timestamp_and_value(self):
        first = list(observations('s', START, 1, signals(), seed=23))
        self.assertEqual(first, list(observations('s', START, 1, signals(), seed=23)))
        self.assertNotEqual(first[0].value, next(observations('s', START, 1, signals(), seed=9)).value)

    def test_sample_ids_are_independent_and_contiguous(self):
        rows = list(observations('s', START, 2, signals()))
        for signal in signals():
            ids = [row.sample_id for row in rows if row.signal_id == signal.signal_id]
            self.assertEqual(ids, list(range(signal.hz * 2)))

    def test_non_divisor_rates_do_not_accumulate_drift(self):
        definitions = signals(7, 3, 11)
        rows = list(observations('s', START, 3, definitions))
        self.assertEqual(Counter(row.signal_id for row in rows), {1: 21, 2: 9, 3: 33})
        speed = [row for row in rows if row.signal_id == 1]
        self.assertEqual((speed[7].ts - START).total_seconds(), 1)

    def test_types_are_preserved(self):
        rows = list(observations('s', START, 1, signals()))
        self.assertTrue(all(type(row.value) is int for row in rows if row.signal_id == 1))
        self.assertTrue(all(type(row.value) is float for row in rows if row.signal_id != 1))

    def test_half_open_interval(self):
        rows = list(observations('s', START, 1, signals()))
        self.assertTrue(all(0 <= (row.ts - START).total_seconds() < 1 for row in rows))

    def test_snapshot_baseline_repeats_cached_slow_values(self):
        rows = list(snapshots('s', START, 2, signals()))
        self.assertEqual(len(rows), 200)
        self.assertEqual(len({row[3] for row in rows[:100]}), 1)
        self.assertNotEqual(rows[99][3], rows[100][3])

    def test_bad_rates_and_duration_rejected(self):
        for hz in (0, -1, 1001, 0.5):
            with self.subTest(hz=hz), self.assertRaises(ValueError):
                signals(hz, 1, 10)
        with self.assertRaises(ValueError):
            list(observations('s', START, 0, signals()))
        with self.assertRaises(ValueError):
            list(observations('s', datetime(2026, 1, 1), 1, signals()))

    def test_batches_keep_every_item(self):
        self.assertEqual(list(batches(range(5), 2)), [[0, 1], [2, 3], [4]])
        with self.assertRaises(ValueError):
            list(batches(range(5), 0))


if __name__ == '__main__':
    unittest.main()
