"""A small lhre, BobSim and BobLib tree in a temporary folder, for provenance tests."""

import hashlib
import subprocess
from pathlib import Path

VEHICLE = """\
schema: boblib.vehicle.v1
vehicle:
  name: Demo
  mass_kg: 280.0
paths:
  boblib: engines/boblib/BobLib
  tire_templates: ../../vehicle/tires
front:
  tire:
    template: demo_front
rear:
  tire:
    template: demo_rear
"""

BUNDLE = """\
schema: lhre.vehicle-bundle.v1
bundle_id: demo-2027-a
status: candidate
superseded_by: null
release:
  approved_by: {value: null, status: unavailable, source: none}
members:
  vehicle: {path: vehicle/vehicle.yml, sha256: VEHICLE_SHA256, schema: boblib.vehicle.v1}
frame:
  units: {value: m, status: declared, source: CAD export note}
  datum_z:
    front: {value: 0.0, status: assumed, source: guess}
    rear: {value: 0.0, status: assumed, source: guess}
setup:
  id: {value: baseline, status: declared, source: setup sheet}
torque_topology:
  drive: {front_share: 0.0, rear_share: 1.0, reaction: chassis}
"""

RUN_PY = """\
import os

from app.tire_eval import force
from engines.dynpy.tire import MF52
from tools.helper import scale

# Demo study.
print(scale(force(MF52(), os.environ.get("X", "1"))))
"""

FILES = {
    "vehicle/vehicle.yml": VEHICLE,
    "vehicle/README.md": "# Vehicle\n",
    "vehicle/tires/demo_front.tir": "FNOMIN = 1000\n",
    "vehicle/tires/demo_rear.tir": "FNOMIN = 1100\n",
    "simulation/studies/demo/run.py": RUN_PY,
    "simulation/studies/demo/README.md": "# Demo\n\nResult.\n",
    "simulation/studies/demo/inputs.yml": "toe_deg: {value: 0.0, status: declared, source: sheet}\n",
    "simulation/tools/helper.py": "def scale(x):\n    return 2 * x\n",
    "simulation/tools/unused.py": "def other():\n    return 3\n",
}

BOBSIM_FILES = {
    ".gitignore": "__pycache__/\n*.pyc\nBuild/\n",
    ".gitmodules": '[submodule "engines/boblib"]\n\tpath = engines/boblib\n\turl = https://example.invalid/BobLib.git\n',
    "Dockerfile": "FROM python:3.11\n",
    "requirements.txt": "numpy==2.0.0\n",
    "README.md": "# BobSim\n",
    "common/__init__.py": "",
    "common/vehicle_io.py": "def load():\n    return 1\n",
    "engines/__init__.py": "",
    "engines/dynpy/__init__.py": "",
    "engines/dynpy/tire.py": "class MF52:\n    mu = 1.0\n",
    "engines/dynpy/README.md": "# dynpy\n",
    "simulations/__init__.py": "",
    "simulations/lap/config.yml": "power_w: 32000\n",
    "optimization/__init__.py": "",
    "app/__init__.py": "",
    "app/tire_eval.py": "from engines.dynpy.tire import MF52\n\n\ndef force(tire, x):\n    return tire.mu * float(x)\n",
    "app/server.py": "PORT = 8000\n",
    "visualization/plot.py": "COLOR = 'red'\n",
    "docs/guide.md": "# Guide\n",
    "tests/test_tire.py": "def test_mu():\n    assert True\n",
}

BOBLIB_FILES = {
    "BobLib/package.mo": "package BobLib\nend BobLib;\n",
    "BobLib/Records/package.order": "VehicleRecord\n",
    "Tests/package.mo": "package Tests\nend Tests;\n",
    "README.md": "# BobLib\n",
}


def git(repo: Path, *args: str) -> str:
    identity = ["-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    result = subprocess.run(
        ["git", *identity, "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())


def _commit_repo(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")


def build_tree(base: Path, *, bundle: bool = False) -> Path:
    """Return the lhre repo root. BobSim and BobLib are separate git repos inside it."""
    repo = base / "lhre"
    bobsim = repo / "simulation" / "bobsim"
    boblib = bobsim / "engines" / "boblib"
    _write(boblib, BOBLIB_FILES)
    _commit_repo(boblib)
    _write(bobsim, BOBSIM_FILES)
    _commit_repo(bobsim)
    _write(repo, FILES)
    if bundle:
        vehicle_sha256 = hashlib.sha256(VEHICLE.encode()).hexdigest()
        _write(repo, {"vehicle/bundle.yml": BUNDLE.replace("VEHICLE_SHA256", vehicle_sha256)})
    (repo / ".gitignore").write_text("simulation/bobsim/\nsimulation/out/\n")
    _commit_repo(repo)
    return repo
