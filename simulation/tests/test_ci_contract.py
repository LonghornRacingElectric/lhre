import re
import subprocess
from pathlib import Path

import pytest
import yaml

SIM = Path(__file__).resolve().parents[1]
WORKFLOW = SIM.parent / ".github" / "workflows" / "simulation.yml"
SMOKE_STEP = "Check that BobSim loads the vehicle"
FAST_MAKE_TARGETS = {"build", "smoke", "unit", "check-vehicle"}
CAR_JOBS = ("four-post", "full-regeneration")
CAR_MAKE_TARGETS = {"build", "car", "four-post-acceptance"}
STANDARD_EVALS = {
    "standard-eval-ramp-steer",
    "standard-eval-steady-state",
    "standard-eval-transient",
    "standard-eval-four-post",
    "standard-eval-all",
}
CAR_INPUTS = ["vehicle/vehicle.yml", "simulation/bobsim", "simulation/tools/car.sh", ".github/workflows/simulation.yml"]
NOT_CAR_INPUTS = ["simulation/docs/bobsim.md", "simulation/studies/front-ackermann/README.md", "simulation/bobsim.md"]


def _dry_run(target, *args):
    result = subprocess.run(
        ["make", "-n", target, "COMPOSE=compose", *args], cwd=SIM, capture_output=True, text=True
    )
    assert result.returncode == 0, f"make -n {target} failed:\n{result.stderr}"
    return result.stdout


@pytest.mark.parametrize("target", ["init", "bump-bobsim"])
def test_submodule_checkouts_keep_lf_and_long_paths(target):
    commands = _dry_run(target, "REF=HEAD").splitlines()
    update = max(i for i, line in enumerate(commands) if "submodule update" in line)
    assert any(
        "submodule foreach --recursive" in line and "core.autocrlf false" in line and "core.longpaths true" in line
        for line in commands[update + 1:]
    ), (
        f"make {target} gets nested submodules but does not set core.autocrlf false and "
        "core.longpaths true in them. Plain git then reports BobLib as changed on Windows."
    )


def _make_calls(job):
    return [
        (target, args.strip())
        for step in job["steps"]
        for target, args in re.findall(r"\bmake\s+(\S+)([^\n]*)", step.get("run", ""))
    ]


def _step_index(job, text):
    matches = [index for index, step in enumerate(job["steps"]) if text in step.get("run", "")]
    assert matches, f"No step runs {text!r}."
    return matches[0]


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def jobs(workflow):
    return workflow["jobs"]


@pytest.fixture(scope="module")
def fast_job(jobs):
    assert "fast" in jobs, f"{WORKFLOW.name} has no fast job. It has {sorted(jobs)}."
    return jobs["fast"]


def test_make_smoke_only_loads_the_vehicle():
    commands = _dry_run("smoke")
    assert "tools/check_vehicle.py" in commands
    assert "pytest" not in commands


def test_make_unit_checks_bobsim_after_the_tests():
    commands = _dry_run("unit")
    assert "pytest" in commands
    assert "LHRE_REQUIRE_GIT" in commands
    assert "git -C bobsim status" in commands
    assert commands.index("pytest") < commands.index("git -C bobsim status")



def test_make_test_runs_smoke_then_unit():
    commands = _dry_run("test")
    assert "tools/check_vehicle.py" in commands
    assert "pytest" in commands
    assert commands.index("tools/check_vehicle.py") < commands.index("pytest")


def test_fast_job_runs_smoke_and_unit_as_separate_steps(fast_job):
    runs = {step.get("name"): step.get("run", "").strip() for step in fast_job["steps"]}
    assert runs.get(SMOKE_STEP) == "make smoke"
    unit_steps = [name for name, run in runs.items() if run == "make unit"]
    assert len(unit_steps) == 1 and unit_steps[0] != SMOKE_STEP


def test_fast_job_runs_only_fast_make_targets(fast_job):
    targets = {
        target
        for step in fast_job["steps"]
        for target in re.findall(r"\bmake\s+(\S+)", step.get("run", ""))
    }
    assert targets <= FAST_MAKE_TARGETS, f"The fast job runs {sorted(targets - FAST_MAKE_TARGETS)}."


