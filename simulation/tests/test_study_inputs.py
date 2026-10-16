import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from tools.study_inputs import StudyInputsError, load_study_inputs, markdown_table, resolve, write_resolved

SIM = Path(__file__).resolve().parents[1]
STUDY = SIM / "studies" / "front-ackermann"
SHK_SHA256 = "95063c3b8a0ccaadfd9971088b65b395eb299acdcfb4ea31b8fe5b68ee254562"
# The values run.py held as literals before they moved to inputs.yml.
OLD_POINTS_MM = {
    "lower_fore_i_m": (110.431, 198.700, 76.000), "lower_aft_i_m": (-100.273, 198.700, 76.000),
    "lower_o_m": (2.000, 599.739, 104.495), "upper_fore_i_m": (165.945, 239.958, 217.000),
    "upper_aft_i_m": (-104.470, 241.260, 196.410), "upper_o_m": (-6.000, 570.866, 295.000),
    "tie_o_m": (80.000, 605.350, 199.930), "wheel_center_m": (0.000, 635.000, 199.930),
}
OLD_RACK_PICKUP_MM = (25.277, 240.652, 140.010)
REMOVED_NAMES = {
    "FRONT_SHK_MM", "RACK_PICKUP_SHK_MM", "FRONT_M", "RACK_PICKUP_M", "TEAM_2027", "LB_TO_KG", "RACK_TRAVEL_MM",
    "MU_SCALE", "MU_SENSITIVITY", "BRAKE_BIAS_NOMINAL", "PLANNED_DIFF", "STATIC_CAMBER_DEG", "TEAM_CAMBER_DEG",
    "MIN_COVERAGE", "AY_FLOOR_G", "ROBUST_TOL_PCT", "BALANCE_RADIUS_M",
}
OLD_LITERALS = {31.75, 0.623, 0.45359237, 0.0254, 430.0, 150.0, 0.65, *OLD_RACK_PICKUP_MM,
                *(abs(c) for p in OLD_POINTS_MM.values() for c in p if abs(c) > 50.0)}
STUDY_DESIGN_CONSTANTS = {
    "G", "RADII_M", "ACKERMANN_PCT", "SLIP_SIDES", "LLTD_OFFSETS", "NOMINAL", "GRIP_PROBE", "RACK_MM", "REPORT_RACK_MM",
    "BUMP_MM", "BRAKE_G", "BRAKE_RADII_M", "FX_MAX_FZ_N", "TOE_OUT_DEG", "TOE_RADII_M", "TOE_ACKERMANN_PCT", "MASS_CASES",
    "ANTI_LINKAGE_PCT", "STEER_FIX_TARGETS_PCT", "STEER_RESERVE", "CORNER_RADIUS_MAX_M", "MAP_RADII_M",
    "MAP_AX_G", "MAP_PCTS", "BEST_MIN_GAIN_PCT", "DERIV_RADII_M", "DERIV_FRACTION", "QSS_SPEEDS_MPS", "QSS_AY_STEP_G",
    "CIRCLE_CHECK_RADIUS_M", "CIRCLE_CHECK_TOL", "VARIANT_RADII_M", "VARIANT_AX_G", "VARIANT_TOL", "RESIDUAL_TOL",
    "STATIONARY_TOL", "LIFT_FZ_N", "HAIRPIN_OUTSIDE_DIAMETER_M", "LOCK_MARGIN_MIN_DEG", "COVERAGE_STEP",
    "COVERAGE_DEMAND", "SKIDPAD_RADIUS_M", "LAP_RADIUS_BANDS_M", "QUICK",
}


@pytest.fixture
def inputs_copy(tmp_path):
    def make(edit=None):
        data = yaml.safe_load((STUDY / "inputs.yml").read_text(encoding="utf-8"))
        if edit:
            edit(data)
        path = tmp_path / "inputs.yml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    return make


