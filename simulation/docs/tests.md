# Tests

`make test` runs two checks in the container. Run it from `simulation/`
before you finish.

| Target | What it checks | Time |
| ------ | -------------- | ---- |
| `make smoke` | BobSim loads `vehicle/vehicle.yml` and its tires. It checks loading only. It does not check physics. | about 3 s |
| `make unit` | `pytest` runs the files in `tests/`. Then the target checks that `bobsim/` has no local changes. | about 14 s |
| `make test` | `make smoke`, then `make unit`. | about 16 s |

The times are from a Windows laptop with Docker Desktop, BobSim `fe03bab`.

## What the tests check

- `test_bobsim_layout.py`: lhre code and docs use the BobSim names at the
  pin. Every import resolves. `make records` writes only into the BobLib of
  BobSim.
- `test_bobsim_pin.py`: the checked-out BobSim contains `739143a`, the
  front anti-dive sign fix (BobSim PR #70). BobSim has no local changes.
- `test_ci_contract.py`: the `make` targets and the CI `fast` job keep the
  shape on this page.

These tests do not check physics. Later PRs add physics fixtures.

## Add a test

- Put the file in `tests/` and name it `test_<topic>.py`. Do not put tests
  in a study folder or in `tools/`.
- To test a study, load its `run.py` with `importlib`. See
  `test_front_ackermann_loads_without_running`.
- Keep each test fast. Do not compile or run Modelica. Do not run a study.
- Use small synthetic inputs. Do not put restricted tire data in a test.
- Write files only under the pytest `tmp_path`. `make unit` fails when
  `bobsim/` changes.
- Import BobSim. Do not copy BobSim code into a test.

## Git in the container

`test_bobsim_pin.py` runs git in the container. In a git worktree, the
`.git` file of the submodule points to a host path. The container cannot
see that path. The two tests then skip and show the reason.

`LHRE_REQUIRE_GIT=1` changes that skip into a failure. CI sets it.

`make unit` checks `bobsim/` again on the host after `pytest`. That check
works in a worktree too.

## CI

`.github/workflows/simulation.yml` has one job, `fast`. It runs on pull
requests that touch `simulation/`, `vehicle/`, `.gitmodules` or the
workflow. It also runs on pushes to `main`. The steps are:

1. Get the BobSim history for the pin check. The checkout gets submodules
   at depth 1, which does not include `739143a`.
2. Build the image.
3. Check that BobSim loads the vehicle: `make smoke`.
4. Run the unit and contract tests: `make unit`.

The job runs no Modelica target and no study. `test_ci_contract.py` fails
if the job runs a `make` target other than `build`, `smoke` or `unit`.