def test_fast_job_fails_when_git_checks_cannot_run(fast_job):
    assert str(fast_job.get("env", {}).get("LHRE_REQUIRE_GIT")) == "1"


def test_pull_requests_check_bundle_immutability_against_the_base_branch(fast_job):
    steps = [step for step in fast_job["steps"] if re.search(r"\bmake\s+check-vehicle\b", step.get("run", ""))]
    assert len(steps) == 1, "The fast job does not run make check-vehicle, so CI never checks bundle_id immutability."
    step = steps[0]
    assert "github.event_name == 'pull_request'" in str(step.get("if", ""))
    run = step["run"]
    assert re.search(r"\bBASE=\S*GITHUB_BASE_REF", run), "make check-vehicle needs BASE set to the base branch."
    fetch = re.search(r"\bgit fetch\b.*GITHUB_BASE_REF", run)
    assert fetch and fetch.start() < run.index("make check-vehicle"), "The step must fetch the base branch first."


def test_make_car_and_the_acceptance_have_targets():
    assert "tools/car.sh" in _dry_run("car", "T=standard-eval-four-post")
    acceptance = _dry_run("four-post-acceptance", "T=standard-eval-four-post")
    assert "tools/four_post_acceptance.py out/car/standard-eval-four-post" in acceptance


def test_four_post_job_runs_make_car_then_the_acceptance(jobs):
    job = jobs["four-post"]
    calls = _make_calls(job)
    assert {target for target, _ in calls} <= CAR_MAKE_TARGETS, calls
    assert ("car", "T=standard-eval-four-post") in calls
    assert ("four-post-acceptance", "T=standard-eval-four-post") in calls
    assert _step_index(job, "make car") < _step_index(job, "make four-post-acceptance")


def test_four_post_job_runs_only_when_the_car_inputs_change(jobs):
    job = jobs["four-post"]
    assert job["needs"] == "changes"
    assert "needs.changes.outputs.car != 'false'" in job["if"]
    pattern = re.search(r"paths='([^']+)'", jobs["changes"]["steps"][0]["run"])[1]
    for path in CAR_INPUTS:
        assert re.search(pattern, path), f"The four-post job does not run when {path} changes."
    for path in NOT_CAR_INPUTS:
        assert not re.search(pattern, path), f"The four-post job runs when only {path} changes."


@pytest.mark.parametrize("name", CAR_JOBS)
def test_car_jobs_upload_the_run_record_but_no_tire_file(jobs, name):
    uploads = [step for step in jobs[name]["steps"] if "upload-artifact" in step.get("uses", "")]
    assert len(uploads) == 1
    assert "!cancelled()" in uploads[0]["if"]
    paths = uploads[0]["with"]["path"].split()
    for wanted in ("identity.json", "vehicle.yml", "run.log", "manifest.json", "*metrics*"):
        assert any(path.endswith(wanted) for path in paths), f"{name} does not upload {wanted}."
    assert "!simulation/out/car/**/*.tir" in paths
    assert not [path for path in paths if path.endswith(".tir") and not path.startswith("!")]


def test_full_regeneration_runs_only_standard_evaluations_on_a_schedule_or_by_hand(workflow, jobs):
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"], f"{WORKFLOW.name} has no schedule."
    assert triggers["workflow_dispatch"]["inputs"]["full_regeneration"]["default"] is False
    job = jobs["full-regeneration"]
    assert "pull_request" not in job["if"] and "push" not in job["if"]
    assert "schedule" in job["if"] and "inputs.full_regeneration" in job["if"]
    calls = _make_calls(job)
    assert {target for target, _ in calls} <= CAR_MAKE_TARGETS, calls
    evals = {args.removeprefix("T=") for target, args in calls if target == "car"}
    assert evals and evals <= STANDARD_EVALS, evals
    text = yaml.safe_dump(job)
    for forbidden in ("opt-", "envelope", "make study", "lap-eval", "sweep"):
        assert forbidden not in text, f"full-regeneration runs {forbidden}."


def test_only_the_car_jobs_run_bobsim_targets(jobs):
    for name, job in jobs.items():
        if name in CAR_JOBS:
            continue
        targets = {target for target, _ in _make_calls(job)}
        assert not targets & {"car", "bobsim", "records"}, f"{name} runs {sorted(targets)}."
