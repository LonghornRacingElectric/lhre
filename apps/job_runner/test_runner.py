import base64
import io
import json
import os
import sys
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import runner
import task


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

    def test_database_connections_close_after_commit_and_rollback(self):
        with self.queue.connect() as committed:
            committed.execute("INSERT OR REPLACE INTO settings VALUES('paused','1')")
        self.assertTrue(self.queue.paused())
        with self.assertRaises(sqlite3.ProgrammingError):
            committed.execute('SELECT 1')
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.queue.connect() as rolled_back:
                rolled_back.execute("UPDATE settings SET value='0' WHERE key='paused'")
                raise RuntimeError('rollback')
        self.assertTrue(self.queue.paused())
        with self.assertRaises(sqlite3.ProgrammingError):
            rolled_back.execute('SELECT 1')

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

    def test_only_fmu_scratch_allows_loading_native_libraries(self):
        fmu = runner.command(SETTINGS, self.temp.name)
        self.assertIn('--tmpfs=/tmp:rw,exec,nosuid,nodev,size=536870912', fmu)
        for template in ['cpu', 'gpu']:
            args = runner.command({**SETTINGS, 'template': template}, self.temp.name)
            self.assertFalse(any(arg.startswith('--tmpfs') for arg in args))
            self.assertIn('type=tmpfs,destination=/tmp,tmpfs-size=536870912', args)
        for args in [fmu, runner.command({**SETTINGS, 'template': 'cpu'}, self.temp.name)]:
            self.assertIn('--read-only', args)
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertEqual(args[args.index('--cap-drop') + 1], 'ALL')
            self.assertEqual(args[args.index('--security-opt') + 1], 'no-new-privileges')

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

    def test_late_reconciliation_preserves_completed_container_outcome(self):
        for exit_code, status in [(0, 'succeeded'), (7, 'failed')]:
            with self.subTest(exit_code=exit_code):
                self.queue.submit(SETTINGS)
                self.queue.update(ID, status='running', started=NOW)
                state = {'State': {'Running': False, 'ExitCode': exit_code}}
                with patch.object(runner, 'inspect', return_value=state), patch.object(runner, 'capture'), patch.object(runner, 'docker') as docker, patch.object(runner.time, 'time', return_value=1794688000):
                    runner.tick(self.queue)
                    docker.assert_not_called()
                job = self.queue.get(ID)
                self.assertEqual(job['status'], status)
                self.assertEqual(job['exit_code'], exit_code)
                self.assertIsNone(job['error'])

    def test_failed_capture_blocks_archive_and_retries_before_completion(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        state = {'State': {'Running': False, 'ExitCode': 0}}
        success = subprocess.CompletedProcess(['docker', 'logs'], 0, 'complete output', 'complete stderr')
        with patch.object(runner, 'inspect', return_value=state), patch.object(runner, 'docker', side_effect=[subprocess.TimeoutExpired('docker logs', 25), success]), patch.object(runner.time, 'time', return_value=1794686405) as clock:
            runner.tick(self.queue)
            with self.queue.connect() as db:
                self.assertEqual(db.execute('SELECT value FROM settings WHERE key=?', ('errors:' + ID,)).fetchone()[0], '1')
            self.assertEqual(self.queue.get(ID)['status'], 'running')
            with self.assertRaises(ValueError):
                runner.archive(self.queue, ID)
            clock.return_value = 1794688000
            runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'succeeded')
        archive = runner.archive(self.queue, ID)
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(archive['base64']))) as tar:
            self.assertEqual(tar.extractfile('stdout.log').read(), b'complete output')
            self.assertEqual(tar.extractfile('stderr.log').read(), b'complete stderr')
            self.assertEqual(json.load(tar.extractfile('job.json'))['status'], 'succeeded')

    def test_docker_logs_exit_failure_is_not_saved_as_successful_capture(self):
        self.queue.submit(SETTINGS)
        def failed_logs(args, **kwargs):
            if kwargs['check']:
                raise subprocess.CalledProcessError(1, args, stderr='daemon unavailable')
            return subprocess.CompletedProcess(args, 1, '', 'daemon unavailable')
        with patch.object(runner.subprocess, 'run', side_effect=failed_logs):
            with self.assertRaises(subprocess.CalledProcessError):
                runner.capture(self.queue, ID)
        self.assertFalse((self.queue.directory(ID) / 'stderr.log').exists())

    def test_slow_launch_reserves_gpu_slots_and_concurrency_even_before_start(self):
        for template, parallel, count in [('gpu', 1, 2), ('cpu', 8, 2), ('cpu', 1, 5)]:
            with self.subTest(template=template, parallel=parallel), tempfile.TemporaryDirectory() as directory:
                queue = runner.Queue(directory, now=lambda: NOW)
                for number in range(count):
                    queue.submit({**SETTINGS, 'id': f'{number:032x}', 'template': template, 'parallel': parallel})
                timeout = subprocess.TimeoutExpired('docker run', 40, stderr=b'launch delayed')
                state = {'State': {'Running': False, 'Status': 'created', 'ExitCode': 0}}
                with patch.object(runner.subprocess, 'run', side_effect=timeout) as run, patch.object(runner, 'inspect', return_value=state), patch.object(runner.time, 'time', return_value=1794686405):
                    runner.tick(queue)
                    self.assertEqual(run.call_count, 4 if count == 5 else 1)
                    runner.tick(queue)
                    self.assertEqual(run.call_count, 4 if count == 5 else 1)
                first = queue.get('0' * 32)
                self.assertEqual(first['status'], 'starting')
                self.assertEqual(first['error'], 'launch delayed')
                self.assertEqual(queue.get(f'{count - 1:032x}')['status'], 'queued')

    def test_launch_timeout_reserves_resources_while_container_is_not_yet_visible(self):
        self.queue.submit({**SETTINGS, 'parallel': 8})
        self.queue.submit({**SETTINGS, 'id': 'b' * 32})
        with patch.object(runner.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker', 40)) as run, patch.object(runner, 'inspect', return_value=None), patch.object(runner.time, 'time', return_value=1794686405):
            runner.tick(self.queue)
            runner.tick(self.queue)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.queue.get(ID)['status'], 'starting')
        self.assertEqual(self.queue.get('b' * 32)['status'], 'queued')
        self.queue.pause(True)
        with patch.object(runner, 'inspect', return_value=None), patch.object(runner, 'docker', return_value=subprocess.CompletedProcess([], 1, '', 'No such container')) as docker, patch.object(runner.time, 'time', return_value=1794686500):
            runner.tick(self.queue)
            docker.assert_called_once_with(['rm', '-f', runner.container_name(ID)], check=False)
        self.assertEqual(self.queue.get(ID)['status'], 'failed')

    def test_created_container_has_deadline_and_is_removed_before_failure(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='starting', started=NOW, error='launch delayed')
        state = {'State': {'Running': False, 'Status': 'created', 'ExitCode': 0}}
        with patch.object(runner, 'inspect', return_value=state), patch.object(runner, 'docker', return_value=subprocess.CompletedProcess([], 0, '', '')) as docker, patch.object(runner.time, 'time', return_value=1794686500):
            runner.tick(self.queue)
            docker.assert_called_once_with(['rm', '-f', runner.container_name(ID)])
        self.assertEqual(self.queue.get(ID)['status'], 'failed')
        self.assertEqual(self.queue.get(ID)['error'], 'launch delayed')

    def test_pause_between_launches_is_checked_in_claim(self):
        self.queue.submit(SETTINGS)
        self.queue.submit({**SETTINGS, 'id': 'b' * 32})
        def launch(*args, **kwargs):
            runner.Queue(self.temp.name, now=lambda: NOW).pause(True)
        with patch.object(runner.subprocess, 'run', side_effect=launch) as run:
            runner.tick(self.queue)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.queue.get('b' * 32)['status'], 'queued')
        self.assertIsNone(self.queue.get('b' * 32)['started'])

    def test_bad_capture_does_not_block_runtime_enforcement_or_new_jobs(self):
        # Newest job is visited first: its broken capture used to abort every tick.
        older = 'b' * 32
        self.queue.submit({**SETTINGS, 'id': older})
        self.queue.update(older, status='running', started=NOW)
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        queued = 'c' * 32
        self.queue.submit({**SETTINGS, 'id': queued})
        stopped = set()
        def inspect(job_id):
            return {'State': {'Running': job_id == older and older not in stopped, 'ExitCode': 0}}
        def docker(args, **kwargs):
            if args[0] == 'stop':
                stopped.add(older)
            return subprocess.CompletedProcess(args, 0, '', '')
        def capture(queue, job_id):
            if job_id == ID:
                raise OSError('corrupt log')
        with patch.object(runner, 'inspect', side_effect=inspect), patch.object(runner, 'docker', side_effect=docker), patch.object(runner, 'capture', side_effect=capture), patch.object(runner.subprocess, 'run') as launch, patch.object(runner.time, 'time', return_value=1794688000) as clock:
            runner.tick(self.queue)
            launch.assert_called_once()
            self.assertIn(older, stopped)
            self.assertEqual(self.queue.get(older)['status'], 'cancelled')
            self.assertEqual(self.queue.get(ID)['status'], 'running')
            for _ in range(2):
                clock.return_value += 60
                runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'failed')
        self.assertIn('3 attempts', self.queue.get(ID)['error'])

    def test_full_database_does_not_block_other_jobs_runtime_stop(self):
        older = 'b' * 32
        self.queue.submit({**SETTINGS, 'id': older})
        self.queue.update(older, status='running', started=NOW)
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        connect = self.queue.connect
        class FullDatabase:
            def __enter__(self):
                self.context = connect()
                self.db = self.context.__enter__()
                return self
            def __exit__(self, *args):
                return self.context.__exit__(*args)
            def execute(self, sql, *args):
                if sql.startswith('INSERT OR REPLACE INTO settings'):
                    raise sqlite3.OperationalError('database full')
                return self.db.execute(sql, *args)
        def inspect(job_id):
            return {'State': {'Running': job_id == older, 'ExitCode': 0}}
        with patch.object(self.queue, 'connect', side_effect=FullDatabase), patch.object(self.queue, 'update', side_effect=sqlite3.OperationalError('database full')), patch.object(runner, 'inspect', side_effect=inspect), patch.object(runner, 'capture', side_effect=OSError('disk full')), patch.object(runner, 'docker') as docker, patch.object(runner.time, 'time', return_value=1794688000):
            runner.tick(self.queue)
            docker.assert_called_once_with(['stop', '-t', '5', runner.container_name(older)], check=False)

    def test_finalization_does_not_allow_concurrent_archive(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        def capture(queue, job_id):
            with self.assertRaisesRegex(ValueError, 'after the job finishes'):
                runner.archive(runner.Queue(self.temp.name, now=lambda: NOW), job_id)
        with patch.object(runner, 'inspect', return_value={'State': {'Running': False, 'ExitCode': 0}}), patch.object(runner, 'capture', side_effect=capture), patch.object(runner.time, 'time', return_value=1794686405):
            runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'succeeded')

    def test_launch_failure_without_container_keeps_stderr(self):
        self.queue.submit(SETTINGS)
        with patch.object(runner.subprocess, 'run', side_effect=subprocess.CalledProcessError(125, 'docker', stderr='missing local image')), patch.object(runner, 'inspect', return_value=None):
            runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'failed')
        self.assertEqual(self.queue.get(ID)['error'], 'missing local image')

    def test_results_use_only_bounded_logs_without_writable_host_mount(self):
        args = runner.command(SETTINGS, self.temp.name)
        self.assertIn('--pull=never', args)
        self.assertEqual(args[args.index('--memory') + 1], args[args.index('--memory-swap') + 1])
        self.assertFalse(any('type=bind' in value for value in args))
        self.queue.submit(SETTINGS)
        output = {'template': 'cpu', 'results': [{'events': 123}]}
        with patch.object(runner, 'docker', return_value=subprocess.CompletedProcess([], 0, 'progress\n' + json.dumps(output) + '\n', '')):
            runner.capture(self.queue, ID)
        self.assertEqual(json.loads((self.queue.directory(ID) / 'results.json').read_text()), output)

    def test_heartbeat_and_prune_continue_when_tick_fails(self):
        with patch.object(runner, 'sweep_orphans', return_value=True), patch.object(runner, 'tick', side_effect=OSError('disk full')), patch.object(runner, 'prune') as prune, patch.object(runner, 'cache_status') as status, patch.object(runner, 'cache_logs'):
            runner.worker_cycle(self.queue)
            prune.assert_called_once_with(self.queue)
            status.assert_called_once_with(self.queue)
        self.assertTrue((self.queue.root / 'heartbeat').exists())

    def test_status_and_logs_rpcs_use_worker_cache_not_subprocesses(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        directory = self.queue.directory(ID)
        directory.mkdir()
        (directory / 'stdout.log').write_text('cached output')
        (self.queue.root / 'status.json').write_text(json.dumps({'templates': {'cpu': True}, 'online': True, 'sampled_at': 1000}))
        (self.queue.root / 'heartbeat').touch()
        os.utime(self.queue.root / 'heartbeat', (1000, 1000))
        with patch.object(runner.subprocess, 'run') as run, patch.object(runner.time, 'time', return_value=1005):
            status = runner.rpc(self.queue, {'op': 'status'})
            self.assertTrue(status['online'])
            self.assertTrue(status['templates']['cpu'])
            self.assertEqual(runner.rpc(self.queue, {'op': 'logs', 'id': ID})['stdout'], 'cached output')
            run.assert_not_called()

    def test_prune_keeps_running_or_unremovable_containers_and_deletes_missing(self):
        for number in range(203):
            self.queue.submit({**SETTINGS, 'id': f'{number:032x}'})
            self.queue.update(f'{number:032x}', status='succeeded', finished=NOW)
        self.queue.directory('0' * 32).mkdir()
        results = [subprocess.CompletedProcess([], 1, '', 'container is running'),
                   subprocess.CompletedProcess([], 1, '', 'No such container'),
                   subprocess.CompletedProcess([], 0, '', '')]
        with patch.object(runner, 'docker', side_effect=results) as docker:
            runner.prune(self.queue)
            self.assertEqual(docker.call_count, 3)
        self.assertEqual(len(self.queue.jobs()), 201)
        self.assertEqual(self.queue.get(f'{2:032x}')['status'], 'succeeded')
        with self.assertRaises(ValueError):
            self.queue.get('0' * 32)

    def test_main_bounds_rpc_input_and_maps_errors(self):
        for data, error in [(b'x' * 8193, 'too large'), (b'not-json', 'Expecting value'), (b'{"op":"exec"}', 'Unknown operation')]:
            output = io.StringIO()
            with patch.object(sys, 'argv', ['runner', 'rpc', '--root', self.temp.name]), patch.object(sys, 'stdin', type('Input', (), {'buffer': io.BytesIO(data)})()), redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    runner.main()
                self.assertEqual(raised.exception.code, 1)
            response = json.loads(output.getvalue())
            self.assertFalse(response['ok'])
            self.assertIn(error, response['error'])
        output = io.StringIO()
        with patch.object(sys, 'argv', ['runner', 'rpc', '--root', self.temp.name]), patch.object(sys, 'stdin', type('Input', (), {'buffer': io.BytesIO(b'{"op":"pause","paused":true}')})()), redirect_stdout(output):
            runner.main()
        self.assertEqual(json.loads(output.getvalue()), {'ok': True, 'result': {'paused': True}})

    def test_rpc_concurrency_limit_rejects_before_reading_or_constructing_queue(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['runner', 'rpc', '--root', self.temp.name]), patch.object(runner.fcntl, 'flock', side_effect=BlockingIOError) as flock, patch.object(runner, 'Queue') as queue, redirect_stdout(output):
            with self.assertRaises(SystemExit):
                runner.main()
            queue.assert_not_called()
            self.assertEqual(flock.call_count, 4)
        self.assertIn('busy', json.loads(output.getvalue())['error'])

    def test_worker_caches_image_and_gpu_status_and_live_logs(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        def docker(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, 'cached log' if args[0] == 'logs' else '', 'warning')
        with patch.object(runner, 'docker', side_effect=docker), patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'fixture gpu', '')), patch.object(runner.os, 'getloadavg', return_value=(0, 0, 0)), patch.object(runner.time, 'time', return_value=1000):
            runner.cache_status(self.queue)
            runner.cache_logs(self.queue)
        self.assertEqual(json.loads((self.queue.root / 'status.json').read_text())['gpu'], 'fixture gpu')
        self.assertEqual(runner.logs(self.queue, ID), {'stdout': 'cached log', 'stderr': 'warning'})

    def test_orphan_sweep_removes_late_failed_and_unknown_jobs_but_preserves_live_jobs(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        failed, unknown = 'b' * 32, 'c' * 32
        self.queue.submit({**SETTINGS, 'id': failed})
        self.queue.update(failed, status='failed', finished=NOW)
        names = '\n'.join([runner.container_name(ID), runner.container_name(failed), runner.container_name(unknown), 'unrelated-service'])
        def docker(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, names if args[0] == 'ps' else '', '')
        with patch.object(runner, 'docker', side_effect=docker) as run:
            self.assertTrue(runner.sweep_orphans(self.queue))
        self.assertEqual(run.call_args_list[0].args[0], ['ps', '-a', '--filter', 'label=lhr.runner=jobs-v1', '--format', '{{.Names}}'])
        self.assertEqual([call.args[0] for call in run.call_args_list[1:]], [['rm', '-f', runner.container_name(failed)], ['rm', '-f', runner.container_name(unknown)]])

    def test_failed_orphan_cleanup_blocks_new_launches_without_blocking_runtime_checks(self):
        self.queue.submit(SETTINGS)
        self.queue.submit({**SETTINGS, 'id': 'b' * 32})
        self.queue.update(ID, status='running', started=NOW)
        with patch.object(runner, 'sweep_orphans', return_value=False), patch.object(runner, 'inspect', return_value={'State': {'Running': True}}) as inspect, patch.object(runner.time, 'time', return_value=1794686405), patch.object(runner.subprocess, 'run') as run, patch.object(runner, 'prune'), patch.object(runner, 'cache_status'), patch.object(runner, 'cache_logs'):
            runner.worker_cycle(self.queue)
            inspect.assert_called_once_with(ID)
            run.assert_not_called()
        self.assertEqual(self.queue.get('b' * 32)['status'], 'queued')

    def test_short_capture_hiccup_remains_retryable_for_two_minutes(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        state = {'State': {'Running': False, 'ExitCode': 0}}
        with patch.object(runner, 'inspect', return_value=state), patch.object(runner, 'capture', side_effect=OSError('temporary log failure')), patch.object(runner.time, 'time', return_value=1794686405) as clock:
            for delta in [0, 2, 6]:
                clock.return_value = 1794686405 + delta
                runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'running')
        with patch.object(runner, 'inspect', return_value=state), patch.object(runner, 'capture'), patch.object(runner.time, 'time', return_value=1794686415):
            runner.tick(self.queue)
        self.assertEqual(self.queue.get(ID)['status'], 'succeeded')
        with self.queue.connect() as db:
            self.assertIsNone(db.execute('SELECT value FROM settings WHERE key=?', ('error_since:' + ID,)).fetchone())

    def test_status_hardware_sampling_is_throttled_to_thirty_seconds(self):
        with patch.object(runner, 'docker', return_value=subprocess.CompletedProcess([], 0, '', '')) as docker, patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'fixture GPU', '')) as nvidia, patch.object(runner.time, 'time', return_value=1000) as clock, patch.object(runner.os, 'getloadavg', return_value=(0, 0, 0)):
            runner.cache_status(self.queue)
            self.assertEqual(docker.call_count, 3)  # One daemon check and two unique images.
            self.assertEqual(nvidia.call_count, 1)
            clock.return_value = 1029
            runner.cache_status(self.queue)
            self.assertEqual(docker.call_count, 3)
            self.assertEqual(nvidia.call_count, 1)
            clock.return_value = 1030
            runner.cache_status(self.queue)
            self.assertEqual(docker.call_count, 6)
            self.assertEqual(nvidia.call_count, 2)

    def test_fresh_heartbeat_does_not_hide_docker_failure_or_stale_status(self):
        heartbeat = self.queue.root / 'heartbeat'
        heartbeat.touch()
        os.utime(heartbeat, (1000, 1000))
        with patch.object(runner, 'docker', side_effect=RuntimeError('Docker unavailable')), patch.object(runner.time, 'time', return_value=1005), patch.object(runner.os, 'getloadavg', return_value=(0, 0, 0)):
            runner.cache_status(self.queue)
            status = runner.rpc(self.queue, {'op': 'status'})
            self.assertFalse(status['online'])
            self.assertIn('Docker unavailable', status['error'])
        (self.queue.root / 'status.json').write_text(json.dumps({'online': True, 'sampled_at': 900}))
        with patch.object(runner.time, 'time', return_value=1005):
            self.assertFalse(runner.rpc(self.queue, {'op': 'status'})['online'])

    def test_log_cache_replacement_preserves_old_content_until_atomic_rename(self):
        self.queue.submit(SETTINGS)
        self.queue.update(ID, status='running', started=NOW)
        directory = self.queue.directory(ID)
        directory.mkdir()
        for name in ['stdout.log', 'stderr.log']:
            (directory / name).write_text('previous')
        replace = Path.replace
        def checked_replace(temporary, target):
            self.assertEqual(target.read_text(), 'previous')
            self.assertEqual(temporary.read_text(), 'new output')
            return replace(temporary, target)
        with patch.object(runner, 'docker', return_value=subprocess.CompletedProcess([], 0, 'new output', 'new output')), patch.object(Path, 'replace', checked_replace):
            runner.cache_logs(self.queue)
        self.assertEqual(runner.logs(self.queue, ID), {'stdout': 'new output', 'stderr': 'new output'})

    def test_stdout_workload_contract_uses_a_new_local_image_tag(self):
        self.assertEqual(runner.IMAGES['cpu'], 'lhr/job-runtime:2026-11-14-stdout-v2')
        self.assertEqual(runner.IMAGES['fmu'], runner.IMAGES['cpu'])


