"""Fixed-template Docker job runner; SSH RPC and worker share a durable SQLite queue."""

import argparse
import base64
import datetime
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time


TERMINAL = {'succeeded', 'failed', 'cancelled'}
LIVE = {'starting', 'running', 'cancelling'}
IMAGES = {
    'cpu': 'lhr/job-runtime:2026-11-14',
    'fmu': 'lhr/job-runtime:2026-11-14',
    'gpu': 'lhr/worker-gpu-check:2026-11-13',
}
MAX_ARCHIVE = 10 * 1024 * 1024


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate(request):
    """Only bounded workload settings cross the SSH boundary, never commands or paths."""
    if not isinstance(request, dict) or set(request) - {'id', 'template', 'parallel', 'seconds'}:
        raise ValueError('Unknown job settings')
    job_id = request.get('id', '')
    if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job ID')
    template = request.get('template')
    if template not in IMAGES:
        raise ValueError('Choose CPU, FMU or GPU')
    parallel, seconds = request.get('parallel', 1), request.get('seconds', 15)
    if type(parallel) is not int or not 1 <= parallel <= 8:
        raise ValueError('Parallelism must be 1 to 8')
    if type(seconds) is not int or not 5 <= seconds <= 120:
        raise ValueError('CPU duration must be 5 to 120 seconds')
    if template == 'gpu' and parallel != 1:
        raise ValueError('GPU checks reserve the whole GPU')
    return {'id': job_id, 'template': template, 'parallel': parallel, 'seconds': seconds}


