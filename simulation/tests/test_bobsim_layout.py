import ast
import importlib
import importlib.util
import os
import re
from pathlib import Path

import pytest
import yaml

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
BOBSIM = SIM / "bobsim"
VEHICLE = Path(os.environ.get("BOBSIM_VEHICLE", REPO / "vehicle" / "vehicle.yml"))
MAP_HINT = "See the old to new map in docs/bobsim.md."

OLD_LAYOUT = re.compile(r"\b_[0-5]_(Utils|VisualSim|EnvelopeSim|StandardSim|OptSim|App)\b|external/BobLib")
LAYOUT_MAP = SIM / "docs" / "bobsim.md"
SKIP_DIRS = {"bobsim", "out", "__pycache__", ".pytest_cache"}


def _lhre_files(root):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(root).parts):
            yield path


def _python_files():
    for folder in ("tools", "studies", "tests"):
        yield from (p for p in _lhre_files(SIM / folder) if p.suffix == ".py")


def _absolute_imports():
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                yield path, node.module, tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    yield path, alias.name, ()


def test_no_old_bobsim_layout_names():
    found = []
    for root in (SIM, REPO / "vehicle"):
        for path in _lhre_files(root):
            if path == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if path == LAYOUT_MAP and line.startswith("|"):
                    continue
                if OLD_LAYOUT.search(line):
                    found.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert not found, f"Old BobSim layout names. {MAP_HINT}\n" + "\n".join(found)


BOBSIM_PACKAGES = {"app", "common", "engines", "optimization", "simulations", "visualization"}


def test_every_bobsim_import_resolves():
    problems = []
    for path, module, names in _absolute_imports():
        if module.split(".")[0] not in BOBSIM_PACKAGES:
            continue
        where = path.relative_to(REPO)
        try:
            imported = importlib.import_module(module)
        except ImportError as error:
            problems.append(f"{where} imports {module}: {error}")
            continue
        missing = [
            name for name in names
            if not hasattr(imported, name) and importlib.util.find_spec(f"{module}.{name}") is None
        ]
        if missing:
            problems.append(f"{where}: {module} has no {missing}")
    assert not problems, f"BobSim imports that do not resolve. {MAP_HINT}\n" + "\n".join(problems)


def _boblib_submodule():
    paths = re.findall(r"^\s*path = (\S+)$", (BOBSIM / ".gitmodules").read_text(encoding="utf-8"), re.M)
    matches = [p for p in paths if p.lower().endswith("boblib")]
    assert len(matches) == 1, f"BobSim registers {paths}. Expected one BobLib submodule."
    return matches[0]


def test_makefile_records_use_the_bobsim_boblib():
    makefile = (SIM / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^BOBLIB := (\S+)$", makefile, re.M)[1] == f"bobsim/{_boblib_submodule()}"


def test_records_plan_writes_into_existing_boblib_folders():
    from app.modelica_generator import plan_modelica_stack

    plan = plan_modelica_stack(VEHICLE, root=BOBSIM)
    assert plan.initialization_error is None, plan.initialization_error
    boblib = (BOBSIM / _boblib_submodule()).resolve()
    planned = [path for path, _ in plan.contents] + list(plan.result.package_orders)
    for path in planned:
        assert path.parent.is_dir(), f"make records would create a new folder {path.parent}"
        assert path.is_relative_to(boblib), f"make records would write {path} outside {boblib}"


def test_check_vehicle_accepts_the_vehicle(monkeypatch):
    from tools import check_vehicle

    monkeypatch.setenv("BOBSIM_VEHICLE", str(VEHICLE))
    assert check_vehicle.main() == 0


def test_check_vehicle_rejects_a_missing_boblib(monkeypatch, tmp_path, capsys):
    from tools import check_vehicle

    data = yaml.safe_load(VEHICLE.read_text(encoding="utf-8"))
    data["paths"]["boblib"] = "no/such/BobLib"
    copy = tmp_path / "vehicle.yml"
    copy.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("BOBSIM_VEHICLE", str(copy))
    assert check_vehicle.main() == 1
    assert "package.mo" in capsys.readouterr().out
