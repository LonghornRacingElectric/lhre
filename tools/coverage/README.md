# Coverage

```bash
bazel coverage //boards/... //drivers/...    # Linux and macOS
uv run --only-group coverage diff-cover \
  "$(bazel info output_path)/_coverage/_coverage_report.dat" \
  --compare-branch=origin/main
```

Line coverage of firmware code from the host tests, on the lines a PR
changes. A suite can pass while new code never runs under it; this is the
report that shows it. CI runs both lines in the `Bazel (Linux)` job on PRs
and puts diff-cover's report on the run's summary page. It never fails the
build: no percent is enforced, but high coverage of changed lines is the
expectation ([testing.md](../../docs/testing.md)), and a reviewer asks
about a low one.

## What's measured

`bazel coverage` uses LLVM's source-based coverage from the hermetic
toolchain (the `coverage` lines in
[`.bazelrc`](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc)).
Only firmware code is instrumented: `boards/*` and `drivers/longhorn` and
`drivers/lhal`, not tests or generated CAN code. Changed lines in anything
else (Python, docs, BUILD files) don't count either way. Coverage needs
Linux or macOS; Windows has no coverage runtime.

## Why changed lines

[diff-cover](https://github.com/Bachmann1234/diff_cover) asks the question a
reviewer cares about, "is the code in this PR run by a test", with no
per-file floors to keep up to date. Old untested code doesn't block an
unrelated change, and new code can't ride in on a well-tested file's
average ([ADR-017](../../docs/architecture/017-tests-gate-merges.md)).

## Gotcha

A source that no host test links isn't in the report at all, so
diff-cover can't see its lines. CI warns on a PR that adds or edits such
a file under `boards/`, `drivers/longhorn/` or `drivers/lhal/`: link it into
a host test. Firmware app code is linked by the app's host tests; code
reachable only from `Board/` (the target entry point), CubeMX's `Core/` and
the STM32 LHAL backend (`drivers/lhal/stm32/`) only run on target, so they
are exempt.
