# Deployment templates

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

Generate a dedicated worker key as `lhr-jobs` on telemetry. On HIL, create a
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

`ecosystem.config.cjs` defines a single PM2 application, `lhr-jobs`. Run it as a
dedicated `lhr-jobs` account, without Docker membership or sudo privileges; do
not reuse `electric` or its PM2 daemon. Install the application on telemetry's
SSD with its data directory owned by `lhr-jobs`, create the mode-0600
configuration/key and verified known_hosts under
`/home/lhr-jobs/.config/lhr-jobs`, and set `sshHost` to `lhr-job-ssh@hil`. Start
and persist only that account's PM2 process list. Keep existing `electric`
services and their PM2 configuration unchanged.

Add `tailscale serve --bg --https=8444 http://127.0.0.1:8091`. Preserve the
existing 443 handler and Funnel flag, and verify the new port is private. The
dashboard uses HTTP Basic authentication over that HTTPS connection, salted
scrypt password verification, same-origin mutation checks and a restrictive
content security policy. The backend does not log credentials. It permits five
failed sign-ins per minute globally before returning 429; cached valid
credentials keep working. The port deliberately lies outside Funnel's supported
ports; verify Serve status before handing out access.

Deployment acceptance: queue/run a CPU job, download its archive, cancel queued
and running jobs, pause/resume, restart the dashboard and worker during a job,
complete the FMU profile, verify NVIDIA output with a read-only root and no
`/results` host mount, and measure idle service usage. Confirm existing
telemetry container IDs/status and public routes remain unchanged. Record
results in the adjacent runbook after deployment.

No service is deployed by this PR. Verify the installed image IDs, account/group
memberships, sudo restriction, cache/result bounds and worker failure recovery
on HIL before enabling the dashboard. The fixed templates publish compact
results and diagnostics on stdout/stderr; they cannot write to the host's result
directory. Rebuild the runtime image with the updated `task.py`; an older image
that writes `/results` is incompatible. Full Gazebo campaigns need a separate
output-storage and isolation design.
