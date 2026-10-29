import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools import readme

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent


@pytest.fixture
def tree(tmp_path):
    sim = tmp_path / "simulation"
    shutil.copytree(SIM / "tools" / "study_template", sim / "tools" / "study_template")
    shutil.copy(SIM / "Makefile", sim / "Makefile")
    (sim / "studies").mkdir()
    (tmp_path / "vehicle").mkdir()
    for name in ("vehicle.yml", "bundle.yml"):
        shutil.copy(REPO / "vehicle" / name, tmp_path / "vehicle" / name)
    return sim


def _new_study(sim, name):
    return subprocess.run(["make", "new-study", f"S={name}"], cwd=sim, capture_output=True, text=True)


def test_new_study_copies_a_template_that_runs_and_fills_its_readme(tree, tmp_path):
    result = _new_study(tree, "demo-study")
    assert result.returncode == 0, result.stdout + result.stderr
    study = tree / "studies" / "demo-study"
    assert sorted(p.name for p in study.iterdir()) == ["README.md", "run.py", "study.yml"]
    text = "".join(p.read_text(encoding="utf-8") for p in study.iterdir())
    assert "STUDY_NAME" not in text and "BUNDLE_ID" not in text
    bundle_id = yaml.safe_load((tmp_path / "vehicle/bundle.yml").read_text(encoding="utf-8"))["bundle_id"]
    assert yaml.safe_load((study / "study.yml").read_text(encoding="utf-8"))["bundle"] == bundle_id

    out = tmp_path / "out"
    out.mkdir()
    (out / "provenance.json").write_text(json.dumps({"study": "demo-study"}), encoding="utf-8")
    env = {**os.environ, "OUT_DIR": str(out), "BOBSIM_VEHICLE": str(tmp_path / "vehicle/vehicle.yml")}
    run = subprocess.run([sys.executable, str(study / "run.py")], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert {"summary.json", "readme.json", "provenance.json"} <= {p.name for p in out.iterdir()}
    readme.fill(study / "README.md", out)
    text = (study / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# demo-study\n") and "**Status: current.**" in text and "TODO<!-- /out -->" not in text


def test_new_study_refuses_a_vehicle_without_a_bundle_id(tree, tmp_path):
    (tmp_path / "vehicle" / "bundle.yml").unlink()
    result = _new_study(tree, "demo-study")
    assert result.returncode != 0
    assert "has no bundle_id" in result.stdout + result.stderr
    assert not (tree / "studies" / "demo-study").exists()


def test_template_run_refuses_a_vehicle_without_a_bundle(tree, tmp_path):
    assert _new_study(tree, "demo-study").returncode == 0
    (tmp_path / "vehicle" / "bundle.yml").unlink()
    env = {**os.environ, "OUT_DIR": str(tmp_path), "BOBSIM_VEHICLE": str(tmp_path / "vehicle/vehicle.yml")}
    run = subprocess.run([sys.executable, str(tree / "studies/demo-study/run.py")], env=env, capture_output=True, text=True)
    assert run.returncode != 0
    assert "the vehicle has None" in run.stderr


@pytest.mark.parametrize("name", ["", "Rear-Stabar", "rear_stabar", "-rear", "rear/stabar", "taken"])
def test_new_study_refuses_a_bad_or_existing_name(tree, name):
    (tree / "studies" / "taken").mkdir()
    before = sorted(p.name for p in (tree / "studies").iterdir())
    result = _new_study(tree, name)
    assert result.returncode != 0
    assert sorted(p.name for p in (tree / "studies").iterdir()) == before
