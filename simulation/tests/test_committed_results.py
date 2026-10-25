import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from study_tree import build_tree

from tools import invalidation, provenance, readme
from tools.invalidation import Layout

SIM = Path(__file__).resolve().parents[1]
REAL = Layout(SIM.parent)
REAL_VEHICLE = REAL.repo / "vehicle" / "vehicle.yml"
STUDY = "simulation/studies/demo"
FIX = "Run 'make study S={study}' again, or mark the README with the line '**Status: historical.**'."
REQUIRE = "A git worktree can cause this. Set LHRE_REQUIRE_GIT=1 to fail instead."


def _only_git(verdict):
    prefix = f"{invalidation.CANNOT_READ}: git cannot"
    return bool(verdict.unknown) and all(detail.startswith(prefix) for _, detail in verdict.unknown)


def gate(path: Path, vehicle: Path, layout: Layout) -> tuple[str, str]:
    study = path.parent.name
    status = invalidation.readme_status(path.read_text(encoding="utf-8"))
    if status in invalidation.MARKED:
        return "ok", f"README status: {status}"
    try:
        verdict = invalidation.check(invalidation.load_record(path), vehicle, layout)
    except (OSError, ValueError) as error:
        return "fail", f"{study}: the README provenance cannot be read: {error} {FIX.format(study=study)}"
    if verdict.status == "current":
        return "ok", "CURRENT"
    details = "\n".join(f"  {key}  {detail}" for key, detail in [*verdict.changed, *verdict.unknown])
    if verdict.status == "unknown" and _only_git(verdict) and os.environ.get("LHRE_REQUIRE_GIT") != "1":
        return "skip", f"{study}: git cannot read BobSim or BobLib, so the result is UNKNOWN. {REQUIRE}"
    return "fail", f"{study}: the committed result is {verdict.status.upper()}. {FIX.format(study=study)}\n{details}"


@pytest.mark.parametrize("path", sorted((REAL.sim / "studies").glob("*/README.md")), ids=lambda p: p.parent.name)
def test_committed_result_is_current_or_marked(path):
    outcome, reason = gate(path, REAL_VEHICLE, REAL)
    if outcome == "skip":
        pytest.skip(reason)
    assert outcome == "ok", reason


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path, bundle=True)


def _commit_result(repo: Path, tmp_path: Path, status_line: str = "") -> Path:
    env = {"STUDY": "demo", "BOBSIM_VEHICLE": str(repo / "vehicle/vehicle.yml")}
    record = provenance.build(["studies/demo/run.py"], env, Layout(repo))
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    path = repo / STUDY / "README.md"
    text = f"# Demo\n\n{status_line}\n\n## Provenance\n\n<!-- out:provenance.json -->\n\n<!-- /out -->\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    readme.fill(path, out)
    return path


def _change_vehicle(repo: Path) -> None:
    vehicle = repo / "vehicle/vehicle.yml"
    vehicle.write_bytes(vehicle.read_bytes().replace(b"mass_kg: 280.0", b"mass_kg: 281.0"))
    bundle = repo / "vehicle/bundle.yml"
    data = yaml.safe_load(bundle.read_text(encoding="utf-8"))
    data["members"]["vehicle"]["sha256"] = invalidation.sha256_file(vehicle)
    bundle.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")


