import json
import shutil
from pathlib import Path

import pytest
import yaml
from study_tree import build_tree, git

from tools import invalidation
from tools.invalidation import Layout

SIM = Path(__file__).resolve().parents[1]
REAL = Layout(SIM.parent)


@pytest.fixture
def tree(tmp_path):
    return build_tree(tmp_path, bundle=True)


def _keys(repo, vehicle=None):
    layout = Layout(repo)
    keys = invalidation.dependency_keys("demo", vehicle or repo / "vehicle/vehicle.yml", layout)
    return keys.values


def _edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is not in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def _append(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _set_bundle(repo, change):
    path = repo / "vehicle/bundle.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")


BOBSIM = "simulation/bobsim"
BOBLIB = "simulation/bobsim/engines/boblib"
STUDY = "simulation/studies/demo"

COSMETIC = {
    "study README": lambda r: _append(r / STUDY / "README.md", "More words.\n"),
    "study figure": lambda r: (r / STUDY / "result.png").write_bytes(b"\x89PNG fake"),
    "vehicle README": lambda r: _append(r / "vehicle/README.md", "Note.\n"),
    "comment in run.py": lambda r: _edit(r / STUDY / "run.py", "# Demo study.", "# A demo study, longer."),
    "blank lines in a tool": lambda r: _append(r / "simulation/tools/helper.py", "\n\n# trailing note\n"),
    "bytecode cache": lambda r: _append(r / STUDY / "__pycache__/run.cpython-311.pyc", "junk"),
    "BobSim docs folder": lambda r: _append(r / BOBSIM / "docs/guide.md", "More.\n"),
    "BobSim README in a physics folder": lambda r: _append(r / BOBSIM / "engines/dynpy/README.md", "More.\n"),
    "BobSim top README": lambda r: _append(r / BOBSIM / "README.md", "More.\n"),
    "BobSim tests": lambda r: _append(r / BOBSIM / "tests/test_tire.py", "x = 1\n"),
    "BobSim app module the study does not import": lambda r: _append(r / BOBSIM / "app/server.py", "HOST = 'x'\n"),
    "BobSim visualization": lambda r: _append(r / BOBSIM / "visualization/plot.py", "WIDTH = 2\n"),
    "BobSim ignored build output": lambda r: _append(r / BOBSIM / "simulations/Build/result.csv", "1,2\n"),
    "BobLib tests": lambda r: _append(r / BOBLIB / "Tests/package.mo", "// more\n"),
    "BobLib README": lambda r: _append(r / BOBLIB / "README.md", "More.\n"),
    "bundle status": lambda r: _set_bundle(r, lambda d: d.update(status="released")),
    "bundle leaf status and source": lambda r: _set_bundle(
        r, lambda d: d["frame"]["datum_z"]["front"].update(status="measured", source="CMM report 12")
    ),
}