def container_name(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job ID')
    return 'lhr-job-' + job_id


def command(settings, directory):
    settings = validate(settings)
    template, parallel = settings['template'], settings['parallel']
    args = ['docker', 'run', '-d', '--name', container_name(settings['id']),
            '--label', 'lhr.runner=jobs-v1', '--network', 'none',
            '--cpus', str(parallel), '--memory', str(512 + 512 * parallel) + 'm',
            '--pids-limit', '256', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges', '--read-only', '--mount',
            'type=tmpfs,destination=/tmp,tmpfs-size=536870912', '--log-driver', 'local',
            '--log-opt', 'max-size=2m', '--log-opt', 'max-file=2',
            '--mount', f'type=bind,src={directory},dst=/results',
            '-e', 'OPENBLAS_NUM_THREADS=1', '-e', 'OMP_NUM_THREADS=1']
    if template == 'gpu':
        args += ['--gpus', 'all', '-e', 'NVIDIA_DRIVER_CAPABILITIES=graphics,utility',
                 IMAGES[template]]
    else:
        args += [IMAGES[template], 'python3', '/runner/task.py', template,
                 '--parallel', str(parallel), '--seconds', str(settings['seconds'])]
    return args


class Queue:
    def __init__(self, root, now=timestamp):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'jobs').mkdir(exist_ok=True)
        self.now = now
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, settings TEXT NOT NULL, status TEXT NOT NULL,
                created TEXT NOT NULL, started TEXT, finished TEXT, exit_code INTEGER,
                error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')

    def connect(self):
        db = sqlite3.connect(self.root / 'jobs.db', timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def jobs(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs ORDER BY created DESC, rowid DESC LIMIT 250').fetchall()
        return [{**dict(row), 'settings': json.loads(row['settings'])} for row in rows]

    def get(self, job_id):
        container_name(job_id)
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise ValueError('Job not found')
        return {**dict(row), 'settings': json.loads(row['settings'])}

    def submit(self, request):
        settings = validate(request)
        encoded = json.dumps(settings, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT settings FROM jobs WHERE id=?', (settings['id'],)).fetchone()
            if row:
                if row['settings'] != encoded:
                    raise ValueError('That request ID already has different settings')
            else:
                active = db.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('succeeded','failed','cancelled')").fetchone()[0]
                if active >= 30:
                    raise ValueError('Queue is full (30 pending/running jobs)')
                if shutil.disk_usage(self.root).free < 2 * 1024**3:
                    raise ValueError('HIL has less than 2 GiB free; new jobs paused')
                db.execute('INSERT INTO jobs(id,settings,status,created) VALUES(?,?,?,?)',
                           (settings['id'], encoded, 'queued', self.now()))
        return self.get(settings['id'])

    def update(self, job_id, **values):
        allowed = {'status', 'started', 'finished', 'exit_code', 'error', 'cancel_requested'}
        if not values or set(values) - allowed:
            raise ValueError('Invalid update')
        with self.connect() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                       (*values.values(), job_id))

    def cancel(self, job_id):
        container_name(job_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('Job not found')
            if row['status'] == 'queued':
                db.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,finished=? WHERE id=?", (self.now(), job_id))
            elif row['status'] not in TERMINAL:
                # Keep starting/running until the worker has actually stopped the container.
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job_id,))
        return self.get(job_id)

    def paused(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(row and row['value'] == '1')

    def pause(self, paused):
        if type(paused) is not bool:
            raise ValueError('paused must be true or false')
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('paused',?)", ('1' if paused else '0',))
        return {'paused': paused}

    def directory(self, job_id):
        self.get(job_id)
        return self.root / 'jobs' / job_id


def docker(args, check=True):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=25, check=check)


def inspect(job_id):
    result = docker(['inspect', container_name(job_id)], check=False)
    if result.returncode:
        if 'No such' in result.stderr:
            return None
        raise RuntimeError('Docker is unavailable: ' + result.stderr[-200:])
    return json.loads(result.stdout)[0]


def capture(queue, job_id):
    directory = queue.directory(job_id)
    directory.mkdir(exist_ok=True)
    result = docker(['logs', '--tail', '2000', container_name(job_id)], check=False)
    for name, content in [('stdout.log', result.stdout[-262144:]),
                          ('stderr.log', result.stderr[-262144:]),
                          ('job.json', json.dumps(queue.get(job_id), indent=2))]:
        target = directory / name
        target.unlink(missing_ok=True)
        with target.open('x') as stream:
            stream.write(content)


def tick(queue):
    jobs = queue.jobs()
    for job in jobs:
        if job['status'] not in LIVE:
            continue
        info = inspect(job['id'])
        if info is None:
            if job['cancel_requested']:
                queue.update(job['id'], status='cancelled', finished=queue.now())
            elif job['status'] == 'starting':
                queue.update(job['id'], status='queued')
            else:
                queue.update(job['id'], status='failed', finished=queue.now(), error='Job container is missing')
            continue
        state = info['State']
        age = time.time() - datetime.datetime.fromisoformat(job['started']).timestamp()
        if state['Running'] and (job['cancel_requested'] or age > 900):
            queue.update(job['id'], status='cancelling', error='Runtime limit (15 min)' if age > 900 else None)
            docker(['stop', '-t', '5', container_name(job['id'])], check=False)
            info = inspect(job['id'])
            state = info['State']
        if not state['Running']:
            cancelled = job['cancel_requested'] or job['status'] == 'cancelling' or age > 900
            queue.update(job['id'], status='cancelled' if cancelled else ('succeeded' if state['ExitCode'] == 0 else 'failed'),
                         finished=queue.now(), exit_code=state['ExitCode'],
                         error='Container exceeded its memory limit' if state.get('OOMKilled') else ('Runtime limit (15 min)' if age > 900 else job['error']))
            capture(queue, job['id'])
        elif job['status'] == 'starting':
            queue.update(job['id'], status='running')
    if queue.paused():
        return
    jobs = queue.jobs()
    active = [j for j in jobs if j['status'] in LIVE]
    slots = sum(j['settings']['parallel'] for j in active)
    gpu_busy = any(j['settings']['template'] == 'gpu' for j in active)
    for job in reversed(jobs):
        if job['status'] != 'queued' or len(active) >= 4:
            continue
        settings = job['settings']
        if slots + settings['parallel'] > 8 or (settings['template'] == 'gpu' and gpu_busy):
            continue
        with queue.connect() as db:
            claimed = db.execute("UPDATE jobs SET status='starting',started=? WHERE id=? AND status='queued' AND cancel_requested=0", (queue.now(), job['id'])).rowcount
        if not claimed:
            continue
        directory = queue.directory(job['id'])
        directory.mkdir(exist_ok=True)
        try:
            subprocess.run(command(settings, directory), capture_output=True, text=True, timeout=40, check=True)
            queue.update(job['id'], status='running')
            active.append(job)
            slots += settings['parallel']
            gpu_busy |= settings['template'] == 'gpu'
        except (subprocess.SubprocessError, OSError) as error:
            # A timeout can occur after Docker has started; reconcile by deterministic name.
            if inspect(job['id']) is None:
                detail = getattr(error, 'stderr', '') or str(error)
                queue.update(job['id'], status='failed', finished=queue.now(), error=detail[-1000:])


def prune(queue):
    finished = [j for j in queue.jobs() if j['status'] in TERMINAL]
    for job in finished[200:]:
        docker(['rm', container_name(job['id'])], check=False)
        shutil.rmtree(queue.directory(job['id']), ignore_errors=True)
        with queue.connect() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job['id'],))