def _stale(repo, capsys, *target):
    argv = ["stale", *target, "--vehicle", str(repo / "vehicle/vehicle.yml")]
    code = invalidation.main(argv, layout=Layout(repo))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_readme_record_is_the_filled_provenance_json(tree, tmp_path):
    path = _commit_result(tree, tmp_path)
    assert invalidation.load_record(path) == json.loads((tmp_path / "out/provenance.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("target", ["demo", f"{STUDY}/README.md"], ids=["study name", "README path"])
def test_stale_check_reads_a_study_name_or_a_readme(tree, tmp_path, capsys, target):
    _commit_result(tree, tmp_path)
    target = str(tree / target) if target.endswith(".md") else target
    code, out, err = _stale(tree, capsys, target)
    assert (code, out.splitlines()[0]) == (0, "CURRENT demo"), err
    _change_vehicle(tree)
    code, out, _ = _stale(tree, capsys, target)
    assert code == 1
    assert out.splitlines()[0] == "STALE demo"
    assert any(line.split()[0] == "vehicle" for line in out.splitlines()[1:])


def test_stale_check_without_a_target_checks_every_study(tree, tmp_path, capsys):
    _commit_result(tree, tmp_path)
    code, out, _ = _stale(tree, capsys)
    assert (code, out.splitlines()[0]) == (0, "CURRENT demo")
    other = tree / "simulation/studies/other"
    other.mkdir()
    (other / "README.md").write_text("# Other\n\nNo provenance.\n", encoding="utf-8")
    _change_vehicle(tree)
    code, out, err = _stale(tree, capsys)
    assert code == 2
    assert "STALE demo" in out.splitlines()
    assert "other" in err and "provenance" in err


def test_stale_check_prints_the_readme_status(tree, tmp_path, capsys):
    _commit_result(tree, tmp_path, "**Status: historical.** The car changed.")
    _change_vehicle(tree)
    code, out, _ = _stale(tree, capsys, "demo")
    assert code == 1
    assert "  README status: historical" in out.splitlines()


@pytest.mark.parametrize("text", ["# Demo\n", "<!-- out:provenance.json -->\n\nnot json\n\n<!-- /out -->\n"])
def test_stale_check_cannot_read_a_readme_without_a_record(tree, capsys, text):
    (tree / STUDY / "README.md").write_text(text, encoding="utf-8")
    code, _, err = _stale(tree, capsys, "demo")
    assert code == 2
    assert "Cannot read" in err


def test_stale_check_rejects_a_name_that_is_not_a_study(tree, capsys):
    code, _, err = _stale(tree, capsys, "no-such-study")
    assert code == 2 and "no-such-study" in err


def test_gate_passes_a_current_result(tree, tmp_path):
    path = _commit_result(tree, tmp_path)
    assert gate(path, tree / "vehicle/vehicle.yml", Layout(tree)) == ("ok", "CURRENT")


def test_gate_fails_an_unmarked_stale_result(tree, tmp_path):
    path = _commit_result(tree, tmp_path)
    _change_vehicle(tree)
    outcome, reason = gate(path, tree / "vehicle/vehicle.yml", Layout(tree))
    assert outcome == "fail"
    assert "STALE" in reason and "make study S=demo" in reason and "vehicle" in reason


@pytest.mark.parametrize(
    "status_line",
    ["**Status: historical.** The car changed.", "**Status: stale.**", "| | |\n| --- | --- |\n| Status | historical |"],
    ids=["historical line", "stale line", "status table row"],
)
def test_gate_passes_a_marked_stale_result(tree, tmp_path, status_line):
    path = _commit_result(tree, tmp_path, status_line)
    _change_vehicle(tree)
    outcome, _ = gate(path, tree / "vehicle/vehicle.yml", Layout(tree))
    assert outcome == "ok"


def test_gate_does_not_accept_another_status(tree, tmp_path):
    path = _commit_result(tree, tmp_path, "**Status: current.**")
    _change_vehicle(tree)
    assert gate(path, tree / "vehicle/vehicle.yml", Layout(tree))[0] == "fail"


def test_gate_fails_an_unmarked_readme_without_a_record(tree):
    path = tree / STUDY / "README.md"
    outcome, reason = gate(path, tree / "vehicle/vehicle.yml", Layout(tree))
    assert outcome == "fail" and "cannot be read" in reason


def test_gate_fails_an_unmarked_version_1_record(tree, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    vehicle_sha256 = invalidation.sha256_file(tree / "vehicle/vehicle.yml")
    record = {"study": "demo", "bobsim_sha": "abc", "vehicle_sha256": vehicle_sha256}
    (out / "provenance.json").write_text(json.dumps(record), encoding="utf-8")
    path = tree / STUDY / "README.md"
    path.write_text("# Demo\n\n<!-- out:provenance.json -->\n\n<!-- /out -->\n", encoding="utf-8")
    readme.fill(path, out)
    outcome, reason = gate(path, tree / "vehicle/vehicle.yml", Layout(tree))
    assert outcome == "fail" and "UNKNOWN" in reason


@pytest.mark.parametrize(("require", "expected"), [("", "skip"), ("1", "fail")])
def test_gate_skips_an_unknown_from_git_unless_git_is_required(tree, tmp_path, monkeypatch, require, expected):
    path = _commit_result(tree, tmp_path)
    shutil.rmtree(tree / "simulation/bobsim/.git")
    monkeypatch.setenv("LHRE_REQUIRE_GIT", require)
    outcome, reason = gate(path, tree / "vehicle/vehicle.yml", Layout(tree))
    assert outcome == expected
    assert "UNKNOWN" in reason


def test_gate_does_not_skip_a_change_when_git_is_unavailable(tree, tmp_path, monkeypatch):
    path = _commit_result(tree, tmp_path)
    shutil.rmtree(tree / "simulation/bobsim/.git")
    _change_vehicle(tree)
    monkeypatch.delenv("LHRE_REQUIRE_GIT", raising=False)
    assert gate(path, tree / "vehicle/vehicle.yml", Layout(tree))[0] == "fail"


def _dry_run(*args):
    result = subprocess.run(["make", "-n", "stale", "COMPOSE=compose", *args], cwd=SIM, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_make_stale_checks_one_study_or_every_study():
    assert "sim python tools/invalidation.py stale front-ackermann" in _dry_run("S=front-ackermann")
    assert _dry_run().strip().endswith("sim python tools/invalidation.py stale")
