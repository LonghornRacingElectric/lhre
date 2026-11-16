# ADR-020: Changed firmware lines need 80% host-test coverage to merge

- **Status:** Accepted
- **Date:** 2026-11

## Context

[ADR-017](017-tests-gate-merges.md) made CI the gate for correctness but
left coverage as a report with no enforced percent. In practice nobody
opens the run's summary page on a green PR, so the report gated nothing:
a firmware change with no test, or a new source no host test links,
merged as green as one with full tests. That is exactly the plausible
gap ADR-017 says reviewers miss.

## Decision

- The `Bazel (Linux)` job fails a PR when under 80% of the firmware lines
  it adds or changes run under a host test, measured by diff-cover on
  `bazel coverage //boards/... //drivers/...`.
- It also fails when a changed firmware source isn't linked into any
  host test, since diff-cover can't see such a file at all, and when
  firmware changed but the coverage run left no report.
- The `coverage-exempt` label turns the failure into a warning. A PR that
  uses it says why. Code that only runs on target (`Board/`, `Core/`,
  `drivers/lhal/stm32/`) is never measured and needs no label.
- This replaces ADR-017's "Coverage is reported on changed lines only,
  with no enforced percent". The rest of ADR-017 stands.

## Alternatives considered

- **Keep it report-only.** Costs nothing and gates nothing, which is
  where we were.
- **100% of changed lines.** Defensive branches (a HAL error path that
  host fakes can't reach) would need the label on many ordinary PRs, and
  a label used routinely stops meaning anything.
- **Whole-file or repo-wide floors.** Rejected in ADR-017 for the same
  reasons: they gate old code instead of the change.

## Consequences

- Firmware PRs need tests to merge, not just to look good.
- Exemptions are visible on the PR as a label, and the reason is in its
  description.
- The threshold is one number (`MIN_COVERAGE` in `ci.yml`); raising it
  is a new ADR.
- Python, simulation and telemetry coverage stay report-only or
  unmeasured for now.
