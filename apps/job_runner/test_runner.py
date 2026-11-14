import base64
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import runner


ID = 'a' * 32
SETTINGS = {'id': ID, 'template': 'fmu', 'parallel': 1, 'seconds': 15}
NOW = '2026-11-14T20:00:00+00:00'


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = runner.Queue(self.temp.name, now=lambda: NOW)
        self.free = patch.object(runner.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024**3})())
        self.free.start()
        self.addCleanup(self.free.stop)

    def test_rejects_commands_paths_boolean_limits_and_bad_ids(self):
        for change in [{'id': '../../etc'}, {'id': None}, {'parallel': True}, {'parallel': 0},
                       {'parallel': 9}, {'seconds': 121}, {'template': 'shell'}, {'command': 'id'}]:
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                runner.validate({**SETTINGS, **change})
        with self.assertRaises(ValueError):
            runner.validate({**SETTINGS, 'template': 'gpu', 'parallel': 2})

    def test_idempotent_submit_and_conflicting_retry(self):
        self.queue.submit(SETTINGS)
        self.queue.submit(SETTINGS)
        self.assertEqual(len(self.queue.jobs()), 1)
        with self.assertRaises(ValueError):
            self.queue.submit({**SETTINGS, 'parallel': 2})
        self.assertEqual(self.queue.get(ID)['created'], NOW)

    def test_queue_survives_recreation_and_cancel_is_durable(self):
        self.queue.submit(SETTINGS)
        self.queue.cancel(ID)
        again = runner.Queue(self.temp.name, now=lambda: NOW)
        self.assertEqual(again.get(ID)['status'], 'cancelled')
        with patch.object(runner.subprocess, 'run') as run:
            runner.tick(again)
            run.assert_not_called()

    def test_running_cancel_does_not_claim_completion_early(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        result = self.queue.cancel(ID)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['cancel_requested'], 1)

    def test_pause_survives_restart_and_does_not_start_jobs(self):
        self.queue.submit(SETTINGS)
        self.queue.pause(True)
        with patch.object(runner.subprocess, 'run') as run:
            runner.tick(runner.Queue(self.temp.name))
            run.assert_not_called()
        self.assertEqual(self.queue.get(ID)['status'], 'queued')
        with self.assertRaises(ValueError):
            self.queue.pause('false')

    def test_container_limits_and_fixed_executable(self):
        args = runner.command({**SETTINGS, 'parallel': 8}, '/trusted/results')
        self.assertEqual(args[:3], ['docker', 'run', '-d'])
        self.assertEqual(args[args.index('--cpus') + 1], '8')
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertIn('--read-only', args)
        self.assertNotIn('--privileged', args)
        self.assertNotIn('/var/run/docker.sock', ' '.join(args))
        gpu = runner.command({**SETTINGS, 'template': 'gpu'}, '/trusted/results')
        self.assertEqual(gpu[gpu.index('--gpus') + 1], 'all')

    def test_runtime_reconciliation_uses_docker_state(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        state = {'State': {'Running': False, 'ExitCode': 7, 'OOMKilled': False}}
        with patch.object(runner, 'inspect', return_value=state), patch.object(runner, 'capture'), patch.object(runner.time, 'time', return_value=1794686405):
            runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'failed')
        self.assertEqual(self.queue.get(ID)['exit_code'], 7)

    def test_recovery_after_worker_restart_does_not_duplicate_container(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='starting', started=NOW)
        with patch.object(runner, 'inspect', return_value={'State': {'Running': True}}), patch.object(runner.time, 'time', return_value=1794686405), patch.object(runner.subprocess, 'run') as run:
            runner.tick(self.queue)
            run.assert_not_called()
        self.assertEqual(self.queue.get(ID)['status'], 'running')

    def test_resource_budget_keeps_eight_process_job_queued(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        second = {**SETTINGS, 'id': 'b' * 32, 'parallel': 8}
        self.queue.submit(second)
        with patch.object(runner, 'inspect', return_value={'State': {'Running': True}}), patch.object(runner.time, 'time', return_value=1794686405), patch.object(runner.subprocess, 'run') as run:
            runner.tick(self.queue)
            run.assert_not_called()
        self.assertEqual(self.queue.get(second['id'])['status'], 'queued')

    def test_archive_excludes_symlinks_and_unapproved_extensions(self):
        self.queue.submit(SETTINGS)
        directory = self.queue.directory(ID)
        directory.mkdir()
        (directory / 'results.json').write_text('{"complete":true}')
        (directory / 'secret.bin').write_text('exclude')
        (directory / 'link.log').symlink_to(directory / 'secret.bin')
        with self.assertRaises(ValueError):
            runner.archive(self.queue, ID)
        self.queue.update(ID, status='succeeded', finished=NOW)
        data = runner.archive(self.queue, ID)
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(data['base64']))) as tar:
            self.assertEqual(tar.getnames(), ['job.json', 'results.json'])
            self.assertTrue(json.load(tar.extractfile('results.json'))['complete'])

    def test_queue_limit_and_disk_reserve(self):
        for number in range(30):
            self.queue.submit({**SETTINGS, 'id': f'{number:032x}'})
        with self.assertRaisesRegex(ValueError, 'full'):
            self.queue.submit(SETTINGS)
        with patch.object(runner.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()), self.assertRaises(ValueError):
            runner.Queue(Path(self.temp.name) / 'other').submit(SETTINGS)

    def test_rpc_rejects_unknown_operations(self):
        with self.assertRaises(ValueError):
            runner.rpc(self.queue, {'op': 'exec', 'command': 'id'})
        with self.assertRaises(ValueError):
            runner.rpc(self.queue, {'op': 'exec'})

    def test_running_cancel_stops_docker_before_marking_cancelled(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        self.queue.cancel(ID)
        states = [{'State': {'Running': True}}, {'State': {'Running': False, 'ExitCode': 137}}]
        with patch.object(runner, 'inspect', side_effect=states), patch.object(runner, 'docker') as docker, patch.object(runner, 'capture') as capture, patch.object(runner.time, 'time', return_value=1794686405):
            runner.tick(self.queue)
            docker.assert_called_once_with(['stop', '-t', '5', 'lhr-job-' + ID], check=False)
            capture.assert_called_once_with(self.queue, ID)
        self.assertEqual(self.queue.get(ID)['status'], 'cancelled')

    def test_runtime_limit_stops_container_and_retains_reason(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        states = [{'State': {'Running': True}}, {'State': {'Running': False, 'ExitCode': 137}}]
        with patch.object(runner, 'inspect', side_effect=states), patch.object(runner, 'docker'), patch.object(runner, 'capture'), patch.object(runner.time, 'time', return_value=1794688000):
            runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'cancelled')
        self.assertEqual(self.queue.get(ID)['error'], 'Runtime limit (15 min)')

    def test_gpu_reservation_prevents_second_gpu_job(self):
        self.queue.submit({**SETTINGS, 'template': 'gpu'})
        self.queue.update(ID, status='running', started=NOW)
        self.queue.submit({**SETTINGS, 'template': 'gpu', 'id': 'b' * 32})
        with patch.object(runner, 'inspect', return_value={'State': {'Running': True}}), patch.object(runner.subprocess, 'run') as run, patch.object(runner.time, 'time', return_value=1794686405):
            runner.tick(self.queue)
            run.assert_not_called()
        self.assertEqual(self.queue.get('b' * 32)['status'], 'queued')

    def test_log_capture_does_not_follow_container_created_symlink(self):
        self.queue.submit(SETTINGS)
        directory = self.queue.directory(ID)
        directory.mkdir()
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('unchanged')
        (directory / 'stdout.log').symlink_to(outside)
        with patch.object(runner, 'docker', return_value=type('Result', (), {'stdout': 'output', 'stderr': ''})()):
            runner.capture(self.queue, ID)
        self.assertEqual(outside.read_text(), 'unchanged')
        self.assertFalse((directory / 'stdout.log').is_symlink())
        self.assertEqual((directory / 'stdout.log').read_text(), 'output')


if __name__ == '__main__':
    unittest.main()
