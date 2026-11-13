# ADR-027: Share Artemis telemetry reads through one query gateway

- **Status:** Accepted; extends [ADR-024](024-artemis-query-gateway.md)
- **Date:** 2026-11

## Context

Direct dashboard SQL and separate live consumers duplicate schema knowledge,
database work and Kafka connections. Frequency pools need a common query and
alignment interface for Grafana, the viewer and scripts.

## Decision

One Go gateway resolves generated Artemis signals to frequency pools. It serves
bounded raw or bucketed queries and live subscriptions. Clients use an HTTP API;
a Grafana datasource provides the query builder and standard live integration.

Postgres remains authoritative and the existing Kafka database writer remains
the sole native telemetry writer. Its transaction records persistence offsets
and session revisions. Recent memory baselines plus a contiguous Kafka tail,
revision-keyed memory slabs and disposable Arrow files satisfy reads before
falling back to Postgres. One seal worker creates immutable finished-session
copies and one-second aggregate states. Cache budgets come from measurements.

## Alternatives considered

Per-client database queries retain uncontrolled work and schema coupling.
Per-client Kafka consumers duplicate buffering and do not provide history.
An external cache adds operational work without a demonstrated need on one
server. Plain recency eviction lets one-off historical scans displace hot days.

## Consequences

Clients share routing, limits, aggregation semantics and cached data. Cache loss
needs only a rebuild. Late imports and session merges invalidate revisions;
timestamps and row counts alone cannot establish validity. Memory is bounded,
so pending-tail overflow produces an explicit coverage gap. Existing SQL panels
need migration to the query builder. Historical-car storage remains independent.
