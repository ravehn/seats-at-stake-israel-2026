"""The single-file reference implementation of the article (docs/article/replication/reference.py) against the package.

Verification by redundancy: the reference is written separately, imports nothing from the package and reads only the
published tables; here it must agree with the package on every number the article states — the factual allocation
and its margin, every grid cell, the step curves, the breakpoints and the integration over δ — and both must reproduce
two hand-computed examples (Appendix F of the article).
"""

from __future__ import annotations

import importlib.util
import json
import math
from types import ModuleType

import pandas as pd
import pytest

from electionforensics.config import ROOT
from electionforensics.seats.allocation import allocate_totals, load_agreements, load_blocs
from electionforensics.seats.counterfactual import _largest_remainder
from electionforensics.seats.estimate import ARTICLE_DIR, MAIN_PRIOR, MC_MEDIAN, MC_SIGMA, PRIORS

REFERENCE = ROOT / "docs" / "article" / "replication" / "reference.py"
TABLES = ARTICLE_DIR / "tables"


def _reference() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reference_k26", REFERENCE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reference_constants_match_the_package_tables() -> None:
    ref = _reference()
    assert ref.AGREEMENTS[26] == load_agreements(26)[0]
    assert ref.AGREEMENTS[25] == load_agreements(25)[0]
    blocs = load_blocs(26)
    assert blocs["change"] == ref.CHANGE and blocs["coalition"] == ref.COALITION and blocs["arab"] == ref.ARAB
    assert ref.THRESHOLD == 0.0325 and ref.SEATS == 120
    assert {k: (p["low"], p["high"]) for k, p in PRIORS.items()} == ref.PRIORS and ref.MAIN_PRIOR == MAIN_PRIOR
    assert math.isclose(ref.MC_MEDIAN, MC_MEDIAN) and math.isclose(ref.MC_SIGMA, MC_SIGMA)


def test_hand_example_of_the_allocation_in_both_implementations() -> None:
    """10 seats, A 60 / B 25 / C 15. Without an agreement the quotients 60, 30, 25, 20, 15, 15, 12.5, 12, 10, 8.57 give
    A 7, B 2, C 1. With a B–C agreement the pair's quotients are 40, 20, 13.3, 10, 8: the 10th seat goes to the pair
    (10 > 8.57), so A 6, pair 4, and inside the pair 25, 15, 12.5, 8.33 > 7.5 gives B 3, C 1. The runner-up A lacked
    (10 − 60/7) · 7 = 10 votes."""
    ref = _reference()
    votes = {"A": 60, "B": 25, "C": 15}
    with_pair = ref.allocate(votes, [("B", "C")], threshold=0.0, seats=10)
    assert with_pair["seats"] == {"A": 6, "B": 3, "C": 1}
    assert math.isclose(with_pair["margin_votes"], 10.0)
    assert ref.allocate(votes, [], threshold=0.0, seats=10)["seats"] == {"A": 7, "B": 2, "C": 1}
    pkg = allocate_totals(votes, 0.0, [("B", "C")], seats=10)
    assert pkg.seats == {"A": 6, "B": 3, "C": 1}
    assert pkg.margin_votes is not None and math.isclose(pkg.margin_votes, 10.0)
    assert allocate_totals(votes, 0.0, [], seats=10).seats == {"A": 7, "B": 2, "C": 1}


def test_hand_example_of_the_transfer_in_both_implementations() -> None:
    """A unit with 1,000 valid votes, 300 for Likud and 200 / 100 / 0 / 50 for the change lists, δ = 2: 20 votes leave
    Likud; quotas 11.43 / 5.71 / 0 / 2.86, floors 11 / 5 / 0 / 2, the two largest remainders (.86, .71) take one more."""
    ref = _reference()
    assert ref.largest_remainder(20, [200, 100, 0, 50]) == [11, 6, 0, 3]
    import numpy as np

    assert _largest_remainder(np.array([20]), np.array([[200, 100, 0, 50]])).tolist() == [[11, 6, 0, 3]]
    unit = {"locality_code": 1, "station": "1", "valid": 1000, "likud": 300, "change": [200, 100, 0, 50]}
    t = ref.transfer([unit], 2.0, "proportional")
    assert t["moved"] == 20 and t["capped"] == 0 and t["gains"] == {"דרך": 11, "אמת": 6, "ל": 0, "רק": 3}
    # the cap: a unit where Likud has fewer votes than the rule asks for
    poor = {"locality_code": 2, "station": "1", "valid": 1000, "likud": 10, "change": [1, 1, 1, 1]}
    t = ref.transfer([poor], 5.0, "proportional")
    assert t["moved"] == 10 and t["capped"] == 40
    # no recipients: the votes stay with Likud in the proportional variant, vanish in the turnout channel
    empty = {"locality_code": 3, "station": "1", "valid": 1000, "likud": 300, "change": [0, 0, 0, 0]}
    assert ref.transfer([empty], 2.0, "proportional")["moved"] == 0
    assert ref.transfer([empty], 2.0, "proportional")["n_units_without_recipients"] == 1
    assert ref.transfer([empty], 2.0, "none")["moved"] == 20


