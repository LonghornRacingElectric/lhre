# ADR-019: Identify the server schema from its running consumers

- **Status:** Accepted
- **Date:** 2026-11

## Context

The server deploys locally from a clone. Its checkout, loaded Docker tags and
running containers can represent different revisions. An independently built
schema authority can therefore advertise a schema that ingest does not use.
BEVO must compare against the deployed consumers without a GitHub deploy or
a change to the telemetry protocol.

## Decision

Bazel packages the shared contract and descriptor at `/schema/` in ingest
and bridge images, using their existing textproto pipeline. The authority's
host-side `--deployed` mode reads both running containers by ID on each HTTP
request, validates the artifacts, checks agreement, and checks that the
containers did not change during the read. Missing or mixed deployments
return HTTP 503, with no checkout or bundled-schema fallback. The profile
identifies the actual containers and image IDs. `schema-serve` in the server
devtool selects this mode for deployments from a clone.

The updater still initiates its existing HTTP exchange and selects only
prebuilt approved GHCR artifacts. Immediately before activation it checks
that the profile is unchanged; a deployment change during the download
leaves its current installation intact. Its plan remains release-specific.
No ingest, protobuf, MQTT, database or packet-ID behavior is changed.

## Alternatives considered

- Use the current Git branch or latest Bazel output: neither proves that
  those artifacts are running.
- Rebuild the authority alongside each component: easy to forget during
  partial rebuilds, image overrides or local deployments.
- Add schema negotiation to each telemetry frame: exceeds deployment
  selection and changes the production protocol.

## Consequences

A checkout-only change cannot silently change the advertised schema. Old
images need a rebuild of both consumers to acquire the metadata. A mixed
deployment temporarily refuses updater checks. The host authority needs
Docker access; its snapshot-container deployment remains separately managed.

This is observation and validation, not a transaction spanning server and
BEVO. Operators must coordinate schema deployments, updater execution and
publisher restarts. Database migrations and implicit scalar presence remain
separate concerns. The live copy test needs a real server/GHCR but can run on
any development machine; executing the core ARM daemons still needs a Pi.

See the [authority runbook](../../apps/telemetry/schema_registry/README.md)
and [BEVO updater](../../apps/BEVO/updater/README.md).
