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
- `test_ci_contract.py`: the `make` targets and the CI jobs keep the
  shape on this page. `make init` and `make bump-bobsim` keep LF and long
  paths in the nested submodules.
- `test_committed_results.py`: each committed study result is current, or
  its README marks it historical or stale. See
  [studies.md](studies.md#status).
- `test_car_wrapper.py`: `make car` on a small fake BobSim git repo. The
  target sees our vehicle. `bobsim/` is restored after a pass, a failed
  target, failed records and an interrupt. A target that leaves `bobsim/`
  changed fails. An executable older than the records fails. No `.tir`
  file reaches the outputs. It runs no Modelica.
- `test_four_post_acceptance.py`: the FourPost acceptance on small
  synthetic metrics files and run manifests. The metrics files use the
  `fe03bab` format, and a format with a `status` column.
- `test_vehicle_bundle.py`: the bundle rules of `make check-vehicle`.
- `test_vehicle_checks.py`: `tools/vehicle_checks.py` fails a bad vehicle
  with its key path: units, missing hardpoints, the stored side, the frame
  and singular geometry. The bundle check runs these checks.
- `test_anti_oracle.py`: the analytical oracle in `anti_oracle.py` against
  closed forms. Cases: horizontal and inclined parallel links, known
  instant centers, front and rear signs, Fx sign reversal, inboard and
  outboard brakes, and wrench closure.
- `test_anti_paths.py`: `tools/anti_paths.py` against the oracle. A changed
  brake reaction alone does not change inboard-drive anti-squat. A negated
  sign fails.


The anti tests check a model against an oracle. They do not validate the
car. See [anti-paths.md](anti-paths.md).

## Add a test

- Put the file in `tests/` and name it `test_<topic>.py`. Do not put tests
  in a study folder or in `tools/`.
- Keep each test fast. Do not compile or run Modelica. Do not run a study.
- Use small synthetic inputs. Do not put restricted tire data in a test.
- Write files only under the pytest `tmp_path`. `make unit` fails when
  `bobsim/` changes.
- Import BobSim. Do not copy BobSim code into a test.

## Git in the container

Some tests and `tools/provenance.py` run git in the container. In a git
worktree, the `.git` file of the submodule points to a host path. The
container cannot see that path. Those checks then skip and show the reason.

`LHRE_REQUIRE_GIT=1` changes that skip into a failure. CI sets it.

`make unit` checks `bobsim/` again on the host after `pytest`. That check
works in a worktree too.

## CI

`.github/workflows/simulation.yml` runs on pull requests that touch
`simulation/`, `vehicle/`, `.gitmodules` or the workflow. It also runs on
pushes to `main`, every Monday and by hand. It has these jobs.

### fast

1. Build the image with the GitHub Actions layer cache. A layer is built
   again only when the BobSim `Dockerfile` or `requirements.txt` changes.
   Only then does CI download from the OpenModelica servers, so an
   OpenModelica outage blocks only a run that changes the image.
2. Check that BobSim loads the vehicle: `make smoke`.
3. On a pull request only, check that a changed bundle member has a new
   `bundle_id`. The step fetches the base branch, then runs
   `make check-vehicle BASE=origin/<base branch>`.
4. Run the unit and contract tests: `make unit`.

The job runs no Modelica target and no study. `test_ci_contract.py` fails
if the job runs a `make` target other than `build`, `smoke`, `unit` or
`check-vehicle`. It also fails if pull requests do not run the step 4 check.

### four-post

It runs FourPost on our car: `make car T=standard-eval-four-post`, then
`make four-post-acceptance`. See
[bobsim.md](bobsim.md#run-a-bobsim-target-on-our-car).

The `changes` job decides if it runs. It runs when a pull request or a
push changes `vehicle/`, the BobSim pin, `simulation/tools/`,
`simulation/Makefile`, `simulation/docker-compose.yml` or the workflow.
If the `changes` job fails, `four-post` runs. A manual run always runs it.
The weekly run does not, because `full-regeneration` covers FourPost.

At BobSim `fe03bab` the acceptance fails for our car. FourPost runs its
own anti-roll bar rates, not ours. See
[bobsim.md](bobsim.md#run-a-bobsim-target-on-our-car).

### full-regeneration

It runs every standard evaluation on our car:
`make car T=standard-eval-all`, then `make four-post-acceptance`. It runs
every Monday, and on a manual run with `full_regeneration` set. It runs
no optimization, envelope, lap or study target.

### Artifacts

`four-post` and `full-regeneration` upload `out/car/`: `identity.json`,
`four_post_acceptance.json`, the executed `vehicle.yml`, the logs, the
metrics, and the manifest, overrides and `run.log` of each Modelica run.
`four-post` also uploads the FourPost result CSVs. No job uploads a
`.tir` file.
