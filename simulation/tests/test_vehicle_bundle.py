import copy
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tools import bundle as vb

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
TIRE = "16x7p5_10_12psi"
MEMBERS = ("vehicle/vehicle.yml", "vehicle/bundle.yml", f"vehicle/tires/{TIRE}.tir")
CANDIDATE_LINE = "candidate: software checks passed. Not hardware approval."
HEX64 = "0" * 64


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "repo"
    (root / "vehicle" / "tires").mkdir(parents=True)
    for rel in MEMBERS:
        shutil.copyfile(REPO / rel, root / rel)
    return root


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    return vb.load_bundle(path)


def _edit(root, change):
    path = root / "vehicle" / "bundle.yml"
    data = _read(path)
    change(data)
    return _write(path, data)


def _codes(problems):
    return {(problem.path, problem.code) for problem in problems}


def _check(root, bundle=None, vehicle=None):
    bundle = bundle or vb.load_bundle(root / "vehicle" / "bundle.yml")
    return vb.check_bundle(bundle, vehicle, root=root)


def _leaf(value, status="declared", source="test source"):
    return {"value": value, "status": status, "source": source}


def _is_leaf(node):
    return isinstance(node, dict) and set(node) == {"value", "status", "source"}


FILLERS = {"reaction": "chassis", "date": "2026-10-14", "license": "restricted", "front": 0.0, "rear": 0.0}


def _declare_everything(data):
    for key, node in (data.items() if isinstance(data, dict) else enumerate(data)):
        if _is_leaf(node):
            if node["status"] not in vb.RELEASE_GRADE:
                fill = node["value"] if node["value"] is not None else FILLERS.get(key, f"test {key}")
                data[key] = _leaf(fill)
        elif isinstance(node, (dict, list)):
            _declare_everything(node)


def _release(data):
    _declare_everything(data)
    data["status"] = "released"


def _sha(path):
    return vb.file_sha256(path)


def test_committed_bundle_passes():
    bundle = vb.load_bundle(REPO / "vehicle" / "bundle.yml")
    assert vb.check_bundle(bundle) == []


def test_committed_bundle_describes_the_committed_vehicle():
    data = _read(REPO / "vehicle" / "bundle.yml")
    assert data["members"]["vehicle"]["path"] == "vehicle/vehicle.yml"
    assert data["members"]["vehicle"]["sha256"] == _sha(REPO / "vehicle" / "vehicle.yml")


