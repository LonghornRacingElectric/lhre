# ADR-017: Tests and linters gate merges; reviews check intent

- **Status:** Accepted
- **Date:** 2026-11

## Context

Agents now write a growing share of this repo's code and review comments.
They produce more code than reviewers can read line by line, and their
mistakes look plausible: a missing edge case, a test loosened until it
passes, a fetch at test time, a warning silenced. A human approval was the
main guard against all of that, and it doesn't scale with the volume.

## Decision

- Correctness is enforced by CI, not by reading. Every class of mistake we
  can describe mechanically gets a gate that feeds `All checks passed`.
- The testing contract in [testing.md](../testing.md) binds every change,
  human or agent: behavior changes ship with a test, tests stay hermetic,
  and tests are never weakened to get green.
- New gates are ratchets: strict for new code, with today's violations in
  a committed baseline that only shrinks.
- Reviews (human and CodeRabbit) focus on intent, design and whether the
  tests actually pin the behavior, and push back on any new baseline
  entry or loosened test.

## Alternatives considered

- **Rely on review.** Doesn't scale with agent volume, and plausible
  mistakes are the ones reviewers miss.
- **Turn on every gate at full strength.** One huge cleanup diff across
  the repo conflicts with every open PR and buries real fixes in noise.
  Ratchets get the same end state one touched file at a time.

## Consequences

- Each gate costs CI time; cheap ones (lint) always run, expensive ones
  stay path-gated by the `Changes` job.
- Baselines are committed files that have to be kept honest.
- A green PR means more than it used to, so reviewers can spend their time
  on whether the change is the right one.
