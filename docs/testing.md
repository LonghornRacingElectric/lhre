# Testing

More of this repo's code and reviews are written by agents. A reviewer
can't re-derive every line, so CI has to catch what a reviewer would have.
This page is the contract: what every change must ship with, what "a good
test" means here, and which gate catches what.
[ADR-017](architecture/017-tests-gate-merges.md) records why.

## What every change ships with

- **A behavior change ships with a test that fails without it.** Bug fixes
  get a regression test; new logic gets tests for its edges (limits,
  saturation, faults, units), not only the happy path.
- **Tests are never weakened to get green.** Don't delete, skip, loosen a
  tolerance, widen a size budget, add `flaky = True`, or add a lint
  suppression to make CI pass. If a test is wrong, fix it in its own
  commit and say why in the PR.
- **Tests are hermetic.** See below. A test that passes on your machine
  and fails on CI (or the other way round) is a broken test.

## Hermetic tests

A hermetic test gives the same answer on every machine, every run, with
no network. Concretely:

- **No network.** Unit tests never reach the internet or a service. Fake
  the boundary (LHAL host fakes for firmware, synthetic files for
  simulation tools). Tests that truly need a service (the telemetry
  integration suite) are tagged `manual` + `local` and run in their own CI
  job.
- **No wall clock.** Inject time. Firmware code takes ticks from LHAL;
  Python takes a clock argument or a fixed timestamp.
- **No unseeded randomness.** Seed it, and put the seed in the test.
- **No absolute or home paths.** Read inputs from runfiles (Bazel) or the
  test's own directory; write only to the test's temp dir
  (`TEST_TMPDIR`, pytest's `tmp_path`).
- **Pinned inputs.** Dependencies come from lockfiles
  (`MODULE.bazel.lock`, `requirements_lock.txt`, `uv.lock`), never from a
  fetch at test time.

## Gates

`All checks passed` is the one required check; every gate below feeds it.

| Gate | What it catches | Run it locally |
| ---- | --------------- | -------------- |
| Bazel build + test (Linux, Windows) | Broken builds, failing unit tests, firmware over its flash/RAM budget | `bazel test //...` |
| UBSan | Undefined behavior in host C++ (overflow, bad shifts, misaligned access) | `bazel test --config=ubsan //boards/... //drivers/... //lib/codegen/cpp/...` |
| `-Wall -Wextra -Werror` on app code | Warnings in firmware application code | part of `bazel build //...` |
| Format | Unformatted C/C++ | `bazel run //tools/format:check` |
| Lint: Python (ruff) | Undefined names, unused imports, swallowed exceptions, unchecked subprocesses, unsafe calls. Stale `noqa` fails. | `uv run --group lint ruff check` |
| Lint: shell (shellcheck) | Word splitting, unused variables, masked failures in scripts | see [tools/lint](../tools/lint/README.md) |
| No switched-off tests | `flaky = True` or `enable_tests = False` in BUILD files, `DISABLED_` or `GTEST_SKIP` in C++, unconditional skips in Python | see [tools/lint](../tools/lint/README.md) |
| Lint: workflows (actionlint) | Broken expressions and job references in CI, shellcheck on `run:` blocks | `uv run --group lint actionlint` |
| Simulation fast checks | lhre's simulation tools, vehicle bundle and study contracts (BobSim itself is tested in BobDyn) | `make -C simulation test`; on a PR also `make -C simulation check-vehicle BASE=origin/<base>` |
| Docs | Broken links, missing nav entries | `uv run --group docs mkdocs build --strict` |

## No baselines

Gates have no grandfather lists. An existing violation is fixed, or
suppressed on its own line (`# noqa: <code>`, `# shellcheck disable=<code>`)
with the reason next to it, so every exception sits where a reviewer sees
it. A suppression that no longer matches anything fails lint. Coverage is
judged on the lines a PR changes, not on whole files.
