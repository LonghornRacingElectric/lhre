"""Tests for ruff_baseline.py on a throwaway repo, so they never touch ours."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import ruff_baseline  # noqa: E402

CONFIG = f"""\
[lint]
select = ["F"]

{ruff_baseline.BEGIN}
{ruff_baseline.SECTION}
{ruff_baseline.END}
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "ruff.toml").write_text(CONFIG)
    (tmp_path / "old.py").write_text("import os\n")  # F401, grandfathered
    (tmp_path / "clean.py").write_text("x = 1\n")
    assert ruff_baseline.main(["--root", str(tmp_path), "--write"]) == 0
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_write_records_existing_violations(repo: Path) -> None:
    baseline = ruff_baseline.committed_baseline(repo / "ruff.toml")
    assert baseline == {"old.py": ["F401"]}


def test_fresh_baseline_passes(repo: Path) -> None:
    assert ruff_baseline.main(["--root", str(repo), "--no-growth-from", "HEAD"]) == 0


def test_fixed_file_leaves_a_stale_entry_that_fails(repo: Path, capsys) -> None:
    (repo / "old.py").write_text("x = 2\n")
    assert ruff_baseline.main(["--root", str(repo)]) == 1
    assert "old.py: F401" in capsys.readouterr().out


def test_regenerating_to_absorb_a_new_violation_is_growth(repo: Path, capsys) -> None:
    (repo / "clean.py").write_text("import sys\n")
    assert ruff_baseline.main(["--root", str(repo), "--write"]) == 0
    assert ruff_baseline.main(["--root", str(repo), "--no-growth-from", "HEAD"]) == 1
    assert "clean.py: F401" in capsys.readouterr().out


def test_no_baseline_at_the_base_ref_is_not_growth(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "x.py").write_text("x = 1\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "before ruff")
    (tmp_path / "ruff.toml").write_text(CONFIG)
    (tmp_path / "y.py").write_text("import os\n")
    ruff_baseline.main(["--root", str(tmp_path), "--write"])
    assert ruff_baseline.main(["--root", str(tmp_path), "--no-growth-from", "HEAD"]) == 0


def test_unreadable_base_ref_fails_instead_of_skipping_growth(repo: Path, capsys) -> None:
    # A failed fetch or a typo must not read as "no baseline at the base".
    assert ruff_baseline.main(["--root", str(repo), "--no-growth-from", "origin/nope"]) == 1
    assert "can't read origin/nope" in capsys.readouterr().out


def test_base_with_the_checker_but_no_baseline_fails(tmp_path: Path, capsys) -> None:
    # Only the PR that adds the checker may start from no baseline.
    git(tmp_path, "init", "-q")
    (tmp_path / ruff_baseline.TOOL).parent.mkdir(parents=True)
    (tmp_path / ruff_baseline.TOOL).write_text("")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "checker, baseline deleted")
    (tmp_path / "ruff.toml").write_text(CONFIG)
    (tmp_path / "y.py").write_text("import os\n")
    ruff_baseline.main(["--root", str(tmp_path), "--write"])
    assert ruff_baseline.main(["--root", str(tmp_path), "--no-growth-from", "HEAD"]) == 1
    assert "ruff.toml is missing at HEAD" in capsys.readouterr().out
