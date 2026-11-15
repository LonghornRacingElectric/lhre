"""Catalog-based Docker job runner; SSH RPC and worker share a durable SQLite queue."""

import argparse
import base64
import datetime
from contextlib import ExitStack, closing, contextmanager
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
    'cpu': 'lhr/job-runtime:2026-11-14-stdout-v2',
    'fmu': 'lhr/job-runtime:2026-11-14-stdout-v2',
    'gpu': 'lhr/worker-gpu-check:2026-11-13',
}
DEFINITIONS = Path(__file__).with_name('jobs.json')
MAX_ARTIFACT_BYTES = 256 * 1024**2
MAX_ARTIFACT_FILES = 512
MAX_ARCHIVE = 10 * 1024 * 1024
WORKER_ERRORS = (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError)


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def definitions():
    """Only administrators install definitions; browsers supply parameter values."""
    if not DEFINITIONS.exists():
        return {}
    entries = json.loads(DEFINITIONS.read_text())['jobs']
    result = {}
    for entry in entries:
        name = entry['id']
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', name) or name in IMAGES or name in result:
            raise ValueError('Invalid or duplicate job definition')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', entry['image']):
            raise ValueError('Job images must use immutable local image IDs')
        if not re.fullmatch(r'[a-f0-9]{40}', entry['revision']):
            raise ValueError('Job definitions must pin their source revision')
        for key, low, high in [('cpu_slots', 1, 8), ('memory_mib', 512, 16384),
                               ('tmp_mib', 64, 4096), ('timeout_s', 30, 3600)]:
            if type(entry[key]) is not int or not low <= entry[key] <= high:
                raise ValueError('Invalid job resource: ' + key)
        if type(entry['gpu']) is not bool or not isinstance(entry['command'], list):
            raise ValueError('Invalid job command or GPU reservation')
        for key, spec in entry['parameters'].items():
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', key):
                raise ValueError('Invalid parameter name')
            parameter(spec, spec['default'])
        for arg in entry['command']:
            if not isinstance(arg, str) or len(arg) > 1024:
                raise ValueError('Invalid job argument')
            for key in re.findall(r'\{([^{}]+)\}', arg):
                if key not in entry['parameters']:
                    raise ValueError('Unknown job argument parameter')
        result[name] = entry
    return result


def parameter(spec, value):
    if spec['type'] == 'integer':
        if type(value) is not int or not spec['min'] <= value <= spec['max']:
            raise ValueError(f"{spec['label']} must be {spec['min']} to {spec['max']}")
    elif spec['type'] == 'enum':
        if not isinstance(value, str) or value not in spec['choices']:
            raise ValueError('Invalid choice for ' + spec['label'])
    else:
        raise ValueError('Unsupported parameter type')
    return value


def custom_settings(request, catalog):
    if set(request) - {'id', 'template', 'parameters', 'revision'}:
        raise ValueError('Unknown job settings')
    definition = catalog[request['template']]
    if request.get('revision') != definition['revision']:
        raise ValueError('Job revision changed; refresh before submitting')
    values = request.get('parameters', {})
    if not isinstance(values, dict) or set(values) - definition['parameters'].keys():
        raise ValueError('Unknown job parameters')
    values = {key: parameter(spec, values.get(key, spec['default']))
              for key, spec in definition['parameters'].items()}
    return {'id': request['id'], 'template': request['template'],
            'parallel': definition['cpu_slots'], 'parameters': values,
            'definition': definition, 'revision': definition['revision']}


def needs_gpu(settings):
    return settings.get('definition', {}).get('gpu', settings['template'] == 'gpu')


def artifact_path(queue, job_id, name):
    # Resolve every component with O_NOFOLLOW, including intermediate directories.
    parts = name.split('/') if isinstance(name, str) else []
    if not parts or any(not part or part in {'.', '..'} or '\\' in part for part in parts):
        raise ValueError('Invalid artifact path')
    root = queue.directory(job_id) / 'artifacts'
    with ExitStack() as stack:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)


def artifacts(queue, job_id):
    root = queue.directory(job_id) / 'artifacts'
    items = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for filename in sorted(files):
            path = Path(directory) / filename
            if path.is_symlink() or not path.is_file():
                continue
            items.append({'path': str(path.relative_to(root)), 'bytes': path.stat().st_size})
            if len(items) > MAX_ARTIFACT_FILES:
                raise ValueError('Job exceeded 512 artifact files')
    return sorted(items, key=lambda item: item['path'])