PHYSICS = {
    "vehicle byte": (lambda r: _append(r / "vehicle/vehicle.yml", " "), "vehicle"),
    "front tire byte": (lambda r: _append(r / "vehicle/tires/demo_front.tir", " "), "tire:demo_front"),
    "run.py code": (lambda r: _edit(r / STUDY / "run.py", "scale(force", "scale(2 * force"), f"lhre:{STUDY}/run.py"),
    "imported tool": (
        lambda r: _edit(r / "simulation/tools/helper.py", "2 * x", "3 * x"),
        "lhre:simulation/tools/helper.py",
    ),
    "study inputs": (lambda r: _edit(r / STUDY / "inputs.yml", "0.0", "0.1"), f"lhre:{STUDY}/inputs.yml"),
    "new import": (
        lambda r: _edit(r / STUDY / "run.py", "import os\n", "import os\n\nimport tools.unused\n"),
        {f"lhre:{STUDY}/run.py", "lhre:simulation/tools/unused.py"},
    ),
    "BobSim engine": (lambda r: _edit(r / BOBSIM / "engines/dynpy/tire.py", "1.0", "0.9"), "bobsim:engines"),
    "BobSim config data": (
        lambda r: _edit(r / BOBSIM / "simulations/lap/config.yml", "32000", "30000"),
        "bobsim:simulations",
    ),
    "BobSim new untracked module": (lambda r: _append(r / BOBSIM / "common/extra.py", "K = 1\n"), "bobsim:common"),
    "BobSim requirements": (
        lambda r: _edit(r / BOBSIM / "requirements.txt", "2.0.0", "2.1.0"),
        "bobsim:requirements.txt",
    ),
    "BobSim app module the study imports": (
        lambda r: _edit(r / BOBSIM / "app/tire_eval.py", "tire.mu *", "0.5 * tire.mu *"),
        "bobsim:app/tire_eval.py",
    ),
    "BobLib model": (
        lambda r: _edit(r / BOBLIB / "BobLib/package.mo", "end BobLib;", "  constant Real g = 9.81;\nend BobLib;"),
        "boblib:BobLib",
    ),
    "BobLib records written": (
        lambda r: _append(r / BOBLIB / "BobLib/Records/DemoRecord.mo", "record DemoRecord end DemoRecord;\n"),
        "boblib:BobLib",
    ),
    "bundle id": (lambda r: _set_bundle(r, lambda d: d.update(bundle_id="demo-2027-b")), "bundle_id"),
    "bundle torque topology": (
        lambda r: _set_bundle(r, lambda d: d["torque_topology"]["drive"].update(reaction="upright")),
        "bundle_physics",
    ),
    "bundle datum value": (
        lambda r: _set_bundle(r, lambda d: d["frame"]["datum_z"]["front"].update(value=0.01)),
        "bundle_physics",
    ),
}


@pytest.mark.parametrize("edit", COSMETIC.values(), ids=COSMETIC.keys())
def test_docs_and_cosmetic_changes_keep_every_key(tree, edit):
    before = _keys(tree)
    edit(tree)
    after = _keys(tree)
    assert after == before


@pytest.mark.parametrize(("edit", "key"), PHYSICS.values(), ids=PHYSICS.keys())
def test_physics_input_changes_change_their_key(tree, edit, key):
    before = _keys(tree)
    edit(tree)
    after = _keys(tree)
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed == ({key} if isinstance(key, str) else key)


def test_keys_are_content_digests_not_commits(tree):
    keys = _keys(tree)
    assert all(value for value in keys.values()), keys
    head = git(tree / BOBSIM, "rev-parse", "HEAD")
    assert head not in keys.values()
    git(tree / BOBSIM, "commit", "-q", "--allow-empty", "-m", "empty commit")
    assert _keys(tree) == keys


def test_every_key_group_is_present(tree):
    keys = _keys(tree)
    assert keys["vehicle"] and keys["bundle_id"] == "demo-2027-a"
    assert {"tire:demo_front", "tire:demo_rear"} <= keys.keys()
    assert f"lhre:{STUDY}/run.py" in keys and "lhre:simulation/tools/helper.py" in keys
    assert "lhre:simulation/tools/unused.py" not in keys
    bobsim = {name for name in keys if name.startswith("bobsim:")}
    assert {f"bobsim:{entry}" for entry in invalidation.BOBSIM_PHYSICS} <= bobsim
    assert "bobsim:app/tire_eval.py" in bobsim and "bobsim:app/__init__.py" in bobsim
    excluded = ("bobsim:docs", "bobsim:visualization", "bobsim:app/server")
    assert not {name for name in bobsim if name.startswith(excluded)}
    assert "boblib:BobLib" in keys


def test_no_bundle_is_a_recorded_value(tmp_path):
    repo = build_tree(tmp_path, bundle=False)
    keys = _keys(repo)
    assert keys["bundle_id"] == "absent" and keys["bundle_physics"] == "absent"


