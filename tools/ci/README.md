# CI helpers

Scripts the jobs in
[`ci.yml`](https://github.com/LonghornRacingElectric/lhre/blob/main/.github/workflows/ci.yml)
run. They live here, not inline in the workflow, so they can be tested.

## `changes.py`: which jobs a change runs

The `Changes` job lists the files a PR or push touches (renamed files' old
paths included) and pipes them to `changes.py`, which prints one
`<area>=true|false` line per area into `$GITHUB_OUTPUT`. Each path-gated
job skips only when its area is `false`.

| Area | Jobs | Runs on |
| ---- | ---- | ------- |
| `bazel` | Bazel (Linux), Bazel (Windows) | anything outside `autonomy/` and `simulation/`, except viewer-only files |
| `telemetry` | Telemetry | `apps/telemetry/`, `apps/BEVO/`, `lib/spec/`, `lib/codegen/telemetry/`, except viewer-only files |
| `viewer` | Telemetry viewer | the same paths, viewer included |
| `sim` | Simulation fast checks | `simulation/`, `vehicle/`, `.gitmodules` |
| `car` | FourPost | `vehicle/`, the BobSim pin, `simulation/tools/`, `simulation/Makefile`, `simulation/docker-compose.yml`; never on the weekly run |
| `autonomy` | Autonomy (colcon) | `autonomy/`, the BobSim pin, `vehicle/` |
| `bevo` | BEVO Pi binaries, BEVO dash frontend | `apps/BEVO/`, `lib/spec/`, `lib/codegen/telemetry/`, `platforms/`, `toolchains/`, `MODULE.bazel(.lock)` |
| `docs` | Docs | any `.md`, `docs/`, `mkdocs.yml`, `pyproject.toml`, `uv.lock` |
| `runner` | HIL dashboard | `apps/job_runner/` |

A **viewer-only** file is one under
`apps/telemetry/analysis/database/viewer_tool/` that Bazel never reads: the
viewer is an npm app outside Bazel, so a change to it alone runs only the
viewer's own job (and Docs for its Markdown). Python there still counts
for Bazel, because `//apps/telemetry/analysis:analysis_lib` globs
`**/*.py` and so takes in `get_schema.py`, and so does a `BUILD` or `.bzl`
file, which would change the package.

A change to `ci.yml` or `changes.py` itself runs every area, so the PR
that changes what runs shows the full result. So do weekly and manual
runs (`--everything`, except FourPost on the weekly run, which
`simulation.yml` covers), pushes too large for the compare API to list,
and an empty file list. If `Changes` fails, every job runs and
`All checks passed` still fails.

To move a path between areas, change `AREAS` and add the case to
`changes_test.py`: `bazel test //tools/ci:changes_test`, or without Bazel,
`python3 -m unittest tools.ci.changes_test` from the repo root.
`simulation/tests/test_ci_contract.py` also checks the `car` area.

## `failed_tests_summary.sh`

Run by the Bazel job when a step fails: reads the test step's console log
and writes each failed test with the last 80 lines of its `test.log` to the
run's summary page, so a failure is readable without the full build output.