def test_committed_inputs_reproduce_the_old_constants(study):
    inputs, loaded = study.parse_inputs([])
    assert inputs.mass_properties == {"mass": (430.0 + 150.0) * 0.45359237, "cg_height": 11.0 * 0.0254, "front_static_frac": 0.45}
    assert (inputs.rack_travel_mm, inputs.mu, inputs.mu_sensitivity, inputs.brake_bias) == (31.75, 0.623, 0.75, 0.65)
    assert (inputs.static_camber_deg, inputs.camber_variant_deg, inputs.planned_diff) == (0.0, -1.0, (5.0, 0.60))
    assert inputs.balance_radius_m == 15.0
    assert (inputs.policy.min_coverage, inputs.policy.ay_floor_g, inputs.policy.tolerances_pct) == (1.0, 0.2, (0.5, 1.0))
    geometry = loaded.geometry
    assert geometry.points_m == {k: [c / 1000.0 for c in v] for k, v in OLD_POINTS_MM.items()}
    assert geometry.rack_pickup_m == [c / 1000.0 for c in OLD_RACK_PICKUP_MM]
    assert geometry.label == study.DESIGN


def test_committed_geometry_is_an_honest_unreleased_transcription(study):
    _, loaded = study.parse_inputs([])
    geometry = loaded.geometry
    assert geometry.kind == "transcription" and geometry.release_status != "released"
    assert geometry.source_file["sha256"] == SHK_SHA256
    assert geometry.source_file["stored"] == "external" and geometry.source_file["verified"] is False
    assert loaded.bundle_id.status == "unavailable" and loaded.bundle_id.value is None
    statuses = {f.status for f in loaded.fields.values()}
    assert "measured" not in statuses
    assert {geometry.points_field.status, geometry.rack_field.status} <= {"declared", "assumed", "guessed"}
    assert all(geometry.frame[k].status != "measured" for k in geometry.frame)


def test_run_py_holds_no_vehicle_or_geometry_literal():
    tree = ast.parse((STUDY / "run.py").read_text(encoding="utf-8"))
    names = {t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
    assert not names & REMOVED_NAMES, f"run.py defines {sorted(names & REMOVED_NAMES)}. Put the value in inputs.yml."
    numeric = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                                                and not isinstance(n.value, bool) for n in ast.walk(node.value)):
            numeric |= {t.id for t in node.targets if isinstance(t, ast.Name) and t.id.isupper()}
    unknown = numeric - STUDY_DESIGN_CONSTANTS
    assert not unknown, f"run.py has new numeric constants {sorted(unknown)}. Vehicle and geometry numbers go in inputs.yml."
    found = sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)} & OLD_LITERALS)
    assert not found, f"run.py still holds {found}."


def test_a_missing_field_fails_and_names_it(study, inputs_copy):
    path = inputs_copy(lambda d: d["policy"].pop("ay_floor"))
    with pytest.raises(StudyInputsError, match="policy.ay_floor"):
        study.load_inputs(path)


def test_an_unknown_field_fails(study, inputs_copy):
    path = inputs_copy(lambda d: d["tire"].update(extra={"value": 1.0, "unit": "1", "status": "assumed", "source": "x"}))
    with pytest.raises(StudyInputsError, match="tire.extra"):
        study.load_inputs(path)


def test_unavailable_goes_with_null(study, inputs_copy):
    path = inputs_copy(lambda d: d["brake"]["front_bias_nominal"].update(status="unavailable"))
    with pytest.raises(StudyInputsError, match="unavailable"):
        study.load_inputs(path)
    path = inputs_copy(lambda d: d["brake"]["front_bias_nominal"].update(value=None, status="unavailable"))
    with pytest.raises(StudyInputsError, match="needs a value for brake.front_bias_nominal"):
        study.load_inputs(path)


def test_a_bad_status_fails(study, inputs_copy):
    path = inputs_copy(lambda d: d["tire"]["grip_scale"].update(status="validated"))
    with pytest.raises(StudyInputsError, match="status 'validated'"):
        study.load_inputs(path)


def test_geometry_in_inches_fails(study, inputs_copy):
    path = inputs_copy(lambda d: d["front_geometry"].update(units="in"))
    with pytest.raises(StudyInputsError, match="units"):
        study.load_inputs(path)


@pytest.mark.parametrize("key", ["axes", "mirror", "origin_xy"])
def test_the_frame_must_be_stated(study, inputs_copy, key):
    path = inputs_copy(lambda d: d["front_geometry"][key].update(value=None, status="unavailable"))
    with pytest.raises(StudyInputsError, match=key):
        study.load_inputs(path)


def test_a_missing_point_fails(study, inputs_copy):
    path = inputs_copy(lambda d: d["front_geometry"]["points"]["value"].pop("tie_o"))
    with pytest.raises(StudyInputsError, match="must name exactly"):
        study.load_inputs(path)


