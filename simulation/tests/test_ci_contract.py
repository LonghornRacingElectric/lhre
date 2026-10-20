import re
import subprocess
from pathlib import Path

import pytest
import yaml

SIM = Path(__file__).resolve().parents[1]
WORKFLOW = SIM.parent / ".github" / "workflows" / "simulation.yml"
SMOKE_STEP = "Check that BobSim loads the vehicle"


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


def test_ci_does_not_call_the_test_run_a_loading_check():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    names = [
        step.get("name")
        for job in jobs.values()
        for step in job.get("steps", [])
        if re.search(r"\bmake\s+test\b", step.get("run", ""))
    ]
    assert SMOKE_STEP not in names, f"The CI step '{SMOKE_STEP}' runs make test, which also runs pytest."
