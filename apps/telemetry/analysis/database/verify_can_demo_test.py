import time
import unittest

from analysis.database.verify_can_demo import observe, verify_history


class Feed:
    def __init__(self):
        self.rows = []

    def discover(self):
        return {'schema_version': 'demo', 'signals': [{'signal': s} for s in ['controls.enable', 'pack.hv_pack_v']]}

    def stream(self, *args, **kwargs):
        for value in [False, True]:
            stamp = str(time.time_ns())
            self.rows.extend([
                {'session_start_ms': '1', 'sample_time_ns': stamp, 'signal': 'controls.enable', 'value': value},
                {'session_start_ms': '1', 'sample_time_ns': stamp, 'signal': 'pack.hv_pack_v', 'value': 0 if not value else 410.5},
            ])
        yield {'type': 'data', 'page': {'data': self.rows}}

    def pages(self, *args):
        yield {'data': self.rows, 'sources': getattr(self, 'sources', ['postgres']),
               'provisional': getattr(self, 'provisional', False)}


class DemoVerificationTest(unittest.TestCase):
    def test_live_evidence_rechecks_exact_values_through_history(self):
        feed = Feed()
        evidence = observe(feed, ['controls.enable', 'pack.hv_pack_v'], .001)
        self.assertEqual(len(evidence['observations']), 4)
        self.assertEqual(verify_history(feed, evidence, require_postgres=True)['matched_observations'], 4)
        feed.sources, feed.provisional = ['ring'], True
        self.assertFalse(verify_history(feed, evidence)['postgres_verified'])
        with self.assertRaisesRegex(RuntimeError, 'Postgres-only'):
            verify_history(feed, evidence, require_postgres=True)
        feed.rows = [dict(row) for row in feed.rows]
        feed.rows[0]['value'] = 0
        with self.assertRaisesRegex(RuntimeError, 'Historical value differs'):
            verify_history(feed, evidence)

    def test_missing_live_samples_cannot_pass_as_a_complete_demo(self):
        class PartialFeed(Feed):
            def stream(self, *args, **kwargs):
                for event in super().stream(*args, **kwargs):
                    event['page']['data'] = self.rows[:2]
                    yield event
        with self.assertRaisesRegex(RuntimeError, 'No fresh repeated observations'):
            observe(PartialFeed(), ['controls.enable', 'pack.hv_pack_v'], .001)


if __name__ == '__main__':
    unittest.main()