def test_released_needs_a_release_record(study, inputs_copy):
    path = inputs_copy(lambda d: d["front_geometry"].update(release_status="released"))
    with pytest.raises(StudyInputsError, match="release record"):
        study.load_inputs(path)


RELEASE = {"approved_by": "A. Person", "decision_ref": "design review 1", "date": "2026-10-15"}


def test_a_guessed_transcription_cannot_be_released(study, inputs_copy):
    def edit(d):
        g = d["front_geometry"]
        g.update(release_status="released", release=dict(RELEASE))
        for key in ("points", "rack_pickup", "axes"):
            g[key]["status"] = "guessed"

    with pytest.raises(StudyInputsError, match="is released, but") as error:
        study.load_inputs(inputs_copy(edit))
    for leaf in ("kind is transcription", "points is guessed", "rack_pickup is guessed", "axes is guessed",
                 "datum_z is unavailable", "hash is not checked"):
        assert leaf in str(error.value)


def test_a_released_export_needs_a_stated_datum_and_a_checked_hash(study, inputs_copy, tmp_path):
    export = tmp_path / "front_v34.csv"
    export.write_text("hardpoints", encoding="utf-8")

    def released(datum=True, local_copy=True):
        def edit(d):
            g = d["front_geometry"]
            g.pop("transcription_of")
            g.update(kind="cad_export", release_status="released", release=dict(RELEASE), export_file={
                "name": export.name, "sha256": hashlib.sha256(export.read_bytes()).hexdigest(), "stored": "git",
                **({"path": str(export)} if local_copy else {})})
            for key in ("points", "rack_pickup", "axes", "origin_xy", "mirror"):
                g[key]["status"] = "cad_export"
            if datum:
                g["datum_z"].update(value="ground plane at static ride height", status="declared")
        return edit

    assert study.load_inputs(inputs_copy(released())).geometry.release_status == "released"
    with pytest.raises(StudyInputsError, match="datum_z is unavailable"):
        study.load_inputs(inputs_copy(released(datum=False)))
    with pytest.raises(StudyInputsError, match="hash is not checked"):
        study.load_inputs(inputs_copy(released(local_copy=False)))


def test_a_geometry_change_changes_the_hash_and_points(study, inputs_copy):
    base = study.load_inputs(inputs_copy())

    def move(d):
        d["front_geometry"]["points"]["value"]["tie_o"][1] += 1.0

    moved = study.load_inputs(inputs_copy(move))
    assert moved.sha256 != base.sha256
    assert moved.geometry.points_m["tie_o_m"][1] == pytest.approx(base.geometry.points_m["tie_o_m"][1] + 0.001)


def test_a_named_external_copy_is_hash_checked(study, inputs_copy, tmp_path):
    copy_path = tmp_path / "copy.shk"
    copy_path.write_text("not the V34 file", encoding="utf-8")
    path = inputs_copy(lambda d: d["front_geometry"]["transcription_of"].update(path=str(copy_path)))
    with pytest.raises(StudyInputsError, match="SHA-256"):
        study.load_inputs(path)
    real = hashlib.sha256(copy_path.read_bytes()).hexdigest()
    path = inputs_copy(lambda d: d["front_geometry"]["transcription_of"].update(path=str(copy_path), sha256=real))
    assert study.load_inputs(path).geometry.source_file["verified"] is True


def test_the_design_label_must_match(study, inputs_copy):
    path = inputs_copy(lambda d: d["front_geometry"].update(label="Front V35"))
    with pytest.raises(StudyInputsError, match="design curve"):
        study.load_inputs(path)


def test_static_toe_must_be_zero(study, inputs_copy):
    path = inputs_copy(lambda d: d["alignment"]["static_toe"].update(value=0.5))
    with pytest.raises(StudyInputsError, match="static toe"):
        study.load_inputs(path)