def artifact_chunk(queue, job_id, name, offset):
    import stat
    if queue.get(job_id)['status'] not in TERMINAL:
        raise ValueError('Artifacts are available after the job finishes')
    if type(offset) is not int or offset < 0:
        raise ValueError('Invalid artifact offset')
    with os.fdopen(artifact_path(queue, job_id, name), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError('Invalid or oversized artifact')
        stream.seek(offset)
        return {'base64': base64.b64encode(stream.read(512 * 1024)).decode(), 'size': info.st_size}


def validate(request):
    """Only bounded workload settings cross the SSH boundary, never commands or paths."""
    if not isinstance(request, dict):
        raise ValueError('Unknown job settings')
    job_id = request.get('id', '')
    if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job ID')
    template = request.get('template')
    if template not in IMAGES:
        catalog = definitions()
        if not isinstance(template, str) or template not in catalog:
            raise ValueError('Choose an installed job type')
        return custom_settings(request, catalog)
    if set(request) - {'id', 'template', 'parallel', 'seconds'}:
        raise ValueError('Unknown job settings')
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
    definition = settings.get('definition')
    if definition:
        settings = custom_settings({k: settings[k] for k in ('id', 'template', 'parameters', 'revision')},
                                   {settings['template']: definition})
    else:
        settings = validate(settings)
    template, parallel = settings['template'], settings['parallel']
    args = ['docker', 'run', '-d', '--name', container_name(settings['id']),
            '--pull=never', '--label', 'lhr.runner=jobs-v1', '--network', 'none',
            '--cpus', str(parallel), '--memory', str(definition['memory_mib'] if definition else 512 + 512 * parallel) + 'm',
            '--memory-swap', str(definition['memory_mib'] if definition else 512 + 512 * parallel) + 'm',
            '--pids-limit', '1024' if definition else '256', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges', '--read-only', '--log-driver', 'local',
            '--log-opt', 'max-size=2m', '--log-opt', 'max-file=2',
            '-e', 'OPENBLAS_NUM_THREADS=1', '-e', 'OMP_NUM_THREADS=1']
    if definition:
        args += [f"--tmpfs=/tmp:rw,exec,nosuid,nodev,size={definition['tmp_mib'] * 1024**2}",
                 '--shm-size=512m', '--user', f'{os.getuid()}:{os.getgid()}',
                 '--mount', f'type=bind,source={Path(directory).resolve() / "artifacts"},destination=/results']
    elif template == 'fmu':
        # Each FMPy process extracts its own native libraries before loading them.
        scratch_bytes = 512 * 1024**2 * parallel
        args += [f'--tmpfs=/tmp:rw,exec,nosuid,nodev,size={scratch_bytes}']
    else:
        args += ['--mount', 'type=tmpfs,destination=/tmp,tmpfs-size=536870912']
    if needs_gpu(settings):
        args += ['--gpus', 'all', '-e', 'NVIDIA_DRIVER_CAPABILITIES=graphics,utility']
    if definition:
        args += [definition['image'], *[re.sub(r'\{([^{}]+)\}',
                 lambda m: str(settings['parameters'][m[1]]), arg) for arg in definition['command']]]
    elif template == 'gpu':
        args += [IMAGES[template]]
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

    @contextmanager
    def connect(self):
        # SQLite's transaction context commits/rolls back but does not close the connection.
        with closing(sqlite3.connect(self.root / 'jobs.db', timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

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


def atomic_text(target, content):
    temporary = target.with_name('.' + target.name + '.tmp')
    temporary.unlink(missing_ok=True)
    with temporary.open('x') as stream:
        stream.write(content)
    temporary.replace(target)


def remove_container(job_id):
    result = docker(['rm', '-f', container_name(job_id)], check=False)
    if result.returncode and 'No such' not in result.stderr:
        raise RuntimeError('Could not remove job container: ' + result.stderr[-200:])


def sweep_orphans(queue):
    result = docker(['ps', '-a', '--filter', 'label=lhr.runner=jobs-v1', '--format', '{{.Names}}'])
    safe = True
    for name in result.stdout.splitlines():
        if not re.fullmatch(r'lhr-job-[a-f0-9]{32}', name):
            continue
        job_id = name[len('lhr-job-'):]
        try:
            try:
                job = queue.get(job_id)
            except ValueError:
                job = None
            if job is None or job['status'] not in LIVE:
                remove_container(job_id)
        except WORKER_ERRORS as error:
            safe = False
            print(json.dumps({'job': job_id, 'worker_error': str(error)[:1000]}), flush=True)
    return safe


def capture(queue, job_id):
    directory = queue.directory(job_id)
    directory.mkdir(exist_ok=True)
    result = docker(['logs', '--tail', '2000', container_name(job_id)])
    # Compact summaries use stdout; campaign recordings remain in their output mount.
    for line in reversed(result.stdout.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and 'results' in record and 'template' in record:
            atomic_text(directory / 'results.json', json.dumps(record))
            break
    for name, content in [('stdout.log', result.stdout[-262144:]),
                          ('stderr.log', result.stderr[-262144:])]:
        atomic_text(directory / name, content)


def reconcile(queue, job):
    info = inspect(job['id'])
    if info is None:
        if job['cancel_requested']:
            remove_container(job['id'])
            queue.update(job['id'], status='cancelled', finished=queue.now())
        elif job['status'] == 'starting':
            age = time.time() - datetime.datetime.fromisoformat(job['started']).timestamp()
            if age > 60:
                remove_container(job['id'])
                queue.update(job['id'], status='failed', finished=queue.now(),
                             error=job['error'] or 'Container did not appear within 60 seconds')
        else:
            queue.update(job['id'], status='failed', finished=queue.now(), error='Job container is missing')
        return
    state = info['State']
    age = time.time() - datetime.datetime.fromisoformat(job['started']).timestamp()
    if state.get('Status') in {'created', 'restarting'}:
        if job['cancel_requested'] or age > 60:
            docker(['rm', '-f', container_name(job['id'])])
            queue.update(job['id'], status='cancelled' if job['cancel_requested'] else 'failed',
                         finished=queue.now(), error=job['error'] or 'Container did not start within 60 seconds')
        return
    timeout = job['settings'].get('definition', {}).get('timeout_s', 900)
    timeout_error = f'Runtime limit ({timeout} seconds)' if job['settings'].get('definition') else 'Runtime limit (15 min)'
    artifact_error = None
    try:
        if sum(item['bytes'] for item in artifacts(queue, job['id'])) > MAX_ARTIFACT_BYTES:
            artifact_error = 'Job exceeded 256 MiB of artifacts'
    except ValueError as error:
        artifact_error = str(error)
    timed_out = state['Running'] and age > timeout
    if state['Running'] and (job['cancel_requested'] or timed_out or artifact_error):
        # Enforce safety even if a full disk prevents recording the state transition.
        grace = '20' if job['settings'].get('definition') else '5'
        docker(['stop', '-t', grace, container_name(job['id'])], check=False)
        queue.update(job['id'], status='cancelling', error=artifact_error or (timeout_error if timed_out else None))
        info = inspect(job['id'])
        state = info['State']
    if not state['Running']:
        # A failed capture must leave the job live so the next tick retries;
        # terminal jobs are immediately eligible for cached result downloads.
        capture(queue, job['id'])
        cancelled = job['cancel_requested'] or job['status'] == 'cancelling' or timed_out
        queue.update(job['id'], status='cancelled' if cancelled else ('succeeded' if state['ExitCode'] == 0 and not artifact_error else 'failed'),
                     finished=queue.now(), exit_code=state['ExitCode'],
                     error='Container exceeded its memory limit' if state.get('OOMKilled') else (artifact_error or (timeout_error if timed_out else job['error'])))
    elif job['status'] == 'starting':
        queue.update(job['id'], status='running')


def cache_logs(queue):
    for job in queue.jobs():
        if job['status'] not in LIVE:
            continue
        try:
            result = docker(['logs', '--tail', '250', container_name(job['id'])])
            directory = queue.directory(job['id'])
            directory.mkdir(exist_ok=True)
            for name, content in [('stdout.log', result.stdout), ('stderr.log', result.stderr)]:
                atomic_text(directory / name, content[-262144:])
        except WORKER_ERRORS as error:
            print(json.dumps({'job': job['id'], 'log_error': str(error)[:1000]}), flush=True)


def tick(queue, allow_launch=True):
    for job in queue.jobs():
        if job['status'] not in LIVE:
            continue
        try:
            reconcile(queue, job)
            with queue.connect() as db:
                db.execute('DELETE FROM settings WHERE key IN (?,?)', ('errors:' + job['id'], 'error_since:' + job['id']))
        except WORKER_ERRORS as error:
            try:
                # One damaged result must not prevent cancellation/runtime enforcement for others.
                with queue.connect() as db:
                    key = 'errors:' + job['id']
                    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                    attempts = int(row['value']) + 1 if row else 1
                    failed_at = time.time()
                    since_key = 'error_since:' + job['id']
                    row = db.execute('SELECT value FROM settings WHERE key=?', (since_key,)).fetchone()
                    since = float(row['value']) if row else failed_at
                    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (since_key, str(since)))
                    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(attempts)))
                print(json.dumps({'job': job['id'], 'worker_error': str(error)[:1000]}), flush=True)
                if attempts >= 3 and failed_at - since >= 120:
                    try:
                        info = inspect(job['id'])
                        if info and (info['State']['Running'] or info['State'].get('Status') in {'created', 'restarting'}):
                            docker(['rm', '-f', container_name(job['id'])])
                        queue.update(job['id'], status='failed', finished=queue.now(),
                                     error=f'Reconciliation failed after {attempts} attempts over at least 120 seconds: ' + str(error)[-800:])
                    except WORKER_ERRORS as stop_error:
                        # Unknown Docker state still reserves resources and is retried next tick.
                        print(json.dumps({'job': job['id'], 'worker_error': str(stop_error)[:1000]}), flush=True)
            except WORKER_ERRORS as persistence_error:
                print(json.dumps({'job': job['id'], 'worker_error': str(persistence_error)[:1000]}), flush=True)
    if not allow_launch or queue.paused() or shutil.disk_usage(queue.root).free < 2 * 1024**3:
        return
    jobs = queue.jobs()
    active = [j for j in jobs if j['status'] in LIVE]
    slots = sum(j['settings']['parallel'] for j in active)
    gpu_busy = any(needs_gpu(j['settings']) for j in active)
    for job in reversed(jobs):
        if job['status'] != 'queued' or len(active) >= 4:
            continue
        settings = job['settings']
        if slots + settings['parallel'] > 8 or (needs_gpu(settings) and gpu_busy):
            continue
        with queue.connect() as db:
            claimed = db.execute("UPDATE jobs SET status='starting',started=? WHERE id=? AND status='queued' AND cancel_requested=0 AND NOT EXISTS (SELECT 1 FROM settings WHERE key='paused' AND value='1')", (queue.now(), job['id'])).rowcount
        if not claimed:
            continue
        directory = queue.directory(job['id'])
        directory.mkdir(exist_ok=True)
        if settings.get('definition'):
            (directory / 'artifacts').mkdir(exist_ok=True)
        try:
            subprocess.run(command(settings, directory), capture_output=True, text=True, timeout=40, check=True)
            queue.update(job['id'], status='running')
            active.append(job)
            slots += settings['parallel']
            gpu_busy |= needs_gpu(settings)
        except (subprocess.SubprocessError, OSError) as error:
            # A timeout can occur after Docker has started; reconcile by deterministic name.
            detail = getattr(error, 'stderr', '') or str(error)
            if isinstance(detail, bytes):
                detail = detail.decode(errors='replace')
            queue.update(job['id'], error=detail[-1000:])
            if inspect(job['id']) is None and not isinstance(error, subprocess.TimeoutExpired):
                queue.update(job['id'], status='failed', finished=queue.now())
            else:
                # Even a merely created container owns its reservations until reconciled.
                active.append(job)
                slots += settings['parallel']
                gpu_busy |= needs_gpu(settings)


def prune(queue):
    finished = [j for j in queue.jobs() if j['status'] in TERMINAL]
    for job in finished[200:]:
        result = docker(['rm', container_name(job['id'])], check=False)
        if result.returncode and 'No such' not in result.stderr:
            continue
        shutil.rmtree(queue.directory(job['id']), ignore_errors=True)
        with queue.connect() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job['id'],))
            db.execute('DELETE FROM settings WHERE key IN (?,?)', ('errors:' + job['id'], 'error_since:' + job['id']))


