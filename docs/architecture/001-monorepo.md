# ADR-001: One monorepo; `main` is always the current car

- **Status:** Accepted
- **Date:** 2026-08 (backfilled — the decision predates this record)

## Context

An FSAE electric car carries several ECUs that share a HAL, utility
libraries, CAN definitions, and tooling. The team turns over roughly a
quarter of its members every year. Student teams commonly end up with a
repo per board, a repo (or top-level directory) per season, or both — and
each split multiplies the places shared code can drift apart and the
number of PRs a cross-cutting change needs.

## Decision

One repository holds everything: firmware for every ECU (`boards/`), the
HAL (`drivers/`), shared libraries (`lib/`), host-side apps (`apps/`), and
build/dev tooling (`tools/`). `main` is always the *current* car; seasons
live in git, not in directory names — milestone tags
(`season/2027/comp-michigan`), a `season/<year>` maintenance branch when
work on the next car starts, and deletion of scrapped hardware's
`boards/` directory on `main` (history and the season branch keep it).
The mechanics are in
[CONTRIBUTING § Season policy](../../CONTRIBUTING.md#season-policy).

## Alternatives considered

- **Repo per board.** Shared HAL and libraries become versioned
  dependencies between repos; boards pin different versions and drift. A
  change to a shared interface needs N coordinated PRs instead of one
  atomic one.
- **Per-year directories or repos.** Every season starts by copying last
  year's tree, immediately forking its history. Dead code accumulates
  forever because nothing is ever safe to delete, and `git blame` stops at
  the copy.

## Consequences

- One dependency graph: `bazel test //...` builds every firmware image and
  runs every host test, for the whole car, in one invocation.
- A change to shared code and every board it affects is one reviewable,
  atomic PR — CI proves the whole car still builds.
- History and blame survive across seasons.
- Requires the season-policy discipline (tag, branch, delete) instead of
  the self-enforcing—but corrosive—copy-per-year layout.
