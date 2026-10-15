import json
import re
from pathlib import Path

import pytest

from tools.readme import BLOCK, fill

README = Path(__file__).resolve().parents[1] / "studies" / "front-ackermann" / "README.md"
# make study writes provenance.json and main() writes inputs.md. write_readme_parts writes the rest.
WRITTEN_ELSEWHERE = {"provenance.json", "inputs.md"}


def row(study, mu, radius, ax, curve, ay, kind="physical", status="accepted"):
    grip = ay if kind == "physical" and status == "accepted" else None
    out = {"mu": mu, "rear": "open", "radius_m": radius, "ax_g": ax, "curve": curve, "ay_max_g": grip,
           "ay_accepted_g": ay if status == "accepted" else None, "bound_kind": kind, "trim_status": status,
           "upper_reason": "" if kind == "physical" else "alpha_out_of_fit:FR", "ay_change_pct": None}
    if status == "accepted":
        out.update({"inner_deg": 20.0, "outer_deg": 18.0, "lock_margin_deg": 5.0})
    return out


@pytest.fixture
def rendered(study, tmp_path):
    inputs, _ = study.parse_inputs(["--quick"])
    curves = [study.BASELINE, *(f"{p:+d}%" for p in study.MAP_PCTS), study.DESIGN]
    op_rows = []
    for radius in inputs.map_radii_m:
        for ax in inputs.map_ax_g:
            for i, curve in enumerate(curves):
                if (radius, ax, curve) == (3.5, 0.0, "-50%"):
                    op_rows.append(row(study, inputs.mu, radius, ax, curve, 0.9, kind="domain"))
                else:
                    op_rows.append(row(study, inputs.mu, radius, ax, curve, 1.0 + 0.001 * i))
    for r in op_rows:
        base = next(b for b in op_rows if (b["radius_m"], b["ax_g"], b["curve"]) == (r["radius_m"], r["ax_g"], study.BASELINE))
        r["ay_change_pct"] = study.gain_pct(r["ay_max_g"], base["ay_max_g"], inputs.policy)
    robust = study.robust_region(op_rows, inputs.policy)
    nominal = {f"{r:g}m": {c: {"ay_change_pct": None, "limiting_axle": None, "bound_kind": "domain"} for c in curves}
               for r in study.RADII_M}
    spread = {f"{r:g}m": {c: [None, None, 0, 12] for c in curves} for r in study.RADII_M}
    toe = study.toe_summary([{"toe_out_deg": t, "radius_m": r, "curve": c, "ay_max_g": None if c == "+10%" else 1.0}
                             for t in inputs.toe_out_deg for r in study.TOE_RADII_M for c in (study.BASELINE, "+10%", "+20%")],
                            inputs.policy)
    lap = {"delta_s": 0.01, "skidpad_delta_s": 0.001}
    s = {
        "nominal": nominal, "ay_change_pct_range_all_cases": spread, "steering_fix": [],
        "steering_fix_skipped": {"-40%": "domain: alpha_out_of_fit:FR"},
        "front_base_linkage": {"arm_offset_mm": 84.0, "tie_o_inboard_of_wheel_center_plane_mm": 29.7, "inner_wheel_at_travel": {
            "toggle_margin_deg": 45.0, "bump_toe_change_deg": {"-25mm": 0.2, "+25mm": -0.4}}},
        "first_principles_optimum": {"3.5m": {"status": "unavailable", "reason": "x"}, "4.5m": {"status": "unavailable", "reason": "x"}},
        "toe": toe,
        "team_2027_inputs": {"straight_braking_limit": {f"{inputs.brake_bias:.2f}": {
            "locks_first": "rear", "limit_g": 1.46, "ideal_front_bias": 0.68, "ideal_limit_g": 1.5}}},
        "operating_map": {"curves": curves, "variants": [], "caps": [], "lap_excluded": [{"curve": "-50%"}],
                          "lock_limited_cells": [], "lock_margin_min_deg": {c: None for c in curves}},
        "robust": {"all": robust, "beyond_3p5": study.robust_region([r for r in op_rows if r["radius_m"] > 3.5], inputs.policy)},
        "lap_qss": {"0.623": {c: lap for c in curves if c != "-50%"}, "radius_bands": []},
        "lock_at_rack_travel": {"mean_steer_at_travel_deg": 23.4, "mean_steer_needed_at_limit_deg": {},
                                "tightest_cg_radius_at_limit_m": None, "min_cg_radius_low_speed_m": 3.83,
                                "rule_hairpin_cg_radius_m": 3.67, "rack_needed_for_3p5m_at_limit_mm": None},
        "ackermann_pct_at_rack": {study.DESIGN: {"10mm": 48.9, "20mm": 50.0, f"{inputs.rack_travel_mm:g}mm": 53.7}},
        "grid_convergence": {"band_s": None}, "lsd_rows": [],
        "trim_coverage": {"all": 0.9, "points": 10, "demand": 0.98, "radius_max_m": 15.0},
        "track_minimum_curvature_line": {"length_m": 1989.0, "corners_under_15m": 42, "corners": [{"min_radius_m": 4.47}]},
        "cap_check": {"max_change_ms": 0.0}, "vehicle": {"lltd_front": 0.3443}, "circle_check": {"error_pct": 0.008},
        "clipped_braking": {"cells": 0, "of": 0, "max_g": 0.0, "max_at": {"curve": None}, "by_curve": {}},
        "brake_model_gap_pts": {"value": None, "n_valid": 0, "n_total": 4}, "mass_gap_pts": {"value": 0.4, "n_valid": 4, "n_total": 4},
        "tire": {"sha256": "0" * 64},
    }
    study.write_readme_parts(tmp_path, json.loads(json.dumps(s)), op_rows, inputs)
    return tmp_path, robust