def logs(queue, job_id):
    job = queue.get(job_id)
    directory = queue.directory(job_id)
    if job['status'] in TERMINAL or job['status'] in LIVE:
        return {stream: (directory / (stream + '.log')).read_text()[-262144:]
                if (directory / (stream + '.log')).is_file()
                and not (directory / (stream + '.log')).is_symlink() else '' for stream in ['stdout', 'stderr']}
    return {'stdout': '', 'stderr': ''}


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
    if not isinstance(request, dict) or set(request) - {'op', 'job', 'id', 'paused', 'path', 'offset'}:
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
    if op == 'artifacts':
        return artifacts(queue, request.get('id', ''))
    if op == 'artifact':
        return artifact_chunk(queue, request.get('id', ''), request.get('path', ''), request.get('offset', 0))
    if op == 'status':
        try:
            snapshot = json.loads((queue.root / 'status.json').read_text())
        except (OSError, ValueError):
            snapshot = {'templates': {}, 'online': False}
        heartbeat = queue.root / 'heartbeat'
        snapshot.update(jobs=queue.jobs(), catalog=list(definitions().values()), paused=queue.paused(),
                        online=bool(snapshot.get('online')) and heartbeat.exists()
                        and time.time() - heartbeat.stat().st_mtime < 60
                        and 0 <= time.time() - snapshot.get('sampled_at', 0) < 60)
        return snapshot
    raise ValueError('Unknown operation')


