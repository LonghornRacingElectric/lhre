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
- Gates use the tools' own mechanisms, with no baseline files or scripts
  of ours: existing violations are fixed or suppressed inline with a
  reason, stale suppressions fail. Coverage is reported on changed lines
  only, with no enforced percent.
- Reviews (human and CodeRabbit) focus on intent, design and whether the
  tests actually pin the behavior, and push back on any new suppression
  or loosened test.

## Alternatives considered

- **Rely on review.** Doesn't scale with agent volume, and plausible
  mistakes are the ones reviewers miss.
- **Baselines that only shrink.** A committed list of today's violations
  per gate, checked by our own scripts. Each script is more code to review
  and trust than the gate it guards, and a list hides each exception from
  the line it excuses.
- **Whole-file coverage floors.** Floors start at whatever a file has
  today (several were 0%), so they gate nothing new. Changed-line coverage
  asks the question a reviewer cares about: is the code in this PR run by
  a test.

## Consequences

- Each gate costs CI time; cheap ones (lint) always run, expensive ones
  stay path-gated by the `Changes` job.
- Inline suppressions are visible in review and must say why.
- A green PR means more than it used to, so reviewers can spend their time
  on whether the change is the right one.
