import unittest
from metrics import Timeline


class TimelineTest(unittest.TestCase):
    def test_late_commits_update_the_send_interval(self):
        timeline = Timeline(10)
        timeline.send(2)
        timeline.send(12)
        before = timeline.snapshot()
        self.assertIsNone(before[0]['p50_ms'])
        self.assertEqual(before[0]['unobserved'], 1)
        timeline.commit(2, 25000)
        after = timeline.snapshot()
        self.assertEqual(after[0]['p50_ms'], 25000)
        self.assertEqual(after[0]['unobserved'], 0)
        self.assertEqual(after[1]['unobserved'], 1)
        self.assertEqual(before[0]['unobserved'], 1)

    def test_missing_intervals_do_not_become_zero_latency(self):
        timeline = Timeline()
        for time in (1, 2, 11):
            timeline.send(time)
        timeline.commit(1, 100)
        timeline.commit(2, 300)
        result = timeline.snapshot()
        self.assertEqual(result[0]['p50_ms'], 200)
        self.assertEqual(result[0]['p95_ms'], 300)
        self.assertIsNone(result[1]['p95_ms'])
        self.assertEqual(timeline.snapshot(), result)


if __name__ == '__main__':
    unittest.main()
