# Architecture Decision Records

Numbered, dated records of the decisions that shape this repo — the ones a
new member would otherwise ask "why are we doing it this weird way?" about,
three years after everyone who made the decision graduated.

## Index

| ADR | Decision | Status |
| --- | -------- | ------ |
| [001](001-monorepo.md) | One monorepo; `main` is always the current car | Accepted |
| [002](002-bazel.md) | Bazel with hermetic toolchains, not CMake | Accepted |
| [003](003-lhal.md) | App code depends on LHAL interfaces, never ST HAL | Accepted |
| [004](004-docs-next-to-code.md) | Docs live next to code, published as one site | Accepted |
| [005](005-telemetry-in-monorepo.md) | Telemetry migrates into this repo, Rust via rules_rust | Accepted |
| [006](006-pi-binary-deploys.md) | The Pi gets static binary bundles, not the repo | Accepted |
| [007](007-can-schema-in-repo.md) | CAN schema source in-repo; generated files never checked in | Accepted |
| 005–007 | Reserved by the in-flight `bevo-migration` branch (telemetry in monorepo, Pi deploys, CAN schema) | Pending merge |
| [008](008-can-spec-pipeline.md) | One CAN spec in textproto; all derived artifacts generated | Accepted |
| [009](009-autonomy-outside-bazel.md) | Autonomy (ROS 2) builds with colcon, outside Bazel | Accepted |

## ADRs vs. READMEs

Both exist, on purpose, and they don't overlap:

- **READMEs** (next to the code) are *living how-to docs*: what a directory
  is, how to use its targets, current gotchas. They're rewritten freely as
  the code changes.
- **ADRs** are *decision records*: the context at the time, the options on
  the table, why one won. They are **immutable** — when a decision changes,
  don't edit the old ADR; write a new one and mark the old one
  `Superseded by ADR-NNN`. The old record stays, because "what did we
  believe then, and what changed" is exactly the information turnover
  destroys.

Where a decision already has a long-form why page (e.g.
[build-system.md](../build-system.md)), the ADR stays short and links to it
— the ADR is the durable index entry, not a duplicate.

## When to write one

Write an ADR for decisions that bind the whole repo or outlive one board:
repo layout, build system, a new abstraction layer or enforcement rule, a
convention everyone must follow. Don't write one for local implementation
choices — those belong in the adjacent README or a comment.

## Format

Create `docs/architecture/NNN-short-slug.md` (next free number), add it to
the index above, and follow this skeleton:

```markdown
# ADR-NNN: Title stating the decision

- **Status:** Accepted | Superseded by [ADR-MMM](MMM-slug.md)
- **Date:** YYYY-MM

## Context

The problem and the constraints, as they were at the time.

## Decision

What was decided, stated plainly.

## Alternatives considered

Each option that was on the table, and why it lost.

## Consequences

What this makes easy, what it costs, what it commits us to.
```