def test_withheld_region_renders_no_law_name(study, rendered):
    out, robust = rendered
    values = json.loads((out / "readme.json").read_text(encoding="utf-8"))
    assert robust["status"] == "withheld"
    assert values["robust_laws"].startswith("No recommendation")
    assert not any(law in values["robust_laws"] for law in robust["laws"])
    text = (out / "robust.md").read_text(encoding="utf-8")
    assert "have common support" in text and "withheld" in text


def test_failed_cells_render_as_withheld_or_not_modeled(study, rendered):
    out, _ = rendered
    assert "withheld" in (out / "map.md").read_text(encoding="utf-8")
    assert "not modeled" in (out / "lap.md").read_text(encoding="utf-8")
    assert "not solved: the 3.5 m limit is not a grip limit" in (out / "linkage.md").read_text(encoding="utf-8")
    assert "withheld" in (out / "toe.md").read_text(encoding="utf-8")
    assert "no grip" not in "".join(p.read_text(encoding="utf-8") for p in out.glob("*.md"))


def test_every_readme_reference_is_written(rendered):
    out, _ = rendered
    values = json.loads((out / "readme.json").read_text(encoding="utf-8"))
    text = README.read_text(encoding="utf-8")
    refs = re.findall(r"<!-- out:([^ ]+) -->", text)
    assert "inputs.md" in refs
    assert [m["ref"] for m in BLOCK.finditer(text)] == refs, "An out: block is nested or not closed."
    for ref in refs:
        name, _, rest = ref.partition("#")
        if name in WRITTEN_ELSEWHERE:
            continue
        if name == "readme.json":
            assert rest.partition("|")[0] in values, f"README reads {ref}. write_readme_parts does not write it."
        else:
            assert (out / name).is_file(), f"README reads {ref}. write_readme_parts does not write it."


def test_a_missing_readme_value_says_withheld_not_null(rendered):
    out, _ = rendered
    values = json.loads((out / "readme.json").read_text(encoding="utf-8"))
    assert [k for k, v in values.items() if v is None] == []
    assert values["brake_model_gap_pts"] == "withheld: no pair of valid rows"
    assert values["robust_floor_pct"] == "withheld: the comparison is withheld"


def test_findings_print_only_with_common_support(study, rendered):
    out, robust = rendered
    text = (out / "findings.md").read_text(encoding="utf-8")
    assert text.startswith("- **No recommendation.**")
    assert not any(law in text for law in robust["laws"])
    ok = {**robust, "status": "ok", "region": {"0.5": ["+50%", "Front V34"]}}
    text = study.findings_md(ok, ok)
    assert "+50%, Front V34 at 0.5 point tolerance" in text and "No recommendation" not in text


def test_a_filled_readme_shows_no_none_and_no_stale_finding(rendered, tmp_path_factory):
    out, _ = rendered
    (out / "provenance.json").write_text("{}", encoding="utf-8")
    (out / "inputs.md").write_text("| Input |\n", encoding="utf-8")
    readme = tmp_path_factory.mktemp("readme") / "README.md"
    readme.write_text(README.read_text(encoding="utf-8"), encoding="utf-8")
    fill(readme, out)
    text = readme.read_text(encoding="utf-8")
    assert "None" not in text
    assert "Pro, not anti" not in text and "No recommendation" in text
