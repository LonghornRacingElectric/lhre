# ADR-015: A study is a README, a study.yml and a run.py

- **Status:** Accepted
- **Date:** 2026-10

## Context

[ADR-012](012-simulation-outside-bazel.md) says: "A study is
`simulation/studies/<name>/` with a `README.md` and a `run.py`." With that
rule, the inputs of a study were in `run.py` and on its command line. A
reader had to read the code to find the question, the bundle, the
parameters and the sweep grid. Each agent made a new study by hand, in a
different shape.

## Decision

This ADR replaces only that one line of ADR-012. The rest of ADR-012 stays.

- A study is `simulation/studies/<name>/` with at least three files:
  - `README.md`: the question, the method, the result and a status line
    (`current`, `historical` or `stale`).
  - `study.yml`: the inputs only. The question, the bundle, the parameters
    and the sweep grid.
  - `run.py`: the entry point. It reads `study.yml` and writes every output
    to `OUT_DIR`.
- A study can add helper modules, more config files and at most two
  committed figures.
- Car numbers come from the vehicle file and its bundle, not from
  `run.py`.
- Every run writes `summary.json`, `readme.json` and `provenance.json`.
- `make new-study S=<name>` copies the template in
  `simulation/tools/study_template/`. There is no study framework.
- A study has no tests of its own. Tests cover shared code in
  `simulation/tools/` once.
- `studies/front-ackermann/` is older than this rule. It stays as it is and
  is marked historical.

[simulation/docs/studies.md](../../simulation/docs/studies.md) has the
detail.

## Alternatives considered

- **Keep README.md and run.py only.** The inputs stay hidden in code, and
  each study has a different shape.
- **A study framework with a base class or a runner.** It adds code that
  every study depends on, for a problem that a template and a convention
  solve.
- **A fixed folder layout.** Some studies need helper modules or extra
  config files. A minimum of three files allows them.
- **Inputs on the command line.** A variant then needs a long `ARGS`
  string. A second config file is easier to read and to review.

## Consequences

- A reader finds the inputs of any new study in `study.yml`.
- `study.yml` is an input of the result. A change to it makes the committed
  result stale.
- `make study` does not read `study.yml`. `run.py` does. A legacy study
  without `study.yml` still runs.
- The template has to stay correct. `tests/test_new_study.py` runs it.
