# ADR-020: Dedicated service accounts and bounded smoke jobs

- **Status:** Accepted
- **Date:** 2026-11
- **Supersedes:** [ADR-019](019-hil-job-service.md)

## Context

The initial HIL service implements CPU smoke, FMU timing and GPU rendering
checks. It does not yet support general simulation studies. Review identified
that root RPC processes, writable host output mounts and a dashboard sharing
the production telemetry account make resource and privilege bounds unclear.
This is an accepted implementation design; deployment and live-host acceptance
are still pending.

## Decision

Keep the dashboard on telemetry with the restricted SSH protocol to HIL, but
run it as a separate unprivileged `lhr-jobs` account. A separate `lhr-job-ssh`
account on HIL can invoke only the root-owned bridge as `lhr-runner`. The worker
and bridge run as `lhr-runner`; its Docker membership remains equivalent to
administrator access on HIL. Root ownership of executable code and restricted
SSH/sudo configuration are required. Separate accounts prevent a dashboard
compromise from inheriting telemetry's Docker privileges.

Cap bridge concurrency across processes and dashboard SSH requests, cache
Docker/NVIDIA observations in the worker, rate-limit failed logins, and bound
archive caching by bytes and count. Use private HTTPS port 8444, outside the
ports supported by Funnel, on the production host.

The initial templates publish compact results through bounded Docker logs,
without a writable host output mount. Worker failures are isolated per job;
failed launches retain resource reservations until reconciled. General studies,
Gazebo, multiple workers, ownership and large artifacts remain future work.

## Alternatives considered

- **Host the dashboard on HIL:** removes the cross-host key, bridge and dashboard
  load from production telemetry. This is simpler for one worker. Retain the
  requested telemetry dashboard and SSD cache for now, with dedicated accounts
  and explicit request/memory bounds; reconsider placement before expansion.
- **Keep shared administrator accounts:** less setup, but a web-process exploit
  inherits unrelated production privileges.
- **Bind-mount an unbounded result directory:** convenient for arbitrary files,
  but container CPU/memory limits do not bound writes to the host disk. Compact
  smoke results fit the existing rotating log transport. Larger artifacts need
  a separately validated quota-backed storage design.

## Consequences

Deployment now requires dedicated accounts and a rebuilt runtime image that
emits summaries on stdout. Result downloads include final metadata, bounded
logs and the recovered JSON summary; rotated diagnostics are not a full study
archive. Nothing here claims live SSH, Docker, NVIDIA or production-host
acceptance. The deployment runbook records those checks before activation.