def cache_status(queue):
    target = queue.root / 'status.json'
    sampled_at = time.time()
    try:
        previous = json.loads(target.read_text())
        if 0 <= sampled_at - previous.get('sampled_at', 0) < 30:
            return
    except (OSError, ValueError):
        pass
    root_usage = shutil.disk_usage(queue.root)
    templates, gpu, online, error = {}, '', False, None
    try:
        docker(['info', '--format', '{{.ServerVersion}}'])
        online = True
        images = {image: docker(['image', 'inspect', image], check=False).returncode == 0
                  for image in sorted(set(IMAGES.values()) | {d['image'] for d in definitions().values()})}
        templates = {template: images[image] for template, image in IMAGES.items()}
        templates.update({name: images[d['image']] for name, d in definitions().items()})
        try:
            gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                                  '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            gpu = ''
    except WORKER_ERRORS as failure:
        online, error = False, str(failure)[:1000]
    snapshot = {'host': 'hil', 'online': online, 'sampled_at': sampled_at, 'error': error,
                'templates': templates, 'gpu': gpu, 'cpu_threads': os.cpu_count(),
                'load': list(os.getloadavg()), 'disk_free_bytes': root_usage.free,
                'limits': {'cpu_slots': 8, 'concurrent_jobs': 4, 'gpu_jobs': 1, 'retained_jobs': 200}}
    atomic_text(target, json.dumps(snapshot))


