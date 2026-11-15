# Lint

```bash
uv run --group lint ruff check          # Python (CI runs this)
uv run --group lint ruff check --fix    # apply the safe fixes
uv run --group lint actionlint          # GitHub workflows (CI runs this)
git ls-files -z '*.sh' '*.bash' apps/BEVO/dashd/deploy/labwc-autostart ':!autonomy' ':!simulation/bobsim' \
  | xargs -0 uv run --group lint shellcheck --severity=warning   # shell (CI runs this)
```

CI also fails on a switched-off test, with one `git grep` in the `Lint`
job: `flaky = True` or `enable_tests = False` in a BUILD file, `DISABLED_` /
`GTEST_SKIP` in C++, or `mark.skip` / `mark.xfail` / `unittest.skip` /
`expectedFailure` in Python, also as a bare `@skip` (`skipif`, `skipIf`
and `skipUnless` for a platform guard are fine). Runtime skips
(`pytest.skip()`, `self.skipTest()`) and `tags = ["manual"]` aren't
caught, since both have real uses (a missing optional dependency, Docker
integration suites); a reviewer reads those.
A test that can't pass gets fixed or deleted in its own commit, with the
reason in the PR ([docs/testing.md](../../docs/testing.md)).

Linters for code Bazel doesn't compile, run straight from their own CLIs.
C/C++ formatting lives in [tools/format](../format/README.md), and
`autonomy/` is linted by `ament_lint` in its colcon tests
([ADR-009](../../docs/architecture/009-autonomy-outside-bazel.md)). The
linters are pinned exactly in `pyproject.toml`'s `lint` group, so a linter
upgrade (which can add findings) is a deliberate change, never drift. The
CI `Lint` job always runs; it's seconds.

## Python: ruff

Config is the repo-root
[`ruff.toml`](https://github.com/LonghornRacingElectric/lhre/blob/main/ruff.toml).
The rules catch bugs and unsafe habits, not style: undefined names, unused
imports, mutable default arguments, loop-variable closures, `except
Exception` that swallows everything, `subprocess.run` without `check=`,
requests without a timeout, `shell=True`, `eval`/`exec`, unsafe YAML and
pickle, blocking calls in async code. Formatting is left alone.

## Shell: shellcheck

Every tracked `.sh`/`.bash` file outside `autonomy/` and BobSim, plus the
one shebang-only script, at warning severity: unquoted expansions that
split, unused variables (usually a typo), `local x=$(cmd)` masking a
failure. A new shebang-only script has to be added to the list in CI.

## Workflows: actionlint

`.github/workflows/*.yml`: unknown keys, `${{ }}` expressions that
reference missing jobs, steps or contexts, and shellcheck on every `run:`
block.

## Suppressions

There is no baseline file ([ADR-017](../../docs/architecture/017-tests-gate-merges.md)).
A finding is fixed, or suppressed on its own line with the reason after
it: `# noqa: <code> <why>` or `# shellcheck disable=<code> # <why>` on the
line above. Python violations that existed when the gate went on say
`pre-existing when the lint gate went on`; fix one and delete its comment.
RUF100 fails any `noqa` that no longer suppresses anything, so stale ones
can't pile up.
