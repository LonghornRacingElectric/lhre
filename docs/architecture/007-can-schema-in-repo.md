# ADR-007: CAN schema source moves in-repo; generated files are never checked in

- **Status:** Accepted
- **Date:** 2026-08

## Context

BEVO migrated (ADR-005) carrying four checked-in generated artifacts:
`can.json`, `can_packets.proto`, `generated_mapping.rs`, and
`sensor_data.desc`, all produced from CSV definitions that lived in
lhre-2026's `drivers/longhorn-lib`. Checked-in generated files drift from
their sources silently, and regeneration required a checkout of the old
repo.

## Decision

- **The schema sources move here**: `apps/BEVO/schema/` holds the CSVs and
  generator scripts, authoritative from now on.
- **Pure derivations are build outputs, never checked in.** Bazel generates
  `can.json` (genrule over the CSVs); `build.rs` generates the prost
  bindings *and* the signal-dispatch mapping into `OUT_DIR` (the mapping
  generator was ported from `codegen.py` to Rust inside `build.rs`, output
  verified byte-identical — this also deleted the Python-protobuf
  dependency and `sensor_data.desc` outright).
- **One deliberate exception:** `can_packets.proto` stays checked in as a
  *tool-updated source* (`bazel run //apps/BEVO/schema:update_can_proto`).
  It cannot be a pure build output because protobuf field numbers are an
  append-only wire contract: the generator seeds tags from the existing
  proto and writes `#N` annotations back into the CSVs so a field's tag
  never changes. Same regenerate-don't-hand-edit model as CubeMX `Core/`.

## Alternatives considered

- **Keep vendored artifacts + sync scripts** (status quo): silent drift,
  cross-repo regeneration dependency.
- **Make the proto a build output too**: rejected — field-number stability
  across arbitrary CSV edits can't be guaranteed by a pure function of the
  CSVs, and a tag change silently corrupts decoding of recorded data.
- **Keep codegen.py driven by Bazel**: worked, but left the Cargo flow
  needing pip `protobuf` and a separate descriptor step; the build.rs port
  gives both build systems the identical single code path.

## Consequences

- Schema edits are: change CSVs → run the updater → commit CSVs + proto.
  Everything else regenerates per build.
- The old repo's `drivers/longhorn-lib` CSVs are no longer authoritative;
  firmware C codegen still living there must migrate before boards consume
  this copy (future ADR).
- Cargo builds now need `python3` (stdlib only) and `protoc` on the machine
  — Bazel builds need neither.
