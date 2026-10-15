import pytest

from tools.support import Observation, SupportPolicy, cell_best, compare, gain_pct

POLICY = SupportPolicy(min_coverage=0.5, ay_floor_g=0.2, tolerances_pct=(0.5, 1.0))
STRICT = SupportPolicy(min_coverage=1.0, ay_floor_g=0.2, tolerances_pct=(0.5,))
LAWS = ("+0%", "+50%", "-50%")


def physical(condition, law, ay):
    return Observation(condition, law, ay, "physical")


def table(values, overrides=()):
    rows = [physical(c, law, ay) for c, cell in values.items() for law, ay in cell.items()]
    replace = {(o.condition, o.law): o for o in overrides}
    return [replace.get((r.condition, r.law), r) for r in rows]


GRID = {
    "C1": {"+0%": 1.00, "+50%": 1.05, "-50%": 0.95},
    "C2": {"+0%": 1.00, "+50%": 1.10, "-50%": 0.90},
    "C3": {"+0%": 1.00, "+50%": 0.99, "-50%": 1.02},
}


def test_a_failed_law_removes_the_condition_for_every_law():
    failed = Observation("C2", "-50%", None, "numerical", "not_converged")
    result = compare(table(GRID, [failed]), LAWS, "+0%", POLICY)
    assert (result.n_supported, result.n_total) == (2, 3)
    assert result.status == "ok"
    assert result.laws["-50%"].worst_shortfall_pct == pytest.approx(100.0 * (1 - 0.95 / 1.05), abs=0.01)
    assert result.laws["+50%"].worst_shortfall_pct == pytest.approx(100.0 * (1 - 0.99 / 1.02), abs=0.01)
    assert all(v.worst_shortfall_pct < 100.0 for v in result.laws.values())
    assert [(e.condition, e.law, e.kind) for e in result.excluded] == [("C2", "-50%", "numerical")]


def test_a_domain_rejected_value_is_never_ranked():
    out_of_fit = Observation("C2", "-50%", 1.5, "domain", "alpha_out_of_fit:FR")
    result = compare(table(GRID, [out_of_fit]), LAWS, "+0%", POLICY)
    assert result.n_supported == 2
    assert "C2" not in {law.binding for law in result.laws.values()}


def test_empty_support_withholds_the_recommendation():
    fails = [Observation(c, "-50%", None, "domain", "steer_bound") for c in GRID]
    result = compare(table(GRID, fails), LAWS, "+0%", POLICY)
    assert result.status == "withheld" and result.reason == "no_common_support"
    assert result.recommendation is None
    assert all(not v for v in result.region.values())
    assert all(law.worst_shortfall_pct is None for law in result.laws.values())


def test_low_coverage_withholds_the_recommendation():
    failed = Observation("C2", "-50%", None, "numerical", "max_evaluations")
    result = compare(table(GRID, [failed]), LAWS, "+0%", STRICT)
    assert result.status == "withheld" and result.reason == "low_coverage"
    assert result.recommendation is None
    assert result.excluded_by_kind() == {"numerical": 1}


def test_an_ill_conditioned_baseline_is_excluded():
    grid = {**GRID, "C4": {"+0%": 0.09, "+50%": 0.21, "-50%": 0.05}}
    result = compare(table(grid), LAWS, "+0%", POLICY)
    assert result.n_supported == 3
    assert [e.kind for e in result.excluded] == ["ill_conditioned"]
    assert gain_pct(0.21, 0.09, POLICY) is None
    assert gain_pct(1.1, 1.0, POLICY) == pytest.approx(10.0)


def test_full_support_gives_a_region():
    result = compare(table(GRID), LAWS, "+0%", STRICT)
    assert result.status == "ok"
    assert result.recommendation == {"0.5": ("+50%",)}


def test_a_missing_law_row_is_an_exclusion():
    rows = [r for r in table(GRID) if not (r.condition == "C3" and r.law == "+50%")]
    result = compare(rows, LAWS, "+0%", POLICY)
    assert ("C3", "+50%", "missing") in {(e.condition, e.law, e.kind) for e in result.excluded}


def test_cell_best_withholds_when_any_law_lacks_a_limit():
    cell = {law: physical("", law, ay) for law, ay in GRID["C1"].items()}
    assert cell_best(cell, ("+50%", "-50%"), "+0%", POLICY, 0.1).law == "+50%"
    cell["-50%"] = Observation("", "-50%", None, "domain", "alpha_out_of_fit:FR")
    best = cell_best(cell, ("+50%", "-50%"), "+0%", POLICY, 0.1)
    assert best.law is None and best.status == "withheld" and "domain" in best.reason


def test_cell_best_keeps_a_parallel_tie():
    cell = {law: physical("", law, 1.0) for law in LAWS}
    best = cell_best(cell, ("+50%", "-50%"), "+0%", POLICY, 0.1)
    assert best.law == "+0%" and best.gain_pct == 0.0


def test_policy_rejects_bad_values():
    with pytest.raises(ValueError):
        SupportPolicy(min_coverage=0.0, ay_floor_g=0.2, tolerances_pct=(0.5,))
    with pytest.raises(ValueError):
        SupportPolicy(min_coverage=1.0, ay_floor_g=-1.0, tolerances_pct=(0.5,))
