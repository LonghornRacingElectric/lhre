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

Use `/var/lib/lhr-runner` for state. The bridge must be executable for sudo's
forced SSH command to work. It runs Python in isolated mode and never
interprets `SSH_ORIGINAL_COMMAND`.

Generate a dedicated worker key on telemetry. On HIL, its authorized_keys
entry must have `from="100.88.194.126",restrict,command="sudo -n
/opt/lhr-runner/bridge"` before the public key (all on one line). The sudoers
rule permits only `/opt/lhr-runner/bridge` with no arguments. Validate that
rule with `visudo -cf` before installing it. The dashboard must not use the
user's unrestricted administrator key.

`Runtime.Dockerfile` builds on the already installed benchmark runtime on HIL.
Its context contains `task.py`, the prior campaign's `input/` directory and
`OrionFMI.fmu`. The native FMU hash is
`b6d3d4ddc8c451d421a95704951f642ea0a8eaf17414f85633d57e4ff5aa71c9`.
The existing `lhr/worker-gpu-check:2026-11-13` image supplies the GPU template.
No private FMU needs to leave HIL to construct the image.

`ecosystem.config.cjs` defines a single PM2 application, `lhr-jobs`, separate
from `viewer_tool`. Install the application on telemetry's SSD, create the
user-only configuration and known_hosts files, then start this app by name.
Inspect PM2 persistence before saving: saving the process list also includes
existing applications, so never run `pm2 delete all` or replace their configs.

Add `tailscale serve --bg --https=8443 http://127.0.0.1:8091`. Preserve the
existing 443 handler and Funnel flag, and verify the new port is private.
The dashboard uses HTTP Basic authentication over that HTTPS connection,
salted scrypt password verification, same-origin mutation checks and a
restrictive content security policy. The backend does not log credentials.

Deployment acceptance: queue/run a CPU job, download its archive, cancel
queued and running jobs, pause/resume, restart the dashboard and worker during
a job, complete the FMU profile, verify NVIDIA output, and measure idle service
usage. Confirm existing telemetry container IDs/status and public routes
remain unchanged. Record results in the adjacent runbook after deployment.
