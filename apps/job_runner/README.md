# HIL jobs

A small PM2-managed Node application on telemetry commands a fixed-template
Docker runner on HIL over SSH. It has no npm runtime dependencies. The browser
can queue jobs, view status/output, cancel, pause new placements, and download
result archives. Nomad is not used.

## Components

| Component | Location | Supervision |
| --- | --- | --- |
| Dashboard and API | `/mnt/server_ssd/lhr-jobs/app` on telemetry | PM2 app `lhr-jobs` under `electric`, Node >=18 |
| Dashboard configuration/key | `~/.config/lhr-jobs` on telemetry | User-only files; never committed |
| Runner executable | `/opt/lhr-runner` on HIL | Root-owned code, systemd runs as `lhr-runner` |
| Queue, logs and results | `/var/lib/lhr-runner` on HIL | SQLite WAL and per-run directories |
| Downloaded result cache | `/mnt/server_ssd/lhr-jobs/data` on telemetry | At most 200 archives and 128 MiB |

The SSH key on telemetry can invoke only the root-owned bridge as `lhr-runner`,
only from telemetry's Tailscale address. The bridge accepts bounded JSON
operations, not a shell command. Its narrowly scoped sudo rule invokes only that
bridge. The runner exposes no network listener or Docker TCP port. Docker socket
access stays on HIL. Browser credentials and worker SSH credentials are
separate; neither is sent to jobs.

The initial deployment listens on `127.0.0.1:8091` only. Use an SSH tunnel
from the operator's machine:

```bash
ssh -N -L 127.0.0.1:8091:127.0.0.1:8091 electric@telemetry
```

Open `http://127.0.0.1:8091`; `publicOrigin` must match that local URL. No new
Tailscale Serve listener or domain route is installed. Telemetry's existing
public Funnel on 443, viewer, Grafana, ingestion and database services keep
their routes. A separate HTTPS listener can be configured later when requested.

## Workloads and limits

- **CPU smoke:** 1 to 8 processes, 5 to 120 seconds each. A Python prime-number
  workload; it is not the earlier sysbench comparison.
- **FMU timing:** 1 to 8 independent copies of the original 600-step pedal
  profile, tolerance `1e-6`. Uses the existing native x86 rebuild and original
  adapter. Checks every profile reaches 12 simulated seconds without an
  adapter error. Nonlinear-solver warnings remain in the logs; this is not
  full Gazebo validation. See the
  [recorded red-mesa campaign](../../autonomy/testing/2026-11-07-fmu-gazebo-red-mesa/README.md).
- **GPU rendering:** the existing NVIDIA EGL/pixel preflight, reserving the
  whole GPU. This verifies rendering, not Gazebo performance.
- **Gazebo campaigns:** not enabled until a validated image and isolation
  profile are available. The UI labels this explicitly.

The worker allows eight CPU slots, four concurrent jobs and one GPU job. Each
container gets a CPU quota matching its process count, 512 MiB plus 512 MiB per
process, a 512 MiB temporary filesystem and at most 256 processes. Containers
have no network, a read-only root filesystem, dropped capabilities, no Docker
socket or writable host mounts. Swap is disabled by setting the memory-plus-swap
limit equal to the memory limit. Missing local images fail with `--pull=never`;
a job never pulls a replacement from Docker Hub. GPU access is granted only to
the GPU template. Jobs stop after 15 minutes. FMU scratch is explicitly
executable because FMPy extracts native shared libraries there before loading
them. It remains a 512 MiB in-memory mount with `nosuid,nodev`; CPU/GPU
templates retain Docker's default non-executable scratch mount.

The worker process itself has a 256 MiB memory cap and 25% of one CPU; Docker
job limits are independent. PM2 restarts the dashboard above 256 MiB RSS and
Node's old generation is limited to 128 MiB and its semi-space to 8 MiB.
A PM2 restart threshold is not a hard
operating-system memory or CPU quota. Measure actual usage with `pm2 list` and
`systemctl status lhr-runner`.

At most 30 jobs may be queued/running and 200 finished jobs are retained on HIL.
Logs rotate (2 MiB, two files per container); downloaded archives contain only
JSON, JSONL, CSV, text and logs, with at most 2 MiB per file and 10 MiB of
source content total. Archives are cached on telemetry's SSD, with at most 200
retained job archives and 128 MiB total. One download is processed at a time to
bound archive decoding memory and serialize cache pruning. New jobs are refused
below 2 GiB free on HIL. Do not put outputs under HIL's unconfigured `/data`
mount.

