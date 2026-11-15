# ADR-021: Versioned HIL jobs and campaign artifacts

- **Status:** Accepted; extends ADR-019's template and output-storage scope
- **Date:** 2026-11

## Context

The initial HIL queue exposes three hardcoded infrastructure checks, not autonomy
campaigns. Course scoring needs configurable runs, reproducible code revisions
and retained ROS recordings. Rotating console output cannot carry full bags.
The requested rollout uses a `tanush/` feature branch on the existing hosts.

## Decision

Keep the existing queue and restricted SSH bridge. Add an administrator-installed,
root-owned job catalog with immutable image IDs, source commits, bounded resources,
argument lists and typed parameters. The dashboard discovers the catalog. Browsers
choose installed programs and parameters; they cannot provide commands, image
names, host paths or build arbitrary code on the production hosts. Snapshot the
accepted definition in each job so queued runs survive catalog updates unchanged.

The first program runs existing headless Gazebo gate/scoring tools. Each campaign
has its own container network/PID namespace, ROS domain, Gazebo partition and
output directory. Reserve the whole GPU and all eight scheduler CPU slots, allowing
one campaign at a time. Keep scored simulation durations separate from wall-time
hang guards and preserve the scorer's nonzero exit status.

Run campaigns as the worker's numeric UID/GID with a read-only root and only their
own output directory mounted writable. Retain artifacts with queue history and
serve individual authenticated, chunked downloads. Reject traversal, symlinks and
special files. Preserve the smaller compact archive API for check summaries.

Enforce a monitored 256 MiB / 512-file output budget on worker cycles, plus a
2 GiB free-space reserve for new jobs. This is not a filesystem quota and can
overshoot between checks. Restrict this profile to trusted installed programs;
a hard quota is a prerequisite for accepting untrusted artifact-producing code.

## Alternatives considered

- Browser-provided SSH/shell commands: inherits worker authority and provides no
  stable resource, parameter or output contract.
- New frontend/backend code for every task: keeps useful work bottlenecked on
  dashboard releases instead of independently installed job definitions.
- Embed bags in console JSON: rotating logs truncate data and base64 grows memory.
- General container orchestration: still unnecessary for one worker; reconsider
  when jobs require multiple hosts or user ownership and fair scheduling.

## Consequences

Adding another installed job needs a container and catalog entry, not UI changes.
Revisions are explicitly built/deployed, not arbitrary branch names fetched at
submission. Campaign artifacts use host storage; their monitored budget is an
operational bound for trusted code, not an isolation guarantee. Validate image,
rendering, scoring, cancellation, downloads and restart recovery before enabling
new programs. Existing telemetry services and network configuration are preserved.