def test_cli_prints_the_candidate_line_and_the_gaps(root, monkeypatch, capsys):
    monkeypatch.setattr(vb, "REPO", root)
    _edit(root, lambda data: data.update(status="candidate"))
    assert vb.main(["check"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().splitlines()[-1] == CANDIDATE_LINE
    assert "axles.front.revision: unavailable" in out
    assert "release.approved_by: unavailable" in out


def test_vehicle_hash_must_match_the_bytes(root):
    vehicle = root / "vehicle" / "vehicle.yml"
    vehicle.write_bytes(vehicle.read_bytes() + b"# one more byte\n")
    problems = _check(root)
    assert ("members.vehicle.sha256", "sha256_mismatch") in _codes(problems)
    assert _sha(vehicle) in " ".join(problem.message for problem in problems)


def test_a_crlf_copy_fails_with_a_hint(root):
    vehicle = root / "vehicle" / "vehicle.yml"
    vehicle.write_bytes(vehicle.read_bytes().replace(b"\n", b"\r\n"))
    problems = [problem for problem in _check(root) if problem.code == "sha256_mismatch"]
    assert [problem.path for problem in problems] == ["members.vehicle.sha256"]
    assert "CRLF" in problems[0].message


def test_tire_hash_must_match_the_bytes(root):
    tire = root / "vehicle" / "tires" / f"{TIRE}.tir"
    tire.write_bytes(tire.read_bytes() + b"!\n")
    assert ("members.tires[0].sha256", "sha256_mismatch") in _codes(_check(root))


def test_missing_member_file_fails(root):
    (root / "vehicle" / "tires" / f"{TIRE}.tir").unlink()
    assert ("members.tires[0].sha256", "missing_file") in _codes(_check(root))


def test_the_tire_that_the_vehicle_loads_must_be_the_bundle_tire(root):
    tire = root / "vehicle" / "tires" / f"{TIRE}.tir"
    tire.write_bytes(tire.read_bytes() + b"!\n")
    bundle = _edit(root, lambda data: data["members"]["tires"][0].update(sha256=_sha(tire)))
    codes = _codes(_check(root, bundle))
    assert ("members.tires[0].sha256", "sha256_mismatch") not in codes
    assert ("members.tires[0].sha256", "tire_mismatch") in codes


def test_every_axle_tire_must_be_a_member(root):
    bundle = _edit(root, lambda data: data["members"]["tires"][0].update(axles=["front"]))
    assert ("members.tires", "tire_mismatch") in _codes(_check(root, bundle))


def test_released_needs_the_approval_record(root):
    bundle = _edit(root, lambda data: data.update(status="released"))
    codes = _codes(_check(root, bundle))
    for path in ("release.approved_by", "release.decision_ref", "release.date"):
        assert (path, "release_blocked") in codes
    assert ("axles.front.revision", "release_blocked") in codes
    assert ("frame.datum_z.rear", "release_blocked") in codes
    assert ("members.tires[0].license", "release_blocked") in codes
    assert ("torque_topology.regen.reaction", "release_blocked") in codes


def test_released_passes_when_every_leaf_is_release_grade(root):
    bundle = _edit(root, _release)
    assert _check(root, bundle) == []


@pytest.mark.parametrize(
    "weak",
    [
        _leaf("V20", "guessed", "x signs copied from V19"),
        _leaf("V20", "assumed", "probably the current front"),
        _leaf(None, "unavailable", "no record"),
    ],
    ids=["guessed", "assumed", "unavailable"],
)
def test_a_weak_leaf_blocks_released_but_not_candidate(root, weak):
    def change(data):
        _release(data)
        data["axles"]["front"]["revision"] = weak

    bundle = _edit(root, change)
    assert _codes(_check(root, bundle)) == {("axles.front.revision", "release_blocked")}
    bundle = _edit(root, lambda data: data.update(status="candidate"))
    assert _check(root, bundle) == []


def _set(path, value):
    def change(data):
        *parents, last = path
        node = data
        for key in parents:
            node = node[key]
        if value is DELETE:
            del node[last]
        else:
            node[last] = copy.deepcopy(value)

    return change


DELETE = object()
SOURCE = ("members", "sources", 0)
BAD = [
    (("axles", "rear", "cad"), None, "axles.rear.cad", "not_a_triple"),
    (("axles", "rear", "cad"), "CAD-123", "axles.rear.cad", "not_a_triple"),
    (("axles", "rear", "svn"), DELETE, "axles.rear.svn", "missing_key"),
    (("axles", "rear", "rack"), _leaf("x"), "axles.rear.rack", "unknown_key"),
    (("axles", "rear", "revision"), _leaf("R3", "unavailable"), "axles.rear.revision", "unavailable_has_value"),
    (("axles", "rear", "revision"), _leaf(None, "declared"), "axles.rear.revision", "missing_value"),
    (("axles", "rear", "revision"), _leaf("R3", "probably"), "axles.rear.revision", "bad_status"),
    (("axles", "rear", "revision"), _leaf("R3", "declared", ""), "axles.rear.revision", "missing_source"),
    (("axles", "front", "source"), _leaf("no-such-source"), "axles.front.source", "unknown_source"),
    (("frame", "axes"), _leaf({"x": "rearward", "y": "left", "z": "up"}), "frame.axes", "bad_value"),
    (("frame", "units"), _leaf({"length": "mm", "mass": "kg", "alignment_angle": "deg"}), "frame.units", "bad_value"),
    (("frame", "mirror", "stored_side"), _leaf("right"), "frame.mirror.stored_side", "bad_value"),
    (("torque_topology", "drive", "reaction"), _leaf("unknown"), "torque_topology.drive.reaction", "bad_value"),
    (("torque_topology", "drive", "front_share"), _leaf(0.3), "torque_topology.drive", "share_sum"),
    (("torque_topology", "regen", "rear_share"), _leaf(1.5), "torque_topology.regen.rear_share", "bad_value"),
    (("members", "tires", 0, "license"), _leaf("maybe"), "members.tires[0].license", "bad_value"),
    (("release", "date"), _leaf("yesterday"), "release.date", "bad_value"),
    (("members", "vehicle", "schema"), "boblib.vehicle.v2", "members.vehicle.schema", "schema_mismatch"),
    (("members", "vehicle", "path"), "../vehicle.yml", "members.vehicle.path", "bad_value"),
    (("bundle_id",), "Orion Baseline", "bundle_id", "bad_value"),
    (("status",), "approved", "status", "bad_value"),
    (("schema",), "lhre.vehicle-bundle.v0", "schema", "bad_value"),
    (("superseded_by",), "orion-baseline-2", "superseded_by", "bad_value"),
    ((*SOURCE, "kind"), "screenshot", "members.sources[0].kind", "bad_value"),
]


@pytest.mark.parametrize(
    ("path", "value", "where", "code"), BAD, ids=[f"{where}-{code}" for _, _, where, code in BAD]
)
def test_bad_bundle_fails(root, path, value, where, code):
    bundle = _edit(root, _set(path, value))
    assert (where, code) in _codes(_check(root, bundle))


def test_superseded_names_its_replacement(root):
    bundle = _edit(root, lambda data: data.update(status="superseded", superseded_by=None))
    assert ("superseded_by", "missing_value") in _codes(_check(root, bundle))
    bundle = _edit(root, lambda data: data.update(superseded_by="orion-baseline-2"))
    assert _check(root, bundle) == []


def test_cli_says_a_superseded_bundle_is_stale_and_not_approved(root, monkeypatch, capsys):
    bundle = _edit(root, lambda data: data.update(status="superseded", superseded_by="orion-baseline-2"))
    monkeypatch.setattr(vb, "REPO", root)
    assert vb.main(["check"]) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == (
        "superseded by orion-baseline-2: software checks passed. Not hardware approval. "
        f"Results that cite {bundle.bundle_id} are stale."
    )


def _hand_entered(data, status):
    data["members"]["sources"].append(
        {
            "id": "front-v20-transcription",
            "kind": "hand-entered",
            "file": {"name": "front-v20 screenshot", "sha256": HEX64, "stored": "external"},
            "units": _leaf({"length": "mm"}),
        }
    )
    data["axles"]["front"]["source"] = _leaf("front-v20-transcription", status)


def test_a_hand_entered_axle_source_is_guessed(root):
    bundle = _edit(root, lambda data: _hand_entered(data, "declared"))
    assert ("axles.front.source", "hand_entered") in _codes(_check(root, bundle))
    bundle = _edit(root, lambda data: data["axles"]["front"]["source"].update(status="guessed"))
    assert _check(root, bundle) == []


def test_a_hand_entered_axle_source_cannot_be_released(root):
    def change(data):
        _release(data)
        _hand_entered(data, "guessed")

    bundle = _edit(root, change)
    assert _codes(_check(root, bundle)) == {("axles.front.source", "release_blocked")}


def test_load_bundle_needs_a_mapping(tmp_path):
    path = tmp_path / "bundle.yml"
    path.write_text("- a list\n", encoding="utf-8")
    with pytest.raises(vb.BundleError):
        vb.load_bundle(path)


def test_same_bundle_id_with_a_new_member_hash_fails(root):
    base = vb.load_bundle(REPO / "vehicle" / "bundle.yml")
    vehicle = root / "vehicle" / "vehicle.yml"
    vehicle.write_bytes(vehicle.read_bytes() + b"# changed\n")
    head = _edit(root, lambda data: data["members"]["vehicle"].update(sha256=_sha(vehicle)))
    assert _check(root, head) == []
    assert _codes(vb.check_immutable(base, head)) == {("bundle_id", "immutable_members")}
    head = _edit(root, lambda data: data.update(bundle_id="orion-baseline-test-2"))
    assert vb.check_immutable(base, head) == []


def test_a_released_bundle_only_changes_to_superseded(root):
    base = _edit(root, _release)
    path = base.path
    changed = copy.deepcopy(base.data)
    changed["axles"]["front"]["revision"]["source"] = "a different note"
    assert _codes(vb.check_immutable(base, vb.Bundle(path, changed))) == {("bundle_id", "immutable_released")}
    superseded = copy.deepcopy(base.data)
    superseded.update(status="superseded", superseded_by="orion-baseline-test-2")
    assert vb.check_immutable(base, vb.Bundle(path, superseded)) == []


def _git(root, *args):
    command = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    result = subprocess.run([*command, "-C", str(root), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_immutability_is_checked_against_a_git_ref(root, monkeypatch, capsys):
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    vehicle = root / "vehicle" / "vehicle.yml"
    vehicle.write_bytes(vehicle.read_bytes() + b"# changed\n")
    _edit(root, lambda data: data["members"]["vehicle"].update(sha256=_sha(vehicle)))
    monkeypatch.setattr(vb, "REPO", root)

    assert vb.main(["check", "--base", "HEAD"]) == 1
    assert "immutable_members" in capsys.readouterr().out

    _edit(root, lambda data: data.update(bundle_id="orion-baseline-test-2"))
    assert vb.main(["check", "--base", "HEAD"]) == 0
    assert vb.main(["check", "--base", "no-such-ref"]) == 2
    assert "no-such-ref" in capsys.readouterr().out


def test_a_base_without_a_bundle_has_nothing_to_compare(root):
    _git(root, "init", "-q")
    _git(root, "add", "vehicle/vehicle.yml")
    _git(root, "commit", "-q", "-m", "base")
    assert vb.load_base_bundle("HEAD", repo=root) is None


def _candidate(root, bundle_id="orion-candidate-test", status="candidate"):
    out = root / "simulation" / "out" / "candidate"
    out.mkdir(parents=True)
    vehicle = out / "vehicle.yml"
    vehicle.write_bytes(b"# candidate copy\n" + (root / "vehicle" / "vehicle.yml").read_bytes())
    data = _read(root / "vehicle" / "bundle.yml")
    if status == "released":
        _release(data)
    data.update(bundle_id=bundle_id, status=status)
    data["members"]["vehicle"].update(path="simulation/out/candidate/vehicle.yml", sha256=_sha(vehicle))
    return _write(out / "bundle.yml", data), vehicle


def test_a_candidate_copy_is_checked_by_path(root, monkeypatch, capsys):
    bundle, vehicle = _candidate(root)
    assert _check(root, bundle, vehicle) == []
    committed = vb.load_bundle(root / "vehicle" / "bundle.yml")
    assert ("members.vehicle.sha256", "sha256_mismatch") in _codes(_check(root, committed, vehicle))

    monkeypatch.setattr(vb, "REPO", root)
    assert vb.main(["check", "--bundle", str(bundle.path), "--vehicle", str(vehicle)]) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == CANDIDATE_LINE


def test_a_candidate_path_is_relative_to_the_working_directory(root, monkeypatch, capsys):
    _candidate(root)
    monkeypatch.setattr(vb, "REPO", root)
    monkeypatch.chdir(root / "simulation")
    paths = ["--bundle", "out/candidate/bundle.yml", "--vehicle", "out/candidate/vehicle.yml"]
    assert vb.main(["check", *paths]) == 0, capsys.readouterr().out
    assert "BobSim load: OK" in capsys.readouterr().out


def test_a_candidate_copy_cannot_be_released(root):
    bundle, vehicle = _candidate(root, status="released")
    assert _codes(_check(root, bundle, vehicle)) == {("status", "release_not_committed_vehicle")}


def test_a_candidate_copy_needs_its_own_bundle_id(root, monkeypatch, capsys):
    committed = _read(root / "vehicle" / "bundle.yml")["bundle_id"]
    bundle, vehicle = _candidate(root, bundle_id=committed)
    monkeypatch.setattr(vb, "REPO", root)
    assert vb.main(["check", "--bundle", str(bundle.path), "--vehicle", str(vehicle)]) == 1
    assert "immutable_members" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("license", "restricted"),
    [
        (_leaf(None, "unavailable", "no license record"), True),
        (_leaf("public", "assumed"), True),
        (_leaf("team-private"), True),
        (_leaf("restricted"), True),
        (_leaf("public"), False),
    ],
    ids=["unavailable", "assumed-public", "team-private", "restricted", "declared-public"],
)
def test_a_tire_is_restricted_unless_declared_public(root, license, restricted):
    bundle = _edit(root, lambda data: data["members"]["tires"][0].update(license=license))
    assert (f"vehicle/tires/{TIRE}.tir" in bundle.restricted_files()) is restricted


def test_the_committed_tire_is_restricted(capsys):
    assert vb.main(["restricted"]) == 0
    assert f"vehicle/tires/{TIRE}.tir" in capsys.readouterr().out.splitlines()


def test_dependency_keys_follow_values_not_notes(root):
    base = vb.load_bundle(root / "vehicle" / "bundle.yml")
    keys = base.dependency_keys()
    assert {"vehicle", f"tire:{TIRE}", "frame", "axle:front", "axle:rear", "setup", "torque_topology"} <= set(keys)
    assert keys["vehicle"] == _sha(root / "vehicle" / "vehicle.yml")

    def note(data):
        data["axles"]["front"]["revision"]["source"] = "a better note"
        data["status"] = "draft"

    assert _edit(root, note).dependency_keys() == keys
    moved = _edit(root, lambda data: data["axles"]["front"].update(revision=_leaf("V34")))
    changed = {name for name, digest in moved.dependency_keys().items() if keys.get(name) != digest}
    assert changed == {"axle:front"}


def test_the_bundle_tool_writes_no_files(root, monkeypatch):
    source = (SIM / "tools" / "bundle.py").read_text(encoding="utf-8")
    assert not re.search(r"write_text|write_bytes|\.write\(|\.dump\(|open\(", source)
    before = {rel: (root / rel).read_bytes() for rel in MEMBERS}
    monkeypatch.setattr(vb, "REPO", root)
    _edit(root, lambda data: data.update(status="released"))
    before["vehicle/bundle.yml"] = (root / "vehicle" / "bundle.yml").read_bytes()
    assert vb.main(["check"]) == 1
    assert {rel: (root / rel).read_bytes() for rel in MEMBERS} == before


def test_vehicle_has_one_vehicle_file():
    found = sorted(
        path.relative_to(REPO).as_posix()
        for path in (REPO / "vehicle").rglob("*.y*ml")
        if (_read(path) or {}).get("schema") == "boblib.vehicle.v1"
    )
    assert found == ["vehicle/vehicle.yml"]
    assert not (REPO / "vehicle" / "candidates").exists()


def test_the_check_says_which_vehicle_checks_ran(capsys):
    vb.main(["check"])
    out = capsys.readouterr().out
    assert "Vehicle checks (units, hardpoints, stored side, singular geometry): passed" in out
    assert "Vertical datum: front unavailable, rear unavailable" in out
    assert "BobSim load: OK" in out


def test_vehicle_checks_do_not_run_on_a_changed_vehicle(root, monkeypatch, capsys):
    vehicle = root / "vehicle" / "vehicle.yml"
    vehicle.write_bytes(vehicle.read_bytes() + b"# changed\n")
    monkeypatch.setattr(vb, "REPO", root)
    assert vb.main(["check"]) == 1
    assert "Vehicle checks (units, hardpoints, stored side, singular geometry): not run." in capsys.readouterr().out


def test_make_check_vehicle_passes_its_options():
    def dry_run(*args):
        result = subprocess.run(
            ["make", "-n", "check-vehicle", "COMPOSE=compose", *args], cwd=SIM, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    plain = dry_run()
    assert "tools/bundle.py check" in plain
    assert "--base" not in plain and "--vehicle" not in plain and "--bundle" not in plain
    full = dry_run("VEHICLE=out/c/vehicle.yml", "BUNDLE=out/c/bundle.yml", "BASE=origin/main")
    for option in ("--vehicle out/c/vehicle.yml", "--bundle out/c/bundle.yml", "--base origin/main"):
        assert option in full