SQLite is authoritative for queue/history. Each transaction closes its owned
connection after commit or rollback, so the long-running worker does not depend
on garbage collection to release database file handles. IDs make retrying a submission
idempotent. Worker restarts reconcile deterministic container names instead of
starting duplicates. Dashboard restarts or SSH disconnects do not stop
containers. Cancellation is reported complete only after Docker confirms the
container stopped. Pausing prevents new starts while existing jobs continue. The
runtime limit applies only to containers observed still running; a delayed
worker check preserves an already-exited container's actual result. Normal
completion becomes terminal and downloadable only after log capture succeeds.
Failed captures are retried on later worker ticks. After at least three
consecutive reconciliation failures over a two-minute retry window, the worker
removes any surviving container before failing the job with an explicit
diagnostic; failed captures never report successful completion. Each job is
reconciled independently, so a damaged result cannot indefinitely block another
job's runtime enforcement. Pruning/status errors do not suppress the worker
heartbeat, but online status also requires a recent successful Docker health
sample. Log cache updates use atomic replacement. Archive metadata uses the
final queue state. A timed-out launch reserves CPU/concurrency/GPU capacity even
when Docker has only created the container. Created/restarting containers must
start within 60 seconds or are removed and failed. Each worker cycle also
removes labelled containers whose job is missing or no longer live, catching
containers that appear late. Launch stderr remains in the job record. Pause
state is checked atomically as each queued job is claimed.

Results come from Docker's bounded rotating stdout/stderr logs. CPU/FMUs emit a
final JSON summary, which the worker saves as `results.json`; FMU diagnostics
are included in stdout. The workloads have no writable host output mount, so
they cannot fill HIL's disk through `/results`. Detailed older output may rotate
out; these are smoke/timing templates, not general artifact-producing studies.

The initial dashboard runs as the existing `electric` account alongside
`viewer_tool`, as recorded in [ADR-020](../../docs/architecture/020-shared-telemetry-dashboard-account.md).
It inherits that account's existing Docker/admin access. Its separate worker
key logs in as `lhr-job-ssh`, which may invoke only the bridge as
`lhr-runner`. The worker's Docker group is administrator-equivalent on HIL; this
is a trusted service account, not a Docker privilege boundary. At most four
bridge RPCs and two dashboard SSH requests run concurrently. Eight additional
dashboard requests may wait for capacity; overflow returns 429. A busy state
read keeps the last connected snapshot labelled as cached, rather than reporting
a connection failure. Hardware/image status is sampled every 30 seconds and live
logs each worker cycle; both are sampled by the supervised worker; read RPCs do
not invoke Docker or NVIDIA. The dashboard coalesces log reads for 2.5 seconds.
Sign-in checks allow five failed credential checks per source per minute; cached
valid logins bypass that limit. Empty credentials always receive a 401 browser
challenge without using an attempt. SSH-tunneled browser sessions share
telemetry's loopback rate limit. When Tailscale Serve is configured as the
trusted loopback proxy, the forwarded source IP gives each client its own
limit. If HIL cannot be reached, the dashboard labels
cached history as disconnected and disables new submissions.

## Development and checks

```bash
node --test apps/job_runner/*.test.mjs
python3 -m unittest discover -s apps/job_runner -p 'test_*.py'
bazel test //apps/job_runner:runner_test
uv run --group lint ruff check apps/job_runner
uv run --group docs mkdocs build --strict
```

Unit tests are hermetic: fake the SSH/Docker boundary, use temporary files, and
inject fixed times. They cover bounds/injection, authentication, CSRF,
idempotency, pause/cancel, restart reconciliation, resource slots and archive
symlinks, launch timeouts, per-job failures, transport decoding/limits, login
throttling, archive retention, browser focus, stale-state controls, workload
switching, fixed polling and native PM2 entrypoint startup. CPU and FMU tests fake clocks and subprocesses
without real benchmark files. Linux runs the runner tests through Bazel; CI also
runs Node tests. Real host/container tests are a separate deployment check,
never unit tests.

Dashboard configuration is a mode-0600 JSON file selected by `LHR_JOBS_CONFIG`.
Required settings: `username`, `passwordSalt`, `passwordHash` (32-byte scrypt
output encoded as hex), `publicOrigin`, `sshHost`, `sshKey`, `knownHosts`,
`dataDir`; optional `port` (default 8091). Use a unique salt. The worker host
key must be verified before installing it in `knownHosts`; do not disable strict
host verification.

## Deployment and rollback

Deployment source and service templates are described in
[deploy](deploy/README.md). Keep keys, password hashes, databases, model
binaries and outputs outside the checkout. Pin the source FMU and base runtime
image in the deployment record before enabling a new workload. The PM2
template executes Node directly in fork mode: PM2's JavaScript wrapper changes
`argv[1]` and otherwise bypasses this app's ESM entrypoint guard.

To stop this service without touching telemetry: `pm2 stop lhr-jobs` on
telemetry, pause the worker queue, and `sudo systemctl stop lhr-runner` on HIL.
Stopping the worker alone leaves already-running job containers running; cancel
those jobs first if they should stop. Close the operator's SSH tunnel when
withdrawing local browser access. Do not reset Tailscale Serve or stop unrelated
Compose projects.
