# ADR-019: PM2 dashboard and restricted SSH runner for HIL jobs

- **Status:** Accepted
- **Date:** 2026-11

## Context

The team needs to queue, observe and cancel containerized simulation studies
on one HIL host. Telemetry already runs a PM2 web application and has SSD
capacity for result archives. Nomad prerequisites were installed but no cluster
was activated. A cluster controller adds operational work for this scope.

## Decision

Use a separate PM2-managed Node application on telemetry and a small
systemd-managed Python runner on HIL. Keep the runner's SQLite queue durable
and job containers independent of the browser/dashboard lifecycle. Commands
cross Tailscale through a dedicated forced-command SSH key and a bounded
JSON protocol. Only reviewed workload templates may start containers.

Keep dashboard access private and authenticated, separate from telemetry's
existing public website. Keep models on HIL and cache compact result archives
on telemetry's SSD. Nomad remains inactive. Add the service under
`apps/job_runner`, with Linux runner tests in Bazel and Node tests in CI.

## Alternatives considered

- **Nomad controller and client:** appropriate for multiple workers and
  automatic placement, but unnecessary initial enrollment and scheduling
  infrastructure for the current single-worker scope.
- **Dashboard starts arbitrary SSH commands:** gives a web process broad
  administrator access and makes cancellation/recovery fragile.
- **Run simulations in the PM2 process:** couples long-running jobs to web
  deployments and does not isolate their dependencies.

## Consequences

The service has no new npm runtime dependencies and can be stopped separately
from telemetry. The runner must implement queue recovery, retention and
resource limits, with tests. Adding workers or arbitrary workloads requires
an explicit design extension; reconsider a scheduler when placement across
several workers becomes necessary.
