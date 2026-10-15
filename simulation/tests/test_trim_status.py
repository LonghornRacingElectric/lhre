import pytest

from tools.trim_status import BoundKind, LimitResult, TrimRecord, TrimStatus, bisect_limit, combine

ACCEPTED = TrimRecord(TrimStatus.ACCEPTED, unknowns=(0.0, 0.1), starts_tried=1)
NUMERICAL = TrimRecord(TrimStatus.NUMERICAL_FAILURE, ("max_evaluations",), residual_norm=0.3, starts_tried=1)
DOMAIN = TrimRecord(TrimStatus.DOMAIN_REJECTED, ("alpha_out_of_fit:FR",), residual_norm=0.0, starts_tried=1)
PHYSICAL = TrimRecord(TrimStatus.PHYSICALLY_INFEASIBLE, ("no_equilibrium",), residual_norm=0.01, starts_tried=1)


def stub(edges):
    def trial(ay_g, warm):
        for edge, record in edges:
            if ay_g > edge:
                return record
        return ACCEPTED

    return trial


def test_record_needs_a_reason_from_its_own_status():
    with pytest.raises(ValueError, match="needs a reason"):
        TrimRecord(TrimStatus.DOMAIN_REJECTED)
    with pytest.raises(ValueError, match="not domain_rejected reasons"):
        TrimRecord(TrimStatus.DOMAIN_REJECTED, ("no_equilibrium",))
    with pytest.raises(ValueError, match="no failure reason"):
        TrimRecord(TrimStatus.ACCEPTED, ("tire_use:FL",))


def test_combine_prefers_an_accepted_start_and_counts_starts():
    picked = combine([NUMERICAL, ACCEPTED, DOMAIN])
    assert picked.accepted and picked.starts_tried == 3


def test_combine_never_turns_a_domain_rejection_into_physics():
    picked = combine([PHYSICAL, DOMAIN, NUMERICAL])
    assert picked.status is TrimStatus.DOMAIN_REJECTED
    assert picked.reasons == ("alpha_out_of_fit:FR",)


def test_combine_prefers_physical_evidence_over_a_numerical_failure():
    assert combine([NUMERICAL, PHYSICAL]).status is TrimStatus.PHYSICALLY_INFEASIBLE
    assert combine([NUMERICAL]).status is TrimStatus.NUMERICAL_FAILURE


@pytest.mark.parametrize(("record", "kind"), [
    (PHYSICAL, BoundKind.PHYSICAL), (DOMAIN, BoundKind.DOMAIN), (NUMERICAL, BoundKind.NUMERICAL),
])
def test_bisection_reports_what_stopped_it(record, kind):
    limit = bisect_limit(stub([(0.8, record)]), (0.02, 0.1), high=2.5, tol=1e-4)
    assert limit.bound_kind is kind
    assert limit.upper_status is record.status
    assert limit.ay_g == pytest.approx(0.8, abs=1e-4)
    assert limit.is_grip_limit is (kind is BoundKind.PHYSICAL)
    assert (limit.grip_g() is None) is (kind is not BoundKind.PHYSICAL)


def test_a_numerical_failure_is_not_a_grip_limit():
    limit = bisect_limit(stub([(1.2, PHYSICAL), (0.8, NUMERICAL)]), (0.02,), high=2.5, tol=1e-4)
    assert limit.bound_kind is BoundKind.NUMERICAL
    assert limit.grip_g() is None
    assert limit.columns()["ay_accepted_g"] == pytest.approx(0.8, abs=1e-4)


def test_no_failure_below_the_ceiling_is_a_search_ceiling():
    limit = bisect_limit(stub([]), (0.02,), high=2.5, tol=1e-3)
    assert limit.bound_kind is BoundKind.SEARCH_CEILING
    assert limit.upper is None and not limit.is_grip_limit


def test_no_accepted_start_keeps_the_failure_status():
    limit = bisect_limit(stub([(0.0, DOMAIN)]), (0.02, 0.1, 0.3), high=2.5, tol=1e-3)
    assert limit.ay_g is None
    assert limit.lower_status is TrimStatus.DOMAIN_REJECTED
    assert limit.bound_kind is BoundKind.DOMAIN
    assert limit.upper.starts_tried == 3


def test_limit_result_rejects_a_nan_value():
    with pytest.raises(ValueError, match="finite"):
        LimitResult(float("nan"), BoundKind.PHYSICAL, TrimStatus.ACCEPTED)
    with pytest.raises(ValueError, match="exactly when"):
        LimitResult(None, BoundKind.PHYSICAL, TrimStatus.ACCEPTED)


def test_not_modeled_limit_has_no_value():
    limit = LimitResult.not_modeled("braking_diff_coupling_withheld")
    assert limit.ay_g is None and limit.bound_kind is BoundKind.NOT_MODELED
    assert limit.columns()["trim_status"] == "not_modeled"