def test_exact_integration_matches_a_closed_form() -> None:
    """A curve that is 1 from δ = 2 upwards gives P(≥ 1) = P(δ ≥ 2) = 1 − Φ((ln 2 − μ)/σ)."""
    ref = _reference()
    lost = [0] * 200 + [1] * 801
    it = ref.integrate_curve(lost)
    expected = 1 - ref.normal_cdf((math.log(2.0) - math.log(ref.MC_MEDIAN)) / ref.MC_SIGMA)
    assert math.isclose(it["p_ge"]["1"], expected, rel_tol=1e-12)
    assert math.isclose(it["mean_seats_lost"], expected, rel_tol=1e-12)
    assert math.isclose(it["p_ge"]["1"] + it["pmf_seats_lost"]["0"], 1.0, rel_tol=1e-12)


def test_reference_reproduces_every_number_of_the_article() -> None:
    results_path = ARTICLE_DIR / "results.json"
    if not results_path.exists() or not (TABLES / "units_A.csv").exists():
        pytest.skip("article results or unit tables not built")
    ref = _reference()
    out = ref.compute(TABLES)
    results = json.loads(results_path.read_text(encoding="utf-8"))
    # the factual allocation, the last seat and its margin
    f = results["factual"]
    assert out["factual"]["seats"] == f["seats"]
    assert math.isclose(out["factual"]["margin_votes"], f["margin_votes"], rel_tol=1e-9)
    assert out["factual"]["last_awarded"][0] == f["last_awarded"][0] and out["factual"]["runner_up"][0] == f["runner_up"][0]
    assert out["factual"]["blocs"] == f["blocs"]
    assert out["no_agreements_blocs"] == results["no_agreements"]
    # the 2022 election from the published totals
    assert out["k25"]["match"] is True and out["k25"]["computed"] == results["verification_previous"]["official"]
    # every grid cell of the three readings the article integrates over
    pkg = {(r["coverage"], float(r["delta"]), r["variant"]): r for r in results["grid"]}
    n = 0
    for cell in out["grid"]:
        r = pkg[(cell["coverage"], float(cell["delta"]), cell["variant"])]
        assert cell["seats"] == r["seats"], (cell["coverage"], cell["delta"], cell["variant"])
        assert cell["d_blocs"] == r["d_blocs"] and cell["moved"] == r["moved"] and cell["capped"] == r["capped"]
        assert cell["gains"] == dict(r["gains"])
        assert cell["n_units"] == r["n_units"] and cell["n_units_without_recipients"] == r["n_units_without_recipients"]
        assert math.isclose(cell["margin_votes"], r["margin_votes"], rel_tol=1e-9)
        assert cell["eligible_changed"] == r["eligible_changed"]
        n += 1
    assert n == 3 * 6 * 6
    # the step curves and the breakpoints
    curves = pd.read_csv(TABLES / "curves_main.csv")
    for key, lost in out["curves"].items():
        c = curves[curves["coverage"] == key].sort_values("delta")
        assert (-c["d_coalition"]).tolist() == lost
        assert out["breakpoints"][key] == results["breakpoints"][key]
    # the exact integral against the package's 200,000 draws: within the sampling error of the draws
    for key, it in out["integration"].items():
        mc = results["mc"][key]
        # 200,000 draws: standard error ≤ 0.0011 on a probability, so half a point is more than four standard errors
        for k in ("1", "2", "3", "4"):
            assert abs(it["p_ge"][k] - mc["p_ge"][k]) < 0.005, (key, k, it["p_ge"][k], mc["p_ge"][k])
        assert abs(it["mean_seats_lost"] - mc["mean_seats_lost"]) < 0.01
        assert abs(it["share_above_curve_max"] - mc["share_above_curve_max"]) < 0.005
    # the same under every distribution of δ of Table 3b (the robustness table of § 5.3)
    assert set(out["integration_priors"]) == set(results["mc_priors"]) == set(ref.PRIORS)
    for pk, per in out["integration_priors"].items():
        for key, it in per.items():
            mc = results["mc_priors"][pk][key]
            for k in ("1", "2", "3", "4"):
                assert abs(it["p_ge"][k] - mc["p_ge"][k]) < 0.005, (pk, key, k, it["p_ge"][k], mc["p_ge"][k])
            assert abs(it["mean_seats_lost"] - mc["mean_seats_lost"]) < 0.01


def test_reference_itinerary_threshold_matches_the_package() -> None:
    """Reading I: the reference derives τ from the published itinerary table and must agree with results.json."""
    results_path = ARTICLE_DIR / "results.json"
    table = TABLES / "itinerary_k26.csv"
    if not results_path.exists() or not table.exists():
        pytest.skip("article results or itinerary table not built")
    ref = _reference()
    got = ref.itinerary_threshold(ref.read_itinerary(table))
    it = json.loads(results_path.read_text(encoding="utf-8"))["itinerary"]
    assert it is not None
    assert got["tau"] == it["tau"] and math.isclose(got["min_share"], it["min"]["share_prev"], rel_tol=1e-12)
    assert math.isclose(got["max"], it["max"]["share_prev"], rel_tol=1e-12)
    assert math.isclose(got["median"], it["median_share_prev"], rel_tol=1e-12)
    assert got["n_localities"] == it["n_ordinary_localities"] and got["n_appearances"] == it["n_appearances"]
    assert got["n_ordinary_appearances"] == it["n_ordinary_appearances"]
    assert round(100 * got["tau"], 1) == 28.5