def logs(queue, job_id):
    job = queue.get(job_id)
    directory = queue.directory(job_id)
    if job['status'] in TERMINAL:
        return {stream: (directory / (stream + '.log')).read_text()[-262144:]
                if (directory / (stream + '.log')).is_file()
                and not (directory / (stream + '.log')).is_symlink() else '' for stream in ['stdout', 'stderr']}
    if job['status'] == 'queued':
        return {'stdout': '', 'stderr': ''}
    result = docker(['logs', '--tail', '250', container_name(job_id)], check=False)
    return {'stdout': result.stdout[-262144:], 'stderr': result.stderr[-262144:]}


def archive(queue, job_id):
    job = queue.get(job_id)
    if job['status'] not in TERMINAL:
        raise ValueError('Results are available after the job finishes')
    output = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        metadata = json.dumps(job, indent=2).encode()
        item = tarfile.TarInfo('job.json')
        item.size = len(metadata)
        tar.addfile(item, io.BytesIO(metadata))
        for path in sorted(queue.directory(job_id).glob('*')):
            if path.name == 'job.json' or path.is_symlink() or not path.is_file():
                continue
            if path.suffix not in {'.json', '.jsonl', '.log', '.csv', '.txt'}:
                continue
            with path.open('rb') as stream:
                content = stream.read(2 * 1024 * 1024)
            total += len(content)
            if total > MAX_ARCHIVE:
                raise ValueError('Result archive exceeds 10 MiB')
            item = tarfile.TarInfo(path.name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
    return {'filename': 'lhr-' + job_id + '.tar.gz', 'base64': base64.b64encode(output.getvalue()).decode()}


def rpc(queue, request):
    if not isinstance(request, dict) or set(request) - {'op', 'job', 'id', 'paused'}:
        raise ValueError('Invalid request')
    op = request.get('op')
    if op == 'submit':
        return queue.submit(request.get('job'))
    if op == 'cancel':
        return queue.cancel(request.get('id', ''))
    if op == 'pause':
        return queue.pause(request.get('paused'))
    if op == 'logs':
        return logs(queue, request.get('id', ''))
    if op == 'archive':
        return archive(queue, request.get('id', ''))
    if op == 'status':
        root_usage = shutil.disk_usage(queue.root)
        templates = {}
        for template, image in IMAGES.items():
            templates[template] = docker(['image', 'inspect', image], check=False).returncode == 0
        try:
            gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                                  '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            gpu = ''
        heartbeat = queue.root / 'heartbeat'
        healthy = heartbeat.exists() and time.time() - heartbeat.stat().st_mtime < 60
        return {'host': 'hil', 'online': healthy, 'paused': queue.paused(), 'jobs': queue.jobs(),
                'templates': templates, 'gpu': gpu, 'cpu_threads': os.cpu_count(),
                'load': list(os.getloadavg()), 'disk_free_bytes': root_usage.free,
                'limits': {'cpu_slots': 8, 'concurrent_jobs': 4, 'gpu_jobs': 1, 'retained_jobs': 200}}
    raise ValueError('Unknown operation')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['worker', 'rpc'])
    parser.add_argument('--root', default='/var/lib/lhr-runner')
    args = parser.parse_args()
    queue = Queue(args.root)
    if args.mode == 'rpc':
        try:
            data = sys.stdin.buffer.read(8193)
            if len(data) > 8192:
                raise ValueError('Request is too large')
            result = rpc(queue, json.loads(data))
            print(json.dumps({'ok': True, 'result': result}))
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(json.dumps({'ok': False, 'error': str(error)[:1000]}))
            raise SystemExit(1) from error
        return
    with (queue.root / 'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                tick(queue)
                prune(queue)
                (queue.root / 'heartbeat').touch()
            except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
                print(json.dumps({'worker_error': str(error)[:1000]}), flush=True)
            time.sleep(2)


if __name__ == '__main__':
    main()
