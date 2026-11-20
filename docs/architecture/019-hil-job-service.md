# ADR-019: PM2 dashboard and restricted SSH runner for HIL jobs

- **Status:** Accepted; telemetry account choice superseded by [ADR-020](020-shared-telemetry-dashboard-account.md)
- **Date:** 2026-11

## Context

The team needs to queue, observe and cancel fixed CPU smoke, FMU timing and GPU
rendering checks on one HIL host. General simulation studies and Gazebo campaigns
are outside the initial scope. Telemetry already runs a PM2 web application and
has SSD capacity for compact result archives. Nomad prerequisites were installed
but no cluster was activated. A cluster controller adds operational work for
this scope. This records the implementation design; deployment and live-host
acceptance are still pending.

## Decision

Use a separate PM2-managed Node application on telemetry and a small
systemd-managed Python runner on HIL. Keep the runner's SQLite queue durable and
job containers independent of the browser/dashboard lifecycle. Commands cross
Tailscale through a dedicated forced-command SSH key and a bounded JSON
protocol. Only reviewed workload templates may start containers.

Keep dashboard access private and authenticated, separate from telemetry's
existing public website and administrator account. Docker privileges stay on
HIL. Keep models there and cache compact results on telemetry's SSD. The initial
workloads publish results through bounded Docker logs without writable host
output mounts. Account setup, resource limits and deployment checks live in the
[runbook](../../apps/job_runner/README.md).

Nomad remains inactive. Add the service under `apps/job_runner`, with Linux
runner tests in Bazel and Node tests in CI.

## Alternatives considered

- **Host the dashboard on HIL:** simpler for one worker; removes the cross-host
  key, SSH bridge and web-process load from production telemetry. Retain the
  requested telemetry dashboard and SSD cache for now, with a dedicated
  unprivileged account and bounded requests. Reconsider placement before
  expanding the service.
- **Nomad controller and client:** appropriate for multiple workers and automatic
  placement, but unnecessary initial enrollment and scheduling infrastructure
  for the current single-worker scope.
- **Dashboard starts arbitrary SSH commands:** gives a web process broad
  administrator access and makes cancellation/recovery fragile.
- **Run workloads in the PM2 process:** couples long-running jobs to web
  deployments and does not isolate their dependencies.
- **Unbounded host output mounts:** container CPU/memory limits do not bound
  writes to host disk. Compact smoke results fit rotating logs. Larger study
  artifacts need a separately validated storage design with quotas.

## Consequences

The service has no npm runtime dependencies and can be stopped separately from
telemetry. The runner must implement queue recovery, retention and resource
limits, with tests. A dashboard compromise must not inherit telemetry's Docker
privileges. The HIL runner's Docker membership remains administrator-equivalent
on that host; it is a trusted service, not a Docker privilege boundary.

Deploy only after live SSH, Docker, NVIDIA and production-host checks pass.
Adding workers, ownership, general workloads or large artifacts requires an
explicit design extension; reconsider a scheduler when placement across several
workers becomes necessary.
