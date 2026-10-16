import re
import subprocess
from pathlib import Path

import pytest
import yaml

SIM = Path(__file__).resolve().parents[1]
WORKFLOW = SIM.parent / ".github" / "workflows" / "simulation.yml"
SMOKE_STEP = "Check that BobSim loads the vehicle"
FAST_MAKE_TARGETS = {"build", "smoke", "unit"}
BOBSIM_SIGN_TEST = "bobsim/tests/test_kinematics.py::test_four_post_anti_dive_sign_is_independent_of_pulse_direction"


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


@pytest.fixture(scope="module")
def fast_job():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
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


def test_make_unit_runs_the_bobsim_anti_dive_sign_test():
    commands = _dry_run("unit")
    assert BOBSIM_SIGN_TEST in commands, "make unit must run BobSim's front anti-dive sign test (PR #70)."
    assert commands.index(BOBSIM_SIGN_TEST) < commands.index("git -C bobsim status")


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
