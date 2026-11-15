# Deployment templates

The [initial deployment record](2026-11-14.md) lists verified live-host checks,
current access and rollback.

`lhr-runner.service` supervises the queue on HIL. From the repository root on
HIL, install the root-owned runner (0644), executable bridge (0755), and unit
(0644) explicitly:

```bash
sudo install -d -o root -g root -m 0755 /opt/lhr-runner
sudo install -o root -g root -m 0644 apps/job_runner/runner.py /opt/lhr-runner/runner.py
sudo install -o root -g root -m 0755 apps/job_runner/deploy/bridge /opt/lhr-runner/bridge
sudo install -o root -g root -m 0644 apps/job_runner/deploy/lhr-runner.service /etc/systemd/system/lhr-runner.service
```

Create a system account `lhr-runner` with group `lhr-runner`, a non-login shell,
and membership in `docker`; install `/var/lib/lhr-runner` owned by that account
with mode 0700. The unit uses `StateDirectory=lhr-runner` too. Docker group
membership is effectively administrator access: keep executable files root-owned
and never give that group to the SSH or dashboard account. The service and its
four permitted concurrent RPC processes run as `lhr-runner`, rather than root.
RPCs read cached status/logs and SQLite; only the worker invokes Docker/NVIDIA.
The process cap applies across SSH sessions, independently of the dashboard's
two-request transport cap.

Use `/var/lib/lhr-runner` for state. The bridge must be executable for sudo's
forced SSH command to work. It runs Python in isolated mode and never interprets
`SSH_ORIGINAL_COMMAND`.

Generate a dedicated worker key as `electric` on telemetry. On HIL, create a
separate `lhr-job-ssh` account with a locked password, a valid `/bin/sh` login
shell for the forced command, and no Docker group or general sudo access. Its
only authorized_keys entry must have
`from="<telemetry-tailnet-ip>",restrict,command="sudo -n -u lhr-runner
/opt/lhr-runner/bridge"` before the public key (all on one line). Replace the
address placeholder with the source host's verified tailnet address; do not
publish it in this repo. The sudoers rule permits only `/opt/lhr-runner/bridge`
with no arguments. Validate that rule with `visudo -cf` before installing it.
The dashboard must not use the user's unrestricted administrator key.

`Runtime.Dockerfile` builds on the already installed benchmark runtime on HIL.
Its context contains `task.py`, the prior campaign's `input/` directory and
`OrionFMI.fmu`. The native FMU hash is
`b6d3d4ddc8c451d421a95704951f642ea0a8eaf17414f85633d57e4ff5aa71c9`. The existing
`lhr/worker-gpu-check:2026-11-13` image supplies the GPU template. No private
FMU needs to leave HIL to construct the image.

`ecosystem.config.cjs` defines one PM2 application, `lhr-jobs`. The initial
deployment uses the existing `electric` account and PM2 daemon, alongside
`viewer_tool`; this intentionally shares that account's existing privileges
([ADR-020](../../../docs/architecture/020-shared-telemetry-dashboard-account.md)).
The application code stays root-owned on telemetry's SSD. Its data directory,
mode-0600 configuration/key and verified known_hosts belong to `electric`.
The default config path is `/home/electric/.config/lhr-jobs/dashboard.json`;
set `LHR_JOBS_CONFIG` before invoking PM2 to override it.

The template starts the current Node executable as a native process in fork
mode. Do not replace it with PM2's JavaScript import wrapper: that changes
`argv[1]`, so the server's ESM entrypoint guard never starts the HTTP listener.
Old-space and semi-space limits are passed directly to Node. Start or restart
only `lhr-jobs`; never delete or restart `viewer_tool`. When saving PM2's
process list, preserve both applications and the existing viewer configuration.

For now, listen only on `127.0.0.1:8091` and set `publicOrigin` to
`http://127.0.0.1:8091`. Use an SSH tunnel from the operator's machine:

```bash
ssh -N -L 127.0.0.1:8091:127.0.0.1:8091 electric@telemetry
```

No new Serve/Funnel route or domain is part of this deployment. The existing
443 handler and Funnel flag stay unchanged. Configure a separate HTTPS origin
only when requested. The dashboard requires HTTP Basic authentication, uses
salted scrypt password verification and same-origin mutation checks, and never
logs credentials. Five failed credential checks per source per minute return
429; initial browser challenges do not consume an attempt.

