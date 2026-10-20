import os
import subprocess
from pathlib import Path

import pytest

BOBSIM = Path(__file__).resolve().parents[1] / "bobsim"
FRONT_ANTI_DIVE_SIGN_FIX = "739143a7d195b436c72270cfc28df4b42622226d"
# The container runs as root on files that the CI runner owns.
SAFE_DIRECTORY = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"}


def _git(*args):
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(BOBSIM), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **SAFE_DIRECTORY},
    )


@pytest.fixture(scope="module")
def bobsim_head():
    head = _git("rev-parse", "HEAD")
    if head.returncode != 0:
        reason = f"git cannot read simulation/bobsim in the container: {head.stderr.strip()}"
        if os.environ.get("LHRE_REQUIRE_GIT") == "1":
            pytest.fail(reason)
        pytest.skip(f"{reason}. A git worktree can cause this. Set LHRE_REQUIRE_GIT=1 to fail instead.")
    return head.stdout.strip()


def test_bobsim_contains_the_front_anti_dive_sign_fix(bobsim_head):
    result = _git("merge-base", "--is-ancestor", FRONT_ANTI_DIVE_SIGN_FIX, "HEAD")
    if result.returncode == 0:
        return
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.fail(
            "simulation/bobsim is a shallow clone, so the check cannot see its history. "
            "Run 'git -C simulation/bobsim fetch --unshallow origin'."
        )
    pytest.fail(
        f"BobSim {bobsim_head[:7]} does not contain {FRONT_ANTI_DIVE_SIGN_FIX[:7]}, "
        f"the front anti-dive sign fix (BobSim PR #70). Move the pin with 'make bump-bobsim REF=<ref>'. "
        f"{result.stderr.strip()}"
    )


def test_bobsim_has_no_local_changes(bobsim_head):
    status = _git("status", "--porcelain", "--ignore-submodules=none")
    assert status.returncode == 0, status.stderr
    assert status.stdout == "", (
        f"simulation/bobsim {bobsim_head[:7]} has local changes. Tests run on the pin as committed. "
        f"Run 'make records-undo' or remove the changes.\n{status.stdout}"
    )