def test_bundle_id_can_be_a_metadata_triple(tree):
    _set_bundle(tree, lambda d: d.update(bundle_id={"value": "demo-2027-a", "status": "declared", "source": "x"}))
    assert _keys(tree)["bundle_id"] == "demo-2027-a"


def test_vehicle_outside_the_repo_is_hashed_by_its_bytes(tree, tmp_path):
    copy = tmp_path / "candidate" / "vehicle.yml"
    copy.parent.mkdir()
    shutil.copyfile(tree / "vehicle/vehicle.yml", copy)
    keys = _keys(tree, vehicle=copy)
    assert keys["vehicle"] == _keys(tree)["vehicle"]
    assert keys["bundle_id"] == "absent"


def test_git_unavailable_gives_no_value_and_a_reason(tree):
    shutil.rmtree(tree / BOBSIM / ".git")
    keys = invalidation.dependency_keys("demo", tree / "vehicle/vehicle.yml", Layout(tree))
    assert keys.values["bobsim:engines"] is None
    assert "git" in keys.unavailable["bobsim:engines"]
    assert keys.values["vehicle"]


def test_missing_tire_gives_no_value_and_a_reason(tree):
    (tree / "vehicle/tires/demo_rear.tir").unlink()
    keys = invalidation.dependency_keys("demo", tree / "vehicle/vehicle.yml", Layout(tree))
    assert keys.values["tire:demo_rear"] is None
    assert "demo_rear" in keys.unavailable["tire:demo_rear"]


def _record(repo, tmp_path):
    keys = invalidation.dependency_keys("demo", repo / "vehicle/vehicle.yml", Layout(repo))
    path = tmp_path / "provenance.json"
    record = {
        "schema": invalidation.SCHEMA,
        "study": "demo",
        "invalidation": {"keys": keys.values, "unavailable": keys.unavailable},
    }
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _stale(repo, path, capsys, *extra):
    argv = ["stale", str(path), "--vehicle", str(repo / "vehicle/vehicle.yml"), *extra]
    code = invalidation.main(argv, layout=Layout(repo))
    return code, capsys.readouterr().out


def test_stale_check_reports_current(tree, tmp_path, capsys):
    path = _record(tree, tmp_path)
    COSMETIC["study README"](tree)
    code, out = _stale(tree, path, capsys)
    assert code == 0
    assert out.startswith("CURRENT demo")


def test_stale_check_prints_stale_and_the_changed_keys(tree, tmp_path, capsys):
    path = _record(tree, tmp_path)
    _append(tree / "vehicle/vehicle.yml", " ")
    _edit(tree / BOBSIM / "engines/dynpy/tire.py", "1.0", "0.9")
    code, out = _stale(tree, path, capsys)
    assert code == 1
    lines = out.splitlines()
    assert lines[0] == "STALE demo"
    assert any(line.split()[0] == "vehicle" for line in lines[1:])
    assert any(line.split()[0] == "bobsim:engines" for line in lines[1:])
    assert not any(line.split()[0].startswith("tire:") for line in lines[1:])


def test_stale_check_reports_added_and_removed_keys(tree, tmp_path, capsys):
    path = _record(tree, tmp_path)
    PHYSICS["new import"][0](tree)
    code, out = _stale(tree, path, capsys)
    assert code == 1
    assert "lhre:simulation/tools/unused.py" in out and "added" in out


def test_stale_check_never_calls_an_unavailable_key_current(tree, tmp_path, capsys):
    path = _record(tree, tmp_path)
    shutil.rmtree(tree / BOBSIM / ".git")
    code, out = _stale(tree, path, capsys)
    assert code == invalidation.EXIT_UNKNOWN
    assert out.startswith("UNKNOWN demo")
    assert "bobsim:engines" in out


