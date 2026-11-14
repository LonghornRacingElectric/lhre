import statistics
import unittest
from types import SimpleNamespace
import common as c
from reads import iso, milliseconds, oracle, workloads, width_for


class ReadMatrixTest(unittest.TestCase):
    def test_independent_oracle_matches_bruteforce_non_aligned_buckets(self):
        start = 1700000000000
        catalog = {"pools": [{"table": "controls_333hz", "group": "controls", "frequency_hz": 333}],
                   "signals": [{"pool": "controls_333hz", "signal": "controls.apps1_travel"}]}
        for aggregate in ("raw", "mean", "max"):
            body = {"t0": iso(start + 123), "t1": iso(start + 1827), "signals": ["controls.apps1_travel"], "agg": aggregate, "max_points": 11}
            width = width_for(catalog, body)
            rows = []
            buckets = {}
            for i in range(666):
                ns = start * 1000000 + i * 1000000000 // 333
                ms = ns // 1000000
                if start + 123 <= ms < start + 1827:
                    if aggregate == "raw":
                        rows.append((iso(ms), start, ns, "controls.apps1_travel", float(i % 100)))
                    else:
                        buckets.setdefault(ms // width * width, []).append(i % 100)
            if aggregate != "raw":
                rows = [(iso(ms), start, ms * 1000000, "controls.apps1_travel", statistics.mean(values) if aggregate == "mean" else max(values)) for ms, values in sorted(buckets.items())]
            fingerprint = c.Fingerprint()
            for row in rows:
                fingerprint.add(row)
            actual = oracle(catalog, start, 2, body)
            self.assertEqual(actual['sha256'], fingerprint.result(set(), 1)['sha256'])
            self.assertEqual(actual['rows'], len(rows))

    def test_matrix_has_distinct_windows_and_no_large_raw_scan(self):
        args = SimpleNamespace(seed=42, windows=[10, 600, 3600, 21600, 86400], random_windows=3, aggregates=['raw', 'mean', 'max'], clients=50)
        cases = workloads(1700000000000, 86400, args)
        self.assertTrue(all(case['window_seconds'] <= 600 for case in cases if case['aggregate'] == 'raw'))
        self.assertEqual({case['position'] for case in cases if case['window_seconds'] == 3600}, {'beginning', 'middle', 'end'})
        self.assertTrue(all(case['unique_windows'] == 50 for case in cases if case['window_seconds'] == 600))
        self.assertTrue(all(case['unique_windows'] == 1 for case in cases if case['window_seconds'] == 86400))
        self.assertEqual(workloads(1700000000000, 86400, args), cases)


if __name__ == '__main__':
    unittest.main()
