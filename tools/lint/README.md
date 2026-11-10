# Lint

```bash
uv run --group lint ruff check                    # Python lint (CI runs this)
uv run --group lint tools/lint/ruff_baseline.py   # baseline is up to date (CI runs this)
uv run --group lint pytest -q tools/lint          # tests for these tools
```

Linters for code Bazel doesn't compile. C/C++ formatting lives in
[tools/format](../format/README.md), and `autonomy/` is linted by
`ament_lint` in its colcon tests ([ADR-009](../../docs/architecture/009-autonomy-outside-bazel.md)).
The linters are pinned exactly in `pyproject.toml`'s `lint` group, so a
linter upgrade (which can add findings) is a deliberate change, never drift.
The CI `Lint` job always runs; it's seconds.

## Python: ruff

Config is the repo-root
[`ruff.toml`](https://github.com/LonghornRacingElectric/lhre/blob/main/ruff.toml).
The rules catch bugs and unsafe habits, not style: undefined names, unused
imports, mutable default arguments, loop-variable closures, `except
Exception` that swallows everything, `subprocess.run` without `check=`,
requests without a timeout, `shell=True`, `eval`/`exec`, unsafe YAML and
pickle, blocking calls in async code. Formatting is left alone.

## Baselines only shrink

Turning a rule on would otherwise mean fixing every old violation in one
huge diff. Instead, `ruff.toml` ends in a generated `per-file-ignores`
section listing the rules each file already broke when the rule went on.
New files are linted fully, and old files can't pick up a rule they don't
already break.

The baseline may only shrink ([ADR-017](../../docs/architecture/017-tests-gate-merges.md)):

- **Fixed an old violation?** Its entry is now stale and CI fails until you
  regenerate: `uv run --group lint tools/lint/ruff_baseline.py --write`.
- **New violation?** Fix it. Regenerating would hide it, so on PRs CI also
  fails if the baseline gained an entry since the base branch
  (`--no-growth-from`).
- **File only moved?** Its entry moves with it and counts as growth. A
  maintainer adds the `lint-baseline-ok` label to the PR and re-runs the
  `Lint` job; the job reads the labels when it runs, so a re-run sees it.
- **Base branch unreadable?** The growth check fails rather than skip
  itself, so a failed fetch can't let a grown baseline through.

Never edit the generated section by hand.
