import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from study_tree import build_tree, git

from tools import invalidation, provenance
from tools.invalidation import Layout

SIM = Path(__file__).resolve().parents[1]
ARGV = ["studies/demo/run.py", "--quick"]


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path, bundle=True)


def _env(repo, **extra):
    return {
        "STUDY": "demo",
        "BOBSIM_VEHICLE": str(repo / "vehicle/vehicle.yml"),
        "LHRE_IMAGE_ID": "sha256:feed",
        **extra,
    }


def _build(repo, **extra):
    return provenance.build(ARGV, _env(repo, **extra), Layout(repo))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_record_has_every_v2_field(tree):
    record = _build(tree, STUDY_VARIANT="quick")
    assert record["schema"] == "lhre.provenance.v2"
    assert record["study"] == "demo" and record["variant"] == "quick"
    assert record["argv"] == ARGV
    assert record["lhre"] == {"sha": git(tree, "rev-parse", "HEAD"), "dirty": False, "source": "git"}
    assert record["bobsim"]["sha"] == git(tree / "simulation/bobsim", "rev-parse", "HEAD")
    assert record["bobsim"]["dirty"] is False
    boblib = tree / "simulation/bobsim/engines/boblib"
    assert record["boblib"]["sha"] == git(boblib, "rev-parse", "HEAD")
    assert record["boblib"]["gitlink"] == record["boblib"]["sha"]
    assert record["boblib"]["dirty"] is False
    assert record["image"] == {"id": "sha256:feed"}
    assert record["vehicle"] == {
        "path": "vehicle/vehicle.yml",
        "sha256": _sha(tree / "vehicle/vehicle.yml"),
        "stored": "checkout",
    }
    assert record["bundle"]["bundle_id"] == "demo-2027-a"
    assert record["bundle"]["sha256"] == _sha(tree / "vehicle/bundle.yml")
    tires = {tire["id"]: tire for tire in record["tires"]}
    assert tires["demo_front"]["sha256"] == _sha(tree / "vehicle/tires/demo_front.tir")
    assert tires["demo_front"]["path"] == "vehicle/tires/demo_front.tir"
    code = record["study_code"]
    assert code["files"]["simulation/studies/demo/run.py"] == _sha(tree / "simulation/studies/demo/run.py")
    assert code["files"]["simulation/tools/helper.py"] == _sha(tree / "simulation/tools/helper.py")
    assert len(code["sha256"]) == 64
    keys = invalidation.dependency_keys("demo", tree / "vehicle/vehicle.yml", Layout(tree))
    assert record["invalidation"]["keys"] == keys.values
    assert record["invalidation"]["unavailable"] == {}
    assert record["utc"].endswith("+00:00")


def test_record_never_contains_tire_content(tree):
    text = json.dumps(_build(tree))
    assert "FNOMIN" not in text


def test_dirty_trees_are_recorded(tree):
    (tree / "simulation/bobsim/common/vehicle_io.py").write_text("def load():\n    return 2\n")
    (tree / "simulation/bobsim/engines/boblib/BobLib/Records/DemoRecord.mo").write_text("record R end R;\n")
    (tree / "simulation/tools/helper.py").write_text("def scale(x):\n    return 4 * x\n")
    record = _build(tree)
    assert record["bobsim"]["dirty"] is True
    assert record["boblib"]["dirty"] is True
    assert record["lhre"]["dirty"] is True


def test_a_changed_tire_byte_changes_the_record(tree):
    before = _build(tree)
    with (tree / "vehicle/tires/demo_rear.tir").open("ab") as handle:
        handle.write(b" ")
    after = _build(tree)
    tires = lambda r: {t["id"]: t["sha256"] for t in r["tires"]}  # noqa: E731
    assert tires(before)["demo_rear"] != tires(after)["demo_rear"]
    assert tires(before)["demo_front"] == tires(after)["demo_front"]


def test_git_failure_is_recorded_as_null_never_guessed(tree):
    shutil.rmtree(tree / "simulation/bobsim/.git")
    record = _build(tree, BOBSIM_SHA="abc123")
    assert record["bobsim"]["sha"] == "abc123"
    assert record["bobsim"]["source"] == "env:BOBSIM_SHA"
    assert record["bobsim"]["dirty"] is None
    assert record["bobsim"]["error"]
    assert record["invalidation"]["keys"]["bobsim:engines"] is None
    assert "bobsim:engines" in record["invalidation"]["unavailable"]


def test_missing_image_id_is_null(tree):
    record = provenance.build(ARGV, {**_env(tree), "LHRE_IMAGE_ID": ""}, Layout(tree))
    assert record["image"] == {"id": None}


def test_main_writes_provenance_json(tree, tmp_path, monkeypatch):
    out = tmp_path / "out"
    for name, value in {**_env(tree), "OUT_DIR": str(out)}.items():
        monkeypatch.setenv(name, value)
    assert provenance.main(ARGV, layout=Layout(tree)) == 0
    text = (out / "provenance.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text)["schema"] == provenance.SCHEMA


def test_main_fails_when_git_is_required_and_unavailable(tree, tmp_path, monkeypatch, capsys):
    shutil.rmtree(tree / "simulation/bobsim/.git")
    for name, value in {**_env(tree), "OUT_DIR": str(tmp_path / "out"), "LHRE_REQUIRE_GIT": "1"}.items():
        monkeypatch.setenv(name, value)
    assert provenance.main(ARGV, layout=Layout(tree)) == 1
    assert "bobsim" in capsys.readouterr().err
    assert not (tmp_path / "out/provenance.json").exists()


def _dry_run(*args):
    result = subprocess.run(
        ["make", "-n", "study", "COMPOSE=compose", *args], cwd=SIM, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_make_study_records_argv_variant_image_and_git_policy():
    commands = _dry_run("S=front-ackermann", "ARGS=--quick", "V=smoke")
    assert "python tools/provenance.py studies/front-ackermann/run.py --quick &&" in commands
    assert "python studies/front-ackermann/run.py --quick" in commands
    for name in ("STUDY_VARIANT=smoke", "LHRE_IMAGE_ID=", "LHRE_SHA=", "LHRE_REQUIRE_GIT="):
        assert name in commands
    assert commands.index("tools/provenance.py") < commands.index("python studies/front-ackermann/run.py")
