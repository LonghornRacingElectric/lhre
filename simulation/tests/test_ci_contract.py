import subprocess
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]


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