class TaskTest(unittest.TestCase):
    def test_cpu_uses_counter_clock_without_elapsed_time(self):
        clock = iter([0, 0, 1, 1])
        result = task.cpu(1, clock=lambda: next(clock))
        self.assertGreater(result['events'], 0)
        self.assertEqual(result['wall_s'], 1)
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_fmu_arguments_completion_weighting_and_serialization(self):
        row = {'label': 'worker-2', 'sim_s': 12, 'idle': {'mean_ms': 1}, 'accel': {'mean_ms': 2}, 'cruise': {'mean_ms': 3}, 'brake': {'mean_ms': 4}}
        completed = subprocess.CompletedProcess([], 0, 'solver warning\n' + json.dumps(row), 'diagnostic')
        with patch.object(task.subprocess, 'run', return_value=completed) as run, redirect_stdout(io.StringIO()) as output:
            result = task.fmu(2)
            run.assert_called_once_with([sys.executable, '/bench/input/fmu_bench.py', '1e-6', 'worker-2', '/bench/OrionFMI.fmu'], capture_output=True, text=True, timeout=600, check=False)
        self.assertIn('diagnostic', output.getvalue())
        self.assertEqual(result['weighted_mean_ms'], 1450 / 600)
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_fmu_rejects_exit_failure_adapter_error_incomplete_profile_and_missing_label(self):
        for code, row in [(1, {'label': 'worker-1', 'sim_s': 12}), (0, {'label': 'worker-1', 'sim_s': 12, 'error': 'adapter failed'}), (0, {'label': 'worker-1', 'sim_s': 11}), (0, {'label': 'other', 'sim_s': 12})]:
            with self.subTest(code=code, row=row), patch.object(task.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, json.dumps(row), 'diagnostic')), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'did not complete'):
                    task.fmu(1)
        with patch.object(task.subprocess, 'run', side_effect=subprocess.TimeoutExpired('benchmark', 600)):
            with self.assertRaises(subprocess.TimeoutExpired):
                task.fmu(1)


if __name__ == '__main__':
    unittest.main()