Deployment acceptance: queue/run a CPU job, download its archive, cancel queued
and running jobs, pause/resume, restart the dashboard and worker during a job,
complete the FMU profile, verify NVIDIA output with a read-only root and no
`/results` host mount, and measure idle service usage. Confirm existing
telemetry container IDs/status and public routes remain unchanged. Record
results in the adjacent runbook after deployment.

Deployment records must verify installed image IDs, account/group
memberships, sudo restriction, cache/result bounds and worker failure recovery
on HIL before enabling the dashboard. The fixed templates publish compact
results and diagnostics on stdout/stderr; they cannot write to the host's result
directory. Rebuild the runtime image with the updated `task.py`; an older image
that writes `/results` is incompatible. Full Gazebo campaigns need a separate
output-storage and isolation design.

Build the stdout-based runtime under the new tag
`lhr/job-runtime:2026-11-14-stdout-v2`, for both CPU and FMU templates. Do not
reuse the older `2026-11-14` tag: it expects a writable `/results` mount. Record
the resulting local image ID before enabling jobs; the runner never pulls it.

The dashboard trusts source IP forwarding only from its loopback Tailscale Serve
proxy. Serve [sets the forwarded source
itself](https://github.com/tailscale/tailscale/blob/main/ipn/ipnlocal/serve.go),
overwriting client-supplied forwarding headers. Keep the application bound to
loopback, with Serve as the only external proxy; verify distinct clients arrive
with distinct source IPs during acceptance. Unauthenticated initial page loads
always get a 401 challenge without consuming a login attempt.

FMU jobs need executable scratch: FMPy extracts the model's native `.so` into
`/tmp` before loading it. The runner uses a 512 MiB-per-process `exec,nosuid,nodev` tmpfs
for this template only. The root filesystem stays read-only and CPU/GPU
scratch keeps Docker's default `noexec` setting. This fixes the live
"failed to map segment from shared object" error without a writable host mount.


## Versioned autonomy runtime

Build `Campaign.Dockerfile` from a clean feature-branch source archive containing
`autonomy/` and `apps/job_runner/`, with `SOURCE_REVISION` set to that commit:

```bash
sudo docker build --build-arg SOURCE_REVISION="$REVISION" \
  -f apps/job_runner/deploy/Campaign.Dockerfile -t lhr/autonomy-campaign:"$REVISION" .
sudo docker image inspect lhr/autonomy-campaign:"$REVISION" --format '{{.Id}}'
```

The image builds the ROS workspace and runs the existing gate/scoring tools.
The `gz` wrapper enables EGL headless rendering for the existing server launch;
it does not force software rendering. No X server or desktop is exposed.
The official ROS base digest is pinned; package versions and final image ID are
recorded during deployment. A rebuild may resolve newer apt packages, so retain
the accepted image ID for reproducible runs.

Replace the revision and image placeholders in `jobs.example.json`, validate
with `runner.definitions()`, and install as `/opt/lhr-runner/jobs.json`, owned by
root and mode 0644. Stage and validate a replacement before atomically renaming
it. Each job must declare explicit parameters/resources and an image entrypoint;
no arbitrary shell-command field is exposed in the browser. The worker reads
catalog changes without a restart, and checks image availability every 30 seconds.
Validate a new image with a private candidate catalog before installing the
production definition. Preserve previous images needed by queued jobs.

Campaign artifacts live under `/var/lib/lhr-runner/jobs/<id>/artifacts`, owned by
`lhr-runner`, retained with the job (200 finished jobs). This mount is the only
writable host directory available to a campaign. A 256 MiB / 512-file monitored
budget terminates oversized jobs; it is not a hard disk quota. Never use this
profile for untrusted uploads. Individual authenticated file downloads stream
chunks and do not populate telemetry's archive cache. The compact download keeps
its existing bounds. A separate filesystem quota is required before supporting
untrusted artifact-producing code.

Roll back code by restoring the saved runner and telemetry app and restarting
only `lhr-runner` / `lhr-jobs`. Remove a catalog entry to disable new submissions;
queued jobs retain their accepted definitions. Cancel a campaign before removing
its image. Keep dashboard credentials, configured origin, queue and worker key.
