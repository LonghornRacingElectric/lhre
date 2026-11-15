# ADR-020: Run the initial jobs dashboard under telemetry's existing account

- **Status:** Accepted
- **Date:** 2026-11
- **Supersedes:** The telemetry account-separation requirement in [ADR-019](019-hil-job-service.md)

## Context

During deployment, the operator chose to run the jobs dashboard alongside the
existing viewer under telemetry's `electric` account. The dashboard initially
stays on loopback and is reached through an operator SSH tunnel; domain setup
is deferred. ADR-019 had called for a separate telemetry account.

## Decision

Use `electric`'s existing PM2 daemon for the separate `lhr-jobs` application.
Keep its configuration, data and restricted HIL SSH key separate from the
viewer. Preserve the existing viewer process, containers and public routes.
HIL still uses separate runner and forced-command SSH accounts.

## Alternatives considered

- **Dedicated telemetry service account:** contains a dashboard compromise
  better, but the operator explicitly selected the current shared account.
- **Move the dashboard to HIL:** removes the cross-host bridge, but changes the
  requested placement. ADR-019's other decisions remain in effect.

## Consequences

The dashboard inherits `electric`'s existing privileges, including Docker
access. The restricted HIL key does not isolate the dashboard from telemetry's
own administrator capabilities. Moving to a dedicated account later remains
possible. Local browser access requires an SSH tunnel; no new public or tailnet
web listener is installed by this deployment.
