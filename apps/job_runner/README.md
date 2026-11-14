# HIL jobs

A small PM2-managed Node application on telemetry commands a fixed-template
Docker runner on HIL over SSH. It has no npm runtime dependencies. The browser
can queue jobs, view status/output, cancel, pause new placements, and download
result archives. Nomad is not used.

## Components

| Component | Location | Supervision |
| --- | --- | --- |
| Dashboard and API | `/mnt/server_ssd/lhr-jobs/app` on telemetry | PM2 app `lhr-jobs`, Node >=18 |
| Dashboard configuration/key | `~/.config/lhr-jobs` on telemetry | User-only files; never committed |
| Runner executable | `/opt/lhr-runner` on HIL | Root-owned, systemd `lhr-runner` |
| Queue, logs and results | `/var/lib/lhr-runner` on HIL | SQLite WAL and per-run directories |
| Downloaded result cache | `/mnt/server_ssd/lhr-jobs/data` on telemetry | At most 200 retained archives |

The SSH key on telemetry can invoke only the root-owned bridge, only from
telemetry's Tailscale address. The bridge accepts bounded JSON operations,
not a shell command. Its narrowly scoped sudo rule invokes only that bridge.
The runner exposes no network listener or Docker TCP port. Docker socket
access stays on HIL. Browser credentials and worker SSH credentials are
separate; neither is sent to jobs.

The dashboard listens on `127.0.0.1:8091`. A separate private Tailscale Serve
HTTPS listener on port 8443 forwards to it. The existing public Funnel on 443,
telemetry viewer, Grafana, ingestion and database services keep their own
routes. Check `tailscale serve status --json` after changes: the new 8443
listener must not appear as enabled under `AllowFunnel`.

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

The worker allows eight CPU slots, four concurrent jobs and one GPU job.
Each container gets a CPU quota matching its process count, 512 MiB plus
512 MiB per process, a 512 MiB temporary filesystem and at most 256 processes.
Containers have no network, a read-only root filesystem, dropped capabilities,
no Docker socket and one fixed per-job output mount. GPU access is granted
only to the GPU template. Jobs stop after 15 minutes.

The worker process itself has a 256 MiB memory cap and 25% of one CPU; Docker
job limits are independent. PM2 restarts the dashboard above 256 MiB RSS and
Node's JS heap is limited to 128 MiB. A PM2 restart threshold is not a hard
operating-system memory or CPU quota. Measure actual usage with `pm2 list`
and `systemctl status lhr-runner`.

At most 30 jobs may be queued/running and 200 finished jobs are retained on
HIL. Logs rotate (2 MiB, two files per container); downloaded archives contain
only JSON, JSONL, CSV, text and logs, with at most 2 MiB per file and 10 MiB
of source content total. Archives are cached on telemetry's SSD, with at most
200 retained job archives. New jobs are refused below 2 GiB free on HIL.
Do not put outputs under HIL's unconfigured `/data` mount.

SQLite is authoritative for queue/history. IDs make retrying a submission
idempotent. Worker restarts reconcile deterministic container names instead
of starting duplicates. Dashboard restarts or SSH disconnects do not stop
containers. Cancellation is reported complete only after Docker confirms the
container stopped. Pausing prevents new starts while existing jobs continue.
If HIL cannot be reached, the dashboard labels cached history as disconnected
and disables new submissions.

## Development and checks

```bash
node --test apps/job_runner/server.test.mjs
python3 -m unittest discover -s apps/job_runner -p 'test_*.py'
bazel test //apps/job_runner:runner_test
uv run --group lint ruff check apps/job_runner
uv run --group docs mkdocs build --strict
```

Unit tests are hermetic: fake the SSH/Docker boundary, use temporary files,
and inject fixed times. They cover bounds/injection, authentication, CSRF,
idempotency, pause/cancel, restart reconciliation, resource slots and archive
symlinks. Linux runs the runner tests through Bazel; CI also runs Node tests.
Real host/container tests are a separate deployment check, never unit tests.

Dashboard configuration is a mode-0600 JSON file selected by
`LHR_JOBS_CONFIG`. Required settings: `username`, `passwordSalt`,
`passwordHash` (32-byte scrypt output encoded as hex), `publicOrigin`,
`sshHost`, `sshKey`, `knownHosts`, `dataDir`; optional `port` (default 8091).
Use a unique salt. The worker host key must be verified before installing it
in `knownHosts`; do not disable strict host verification.

## Deployment and rollback

Deployment source and service templates are described in
[deploy](deploy/README.md). Keep keys, password hashes, databases, model
binaries and outputs outside the checkout. Pin the source FMU and base runtime
image in the deployment record before enabling a new workload.

To stop this service without touching telemetry: `pm2 stop lhr-jobs` on
telemetry, pause the worker queue, and `sudo systemctl stop lhr-runner` on HIL.
Stopping the worker alone leaves already-running job containers running;
cancel those jobs first if they should stop. Remove only the 8443 Serve
listener when withdrawing browser access. Do not reset Tailscale Serve or
stop unrelated Compose projects.
