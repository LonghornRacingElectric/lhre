# Lint

```bash
uv run --group lint ruff check          # Python lint (CI runs this)
uv run --group lint ruff check --fix    # apply the safe fixes
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

## Suppressions

There is no baseline file. Violations that existed when the gate went on
carry `# noqa: <code> pre-existing when the lint gate went on`; fix one
and delete its comment. A new suppression names the rule and says why on
the same line. RUF100 fails any `noqa` that no longer suppresses anything,
so stale ones can't pile up ([ADR-017](../../docs/architecture/017-tests-gate-merges.md)).
