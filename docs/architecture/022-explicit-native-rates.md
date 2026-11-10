# ADR-022: Express CAN timing as native rates instead of tiers

- **Status:** Accepted; supersedes the rate-tier decision in [ADR-008](008-can-spec-pipeline.md).
- **Date:** 2026-11

## Context

FAST/MEDIUM/SLOW tiers were exposed by the spec editor and validator but had
no publishing or codegen consumer. Signal discovery needs numerical native
and uplink rates, which describe different stages of the telemetry pipeline.

## Decision

Remove rate tiers and their overrides. Each CAN message keeps its existing numerical
`frequency_hz` field.
Every signal in that message inherits its declared rate. Zero means aperiodic;
negative or nonfinite rates are invalid. Per-signal uplink policy remains
separate. External signals have no native-rate declaration.

## Alternatives considered

- Keep tiers alongside numerical rates: retains an unused second vocabulary.
- Declare a native rate on every CAN signal: duplicates message timing and
  allows contradictory rates inside one frame.

## Consequences

Editor, validation, documentation, and generators use one numerical native rate.
Generated CAN JSON and C++ keep their existing consumer-facing timing names.
Existing textproto frequency fields remain unchanged. Snapshot protobuf ids,
types, and SQL structure are unaffected.