def test_resolved_file_records_every_value_and_hash(study, inputs_copy, tmp_path):
    path = inputs_copy()
    inputs, loaded = study.parse_inputs(["--inputs", str(path), "--mu", "0.7"])
    assert inputs.mu == 0.7
    files = {"inputs": path}
    resolved = json.loads(write_resolved(tmp_path, resolve(loaded, files, {"argv": ["--mu", "0.7"]})).read_text(encoding="utf-8"))
    assert resolved["schema"] == "lhre.study-inputs.resolved.v1"
    assert resolved["inputs_file"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(resolved["values"]) == set(study.INPUT_SPEC)
    for key, record in resolved["values"].items():
        assert {"value", "status", "source", "unit"} <= set(record), key
    assert resolved["overrides"] == ["tire.grip_scale"]
    assert resolved["values"]["tire.grip_scale"]["source"] == "command line --mu"
    assert resolved["values"]["mass_properties.mass"]["file_unit"] == "lb"
    assert resolved["front_geometry"]["source_file"]["sha256"] == SHK_SHA256
    assert resolved["files"]["inputs"]["sha256"] == resolved["inputs_file"]["sha256"]


def test_a_unit_the_study_cannot_convert_fails(study, inputs_copy):
    path = inputs_copy(lambda d: d["steering"]["rack_travel"].update(unit="furlong"))
    with pytest.raises(StudyInputsError, match="cannot convert"):
        study.load_inputs(path)


def test_the_loader_converts_units_and_keeps_the_file_value(tmp_path):
    data = yaml.safe_load((STUDY / "inputs.yml").read_text(encoding="utf-8"))
    kept = {k: copy.deepcopy(data[k]) for k in ("schema", "study", "bundle_id", "front_geometry")}
    kept["steering"] = {"rack_travel": {**data["steering"]["rack_travel"], "value": 3.175}}
    path = tmp_path / "inputs.yml"
    path.write_text(yaml.safe_dump(kept), encoding="utf-8")
    loaded = load_study_inputs(path, {"steering.rack_travel": "m"})
    assert loaded.value("steering.rack_travel") == 3.175 / 1000.0
    assert loaded.fields["steering.rack_travel"].file_value == 3.175


@pytest.mark.parametrize("field", ["points", "rack_pickup"])
def test_a_transcription_cannot_claim_an_export_status(study, inputs_copy, field):
    path = inputs_copy(lambda d: d["front_geometry"][field].update(status="cad_export"))
    with pytest.raises(StudyInputsError, match="transcription"):
        study.load_inputs(path)


def test_the_inputs_table_shows_every_value_with_its_status(study, inputs_copy):
    _, loaded = study.parse_inputs(["--inputs", str(inputs_copy()), "--bias", "0.7"])
    text = markdown_table(resolve(loaded, {}))
    rows = [line for line in text.splitlines()[2:] if line.startswith("| ")]
    for key, field in loaded.fields.items():
        row = next(r for r in rows if r.startswith(f"| {key} |"))
        assert f"| {field.status} |" in row
    assert "580 lb" in text and "11 in" in text and "31.75 mm" in text
    assert "0.7 (override)" in next(r for r in rows if r.startswith("| brake.front_bias_nominal |"))
    assert "transcription, release status candidate" in text and "hash not checked by the run" in text
    assert "| Vehicle bundle | unavailable | unavailable |" in text


def test_the_design_front_matches_the_old_literals(study):
    from common.vehicle_io import load_yaml
    from simulations.envelope.vehicle_yaml import project_vehicle_yaml

    _, loaded = study.parse_inputs([])
    vehicle = load_yaml(SIM.parent / "vehicle" / "vehicle.yml")
    front = study.with_front(vehicle, loaded.geometry)
    for key, point in OLD_POINTS_MM.items():
        assert front["front"]["suspension"][key] == pytest.approx([c / 1000.0 for c in point], abs=1e-12)
    assert front["front"]["steering"]["rack_pickup_m"] == pytest.approx([c / 1000.0 for c in OLD_RACK_PICKUP_MM], abs=1e-12)
    assert project_vehicle_yaml(front).ggv.track_front == pytest.approx(1.270, abs=1e-12)
    assert vehicle["front"]["suspension"] != front["front"]["suspension"]


@pytest.mark.parametrize("axle, name", [("front", "camber_deg"), ("rear", "camber_deg"), ("front", "toe_deg"), ("rear", "toe_deg")])
def test_alignment_must_match_the_vehicle_file(study, axle, name):
    from common.vehicle_io import load_yaml

    _, loaded = study.parse_inputs([])
    vehicle = load_yaml(SIM.parent / "vehicle" / "vehicle.yml")
    study.check_alignment(loaded, vehicle)
    vehicle[axle]["wheel"][name] = 0.5
    with pytest.raises(StudyInputsError, match=f"{axle}.wheel.{name}"):
        study.check_alignment(loaded, vehicle)
    del vehicle[axle]["wheel"][name]
    with pytest.raises(StudyInputsError, match=f"{axle}.wheel.{name}"):
        study.check_alignment(loaded, vehicle)