def test_a_changed_key_wins_over_an_unknown_key(tree, tmp_path, capsys):
    path = _record(tree, tmp_path)
    shutil.rmtree(tree / BOBSIM / ".git")
    _append(tree / "vehicle/tires/demo_front.tir", " ")
    code, out = _stale(tree, path, capsys)
    assert code == 1
    assert out.startswith("STALE demo")
    assert "tire:demo_front" in out and "bobsim:engines" in out


@pytest.mark.parametrize("study", ["", "renamed"])
def test_stale_check_needs_the_study_folder(tree, tmp_path, capsys, study):
    path = _record(tree, tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, "study": study}), encoding="utf-8")
    code, out = _stale(tree, path, capsys)
    assert code == invalidation.EXIT_UNKNOWN
    assert "no study folder" in out


def test_version_1_provenance_is_never_current(tree, tmp_path, capsys):
    vehicle = tree / "vehicle/vehicle.yml"
    path = tmp_path / "provenance.json"
    old = {"study": "demo", "bobsim_sha": "abc", "vehicle_sha256": invalidation.sha256_file(vehicle)}
    path.write_text(json.dumps(old), encoding="utf-8")
    code, out = _stale(tree, path, capsys)
    assert code == invalidation.EXIT_UNKNOWN
    assert "no invalidation keys" in out
    _append(vehicle, " ")
    code, out = _stale(tree, path, capsys)
    assert code == 1
    assert "vehicle" in out


def test_stale_check_uses_bobsim_vehicle_when_no_vehicle_is_given(tree, tmp_path, capsys, monkeypatch):
    path = _record(tree, tmp_path)
    copy = tmp_path / "other.yml"
    copy.write_bytes((tree / "vehicle/vehicle.yml").read_bytes() + b" ")
    monkeypatch.setenv("BOBSIM_VEHICLE", str(copy))
    code = invalidation.main(["stale", str(path)], layout=Layout(tree))
    out = capsys.readouterr().out
    assert code == 1 and "vehicle" in out


def test_python_digest_ignores_comments_and_layout_only():
    base = b"def f(x):\n    return x + 1\n"
    assert invalidation.python_digest(base) == invalidation.python_digest(
        b"# header\ndef f(x):  # why\n\n        return x + 1\n"
    )
    assert invalidation.python_digest(base) == invalidation.python_digest(base.replace(b"\n", b"\r\n"))
    assert invalidation.python_digest(base) != invalidation.python_digest(b"def f(x):\n    return x + 2\n")
    assert invalidation.python_digest(base) != invalidation.python_digest(b"def f(x):\n    return 'x + 1'\n")


def test_physics_entries_exist_in_the_pinned_bobsim():
    for entry in invalidation.BOBSIM_PHYSICS:
        assert (REAL.bobsim / entry).exists(), f"BobSim has no {entry}. Update BOBSIM_PHYSICS for the new layout."
    excluded = ("app", "docs", "visualization", "tests")
    assert not [entry for entry in invalidation.BOBSIM_PHYSICS if entry.split("/")[0] in excluded]


def test_front_ackermann_keys_cover_its_imports_outside_the_physics_folders():
    files = invalidation.import_closure([REAL.sim / "studies/front-ackermann/run.py"], REAL)
    names = {path.relative_to(REAL.repo).as_posix() for path in files}
    assert "simulation/tools/parallel.py" in names
    assert "simulation/bobsim/app/tire_eval.py" in names


def test_tire_files_match_what_bobsim_reads():
    from common.vehicle_io import load_yaml, tire_template_name, tire_templates_root

    data = load_yaml(REAL.repo / "vehicle/vehicle.yml")
    names = {tire_template_name(data, data[side]) for side in ("front", "rear")}
    if data.get("aero", {}).get("tire_template"):
        names.add(data["aero"]["tire_template"])
    expected = {name: (tire_templates_root(data) / f"{name}.tir").resolve() for name in names}
    found = {name: path.resolve() for name, path in invalidation.tire_files(data, REAL).items()}
    assert found == expected
