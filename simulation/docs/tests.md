# Tests

`make test` runs two checks in the container. Run it from `simulation/`
before you finish.

| Target | What it checks | Time |
| ------ | -------------- | ---- |
| `make smoke` | BobSim loads `vehicle/vehicle.yml` and its tires. It checks loading only. It does not check physics. | about 6 s |
| `make unit` | `pytest` runs the files in `tests/` and one BobSim test. Then the target checks that `bobsim/` has no local changes. | about 31 s |
| `make test` | `make smoke`, then `make unit`. | about 40 s |

The times are from a Windows laptop with Docker Desktop, BobSim `fe03bab`.
The BobSim test takes about 6 s of `make unit`.

## What the tests check

- `test_bobsim_layout.py`: lhre code and docs use the BobSim names at the
  pin. Every import resolves. `make records` writes only into the BobLib of
  BobSim.
- `test_bobsim_pin.py`: the checked-out BobSim contains `739143a`, the
  front anti-dive sign fix (BobSim PR #70). BobSim has no local changes.
- `test_aero_ride_heights.py`: the static aero ride heights lie inside the
  CFD grids. BobSim's car passes. Our car does not, so its test is a
  strict xfail. See
  [bobsim.md](bobsim.md#aero-ride-height-references).
- `test_ci_contract.py`: the `make` targets and the CI `fast` job keep the
  shape on this page. `make init` and `make bump-bobsim` keep LF and long
  paths in the nested submodules.

`make unit` also runs one BobSim test by node id:
`bobsim/tests/test_kinematics.py::test_four_post_anti_dive_sign_is_independent_of_pulse_direction`.
It gives the FourPost report code a synthetic jacking force with a known
sign. It checks the sign of the front anti-dive percent. It runs no
Modelica. It fails when a pin reverts the sign fix. It also fails when
the pin does not have the test.

The lhre tests do not check physics results. Later PRs add physics
fixtures.

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