def worker_cycle(queue):
    try:
        allow_launch = sweep_orphans(queue)
    except WORKER_ERRORS as error:
        allow_launch = False
        print(json.dumps({'worker_error': str(error)[:1000]}), flush=True)
    for operation in (lambda q: tick(q, allow_launch=allow_launch), prune, cache_status, cache_logs):
        try:
            operation(queue)
        except WORKER_ERRORS as error:
            print(json.dumps({'worker_error': str(error)[:1000]}), flush=True)
    (queue.root / 'heartbeat').touch()


def main():
    global DEFINITIONS
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['worker', 'rpc', 'check-catalog'])
    parser.add_argument('--root', default='/var/lib/lhr-runner')
    parser.add_argument('--catalog', default=str(DEFINITIONS))
    args = parser.parse_args()
    DEFINITIONS = Path(args.catalog)
    if args.mode == 'check-catalog':
        print(json.dumps(list(definitions().values()), indent=2))
        return
    with ExitStack() as locks:
        if args.mode == 'rpc':
            Path(args.root).mkdir(parents=True, exist_ok=True)
            for slot in range(4):
                lock = locks.enter_context((Path(args.root) / f'rpc-{slot}.lock').open('w'))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    lock.close()
            else:
                print(json.dumps({'ok': False, 'error': 'Runner is busy; retry shortly'}))
                raise SystemExit(1)
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
                worker_cycle(queue)
                time.sleep(2)


if __name__ == '__main__':
    main()
