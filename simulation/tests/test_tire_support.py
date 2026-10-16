import hashlib

import pytest

from tools.tire_support import TireSupport, TireSupportError


def test_support_reads_the_fit_ranges_and_hash(synthetic_tir):
    support = TireSupport.from_tir(synthetic_tir)
    assert support.fz == (100.0, 2000.0)
    assert support.alpha == (-0.2, 0.2)
    assert support.kappa == (-0.2, 0.2)
    assert support.gamma == (-0.05, 0.05)
    assert support.sha256 == hashlib.sha256(synthetic_tir.read_bytes()).hexdigest()
    assert support.source == "synthetic.tir"


@pytest.mark.parametrize("key", ["FZMAX", "ALPMIN", "KPUMAX", "CAMMIN"])
def test_a_missing_fit_limit_fails_and_names_it(edited_tir, key):
    path = edited_tir(lambda text: "\n".join(line for line in text.splitlines() if not line.startswith(key)))
    with pytest.raises(TireSupportError, match=key):
        TireSupport.from_tir(path)


def test_angles_in_degrees_fail(edited_tir):
    with pytest.raises(TireSupportError, match="ANGLE"):
        TireSupport.from_tir(edited_tir(lambda text: text.replace("'radians'", "'degrees'")))


def test_an_inverted_range_fails(edited_tir):
    with pytest.raises(TireSupportError, match="fz fit range"):
        TireSupport.from_tir(edited_tir(lambda text: text.replace("FZMIN = 100", "FZMIN = 3000")))


def test_violations_name_the_quantity_and_corner(synthetic_tir):
    support = TireSupport.from_tir(synthetic_tir)
    assert support.violations(500.0, 0.1, 0.05, 0.0, "FL") == ()
    assert support.violations(95.0, 0.1, 0.0, 0.0, "RL") == ("fz_out_of_fit:RL",)
    assert support.violations(500.0, 0.25, 0.0, 0.0, "FR") == ("alpha_out_of_fit:FR",)
    assert support.violations(500.0, 0.0, 0.3, 0.0, "RR") == ("kappa_out_of_fit:RR",)
    assert support.violations(500.0, 0.0, 0.0, -0.06, "FL") == ("camber_out_of_fit:FL",)
    assert support.violations(500.0, float("nan"), 0.0, 0.0) == ("alpha_out_of_fit",)


def test_search_bounds_stay_inside_the_fit(synthetic_tir):
    support = TireSupport.from_tir(synthetic_tir)
    assert support.search_bounds("kappa", 0.0, 0.5) == (0.0, 0.2)
    assert support.search_bounds("alpha", 0.0, 0.6) == (0.0, 0.2)
    with pytest.raises(TireSupportError):
        support.search_bounds("fz", 2500.0, 3000.0)


def test_the_summary_names_the_fit_limits_without_their_values(synthetic_tir):
    support = TireSupport.from_tir(synthetic_tir)
    summary = support.summary()
    assert summary["sha256"] == support.sha256
    assert summary["limit_keys"] == {"fz": ["FZMIN", "FZMAX"], "alpha": ["ALPMIN", "ALPMAX"],
                                     "kappa": ["KPUMIN", "KPUMAX"], "gamma": ["CAMMIN", "CAMMAX"]}

    def leaves(node):
        if isinstance(node, dict):
            return [x for v in node.values() for x in leaves(v)]
        if isinstance(node, (list, tuple)):
            return [x for v in node for x in leaves(v)]
        return [node]

    assert not [v for v in leaves(summary) if isinstance(v, (int, float))]
