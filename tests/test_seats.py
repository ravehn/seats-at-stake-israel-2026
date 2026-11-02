"""Seat module: Bader–Ofer reproduction, coverage sets, the counterfactual transfer and the estimate end to end."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from electionforensics.config import THRESHOLD_BY_KNESSET
from electionforensics.ingest.cec import load_election
from electionforensics.ingest.registry import snapshot_files
from electionforensics.model.features import build_features
from electionforensics.seats.allocation import (
    allocate_totals,
    election_allocation,
    frame_seats,
    load_agreements,
    load_blocs,
    load_frames,
    totals_from_stations,
)
from electionforensics.seats.counterfactual import TransferEngine, Variant, _largest_remainder, variants
from electionforensics.seats.coverage import build_coverages, locality_table
from electionforensics.seats.estimate import OFFICIAL_25, PRIORS, code_fingerprint, run_estimate, save_results
from electionforensics.seats.scaling import ORDER_LIKUD, coverage_scaling, spread_by_share
from tests.conftest import features_from_rows, make_election, random_rows, tiny_cbs

PINNED_SNAPSHOT = "expb_20261030T121730Z.csv"
PINNED_SHA256 = "858b87d94c409759ea6ab2d22cc1c61fc9ff8bf3b77be669ba83e498c2cea021"
#: the allocation of the pinned snapshot as published by the CEC results site at the time (checked 2026-10-30/31)
PINNED_SEATS_26 = {"מחל": 27, "דרך": 21, "רק": 12, "ודם": 11, "אמת": 8, "עם": 8, "ל": 7, "ב": 7, "ג": 7, "שס": 6, "ט": 6}


def test_allocation_reproduces_the_official_2022_result() -> None:
    data = load_election(25)
    a = election_allocation(25, data.stations, data.parties)
    assert {p: s for p, s in a.seats.items() if s > 0} == OFFICIAL_25
    assert sum(a.seats.values()) == 120
    assert a.last_awarded is not None and a.runner_up is not None
    assert a.margin_votes is not None and a.margin_votes > 0
    assert a.bloc_seats(load_blocs(25)) == {"coalition": 64, "change": 46, "arab": 10, "other": 0}


def test_allocation_of_the_pinned_2026_snapshot() -> None:
    files = {p.name: p for p in snapshot_files("expb")}
    if PINNED_SNAPSHOT not in files:
        pytest.skip("pinned snapshot not on disk")
    data = load_election(26, files[PINNED_SNAPSHOT])
    assert data.sha256 == PINNED_SHA256
    a = election_allocation(26, data.stations, data.parties)
    assert a.seats == PINNED_SEATS_26
    assert sum(a.seats.values()) == 120
    assert a.bloc_seats(load_blocs(26)) == {"coalition": 53, "change": 48, "arab": 19, "other": 0}
    # the alternative frame of the appendix: opposition with Ra'am / Netanyahu bloc / Joint List / change without Beyachad
    assert frame_seats(a.seats, load_frames(26)) == {"opposition_with_raam": 56, "netanyahu_bloc": 53, "joint_list": 11,
                                                      "change_without_beyachad": 36}
    assert load_frames(25) == {}
    assert all(p in a.eligible for pair in a.agreements for p in pair)
    # a pair with a partner below the threshold is reported as void and never applied
    totals = totals_from_stations(data.stations, data.parties)
    below = [p for p in data.parties if p not in a.eligible][:2]
    with_void = allocate_totals(totals, THRESHOLD_BY_KNESSET[26], [*a.agreements, (below[0], below[1])])
    assert with_void.seats == a.seats
    assert any("void" in n for n in with_void.notes)


def test_allocation_without_agreements_differs_by_one_seat_in_2026() -> None:
    files = {p.name: p for p in snapshot_files("expb")}
    if PINNED_SNAPSHOT not in files:
        pytest.skip("pinned snapshot not on disk")
    data = load_election(26, files[PINNED_SNAPSHOT])
    totals = totals_from_stations(data.stations, data.parties)
    pairs, _ = load_agreements(26)
    with_pairs = allocate_totals(totals, THRESHOLD_BY_KNESSET[26], pairs).seats
    without = allocate_totals(totals, THRESHOLD_BY_KNESSET[26], []).seats
    assert sum(abs(with_pairs[p] - without[p]) for p in with_pairs) == 2


def test_largest_remainder_is_exact_and_proportional() -> None:
    total = np.array([10, 7, 0, 5])
    w = np.array([[1, 1, 2], [3, 0, 1], [1, 1, 1], [0, 0, 0]])
    out = _largest_remainder(total, w)
    assert out.sum(axis=1).tolist() == [10, 7, 0, 0]  # a row without recipients receives nothing
    assert out[0].tolist() == [2, 3, 5] or out[0].tolist() == [3, 2, 5]
    assert out[1].tolist() == [5, 0, 2]


def _synthetic() -> tuple[object, object]:
    rng = np.random.default_rng(7)
    rows = random_rows(rng, n_localities=30, stations_per=6)
    return rows, rng


def test_transfer_conserves_votes_except_in_the_turnout_channel() -> None:
    rows, _ = _synthetic()
    feat = features_from_rows(rows)
    data = make_election(rows)
    mask = feat.stations["locality_code"] < 1015
    eng = TransferEngine(data.stations, feat.stations, mask, feat.parties, ["פה"])
    base = sum(eng.base_totals.values())
    for v in variants(["פה"]):
        t = eng.run(2.0, v)
        if v.recipients == "none":
            assert sum(t.totals.values()) == base - t.moved
        else:
            assert sum(t.totals.values()) == base
            assert sum(t.gains.values()) == t.moved
        assert t.totals["מחל"] == eng.base_totals["מחל"] - t.moved
        assert t.moved >= 0 and t.capped >= 0
    # δ = 0 moves nothing and leaves the totals as they were
    t0 = eng.run(0.0, variants(["פה"])[0])
    assert t0.moved == 0 and t0.totals == eng.base_totals
    # the removal is capped at the Likud votes present: a huge δ cannot drive Likud negative
    big = eng.run(99.0, Variant("x", "station", "proportional", ""))
    assert big.totals["מחל"] >= 0 and big.capped > 0


def test_transfer_at_delta_zero_keeps_the_factual_allocation() -> None:
    rows, _ = _synthetic()
    feat = features_from_rows(rows)
    data = make_election(rows)
    mask = pd.Series(True, index=feat.stations.index)
    eng = TransferEngine(data.stations, feat.stations, mask, feat.parties, ["פה"])
    factual = allocate_totals(eng.base_totals, 0.0325, [])
    again = eng.run(0.0, variants(["פה"])[0]).allocate(0.0325, [])
    assert again.seats == factual.seats
    moved = eng.run(3.0, variants(["פה"])[0]).allocate(0.0325, [])
    assert moved.seats["מחל"] <= factual.seats["מחל"]
    assert sum(moved.seats.values()) == 120


def test_station_and_locality_units_remove_the_same_order_of_votes() -> None:
    rows, _ = _synthetic()
    feat = features_from_rows(rows)
    data = make_election(rows)
    mask = pd.Series(True, index=feat.stations.index)
    eng = TransferEngine(data.stations, feat.stations, mask, feat.parties, ["פה", "ג"])
    vs = {v.key: v for v in variants(["פה", "ג"])}
    a, b = eng.run(1.5, vs["main"]), eng.run(1.5, vs["station"])
    assert abs(a.moved - b.moved) <= eng.n_covered_stations  # rounding per unit only
    assert set(a.gains) == {"פה", "ג"} == set(b.gains)


def test_transfer_can_be_restricted_to_a_subset_of_localities() -> None:
    rows, _ = _synthetic()
    feat = features_from_rows(rows)
    data = make_election(rows)
    mask = pd.Series(True, index=feat.stations.index)
    eng = TransferEngine(data.stations, feat.stations, mask, feat.parties, ["פה"])
    main = variants(["פה"])[0]
    codes = sorted({int(c) for c in feat.stations["locality_code"].dropna()})
    full, part = eng.run(2.0, main), eng.run(2.0, main, localities=codes[: len(codes) // 2])
    assert part.n_units == len(codes) // 2 and 0 < part.moved < full.moved
    assert eng.run(2.0, main, localities=codes).moved == full.moved
    assert eng.run(2.0, main, localities=[]).moved == 0


def test_coverage_scaling_on_synthetic_elections() -> None:
    rng = np.random.default_rng(11)
    rows = random_rows(rng, n_localities=40, stations_per=4)
    cbs = tiny_cbs(sorted({int(r["locality_code"]) for r in rows}))  # type: ignore[call-overload]
    d24, d25 = make_election(rows, 24), make_election([dict(r) for r in rows], 25)
    f24, f25 = build_features(d24, cbs), build_features(d25, cbs)
    cov = build_coverages(f24, f25, ("B",))["B"]
    blocs = load_blocs(25)
    eng = TransferEngine(d25.stations, f25.stations, cov.station_mask, d25.parties, blocs["change"])
    main = variants([p for p in blocs["change"] if p in d25.parties])[0]
    factual = election_allocation(25, d25.stations, d25.parties)
    agreements, _ = load_agreements(25)
    sc = coverage_scaling(eng, main, cov, f25.stations, locality_table(f24, f25), factual, blocs, THRESHOLD_BY_KNESSET[25],
                          agreements, deltas=(2.0, 8.0), fractions=(0.25, 0.5, 0.75, 1.0), n_random=2, seed=3)
    assert set(sc["order"]) == {ORDER_LIKUD, "random order 1", "random order 2"} and len(sc) == 3 * 4 * 2
    for (_order, _delta), g in sc.groupby(["order", "delta"]):
        g = g.sort_values("target_share")
        assert (g["covered_share_of_set"] <= g["target_share"] + 1e-9).all() and (g["n_localities"] >= 1).all()
        assert g["votes_moved"].is_monotonic_increasing and g["likud_seats_lost"].is_monotonic_increasing
    # at 100 % every order covers the same set and gives the same seats, and the spread is well defined per δ
    full = sc[sc["target_share"] == 1.0]
    assert full.groupby("delta")["coalition_seats_moved"].nunique().eq(1).all()
    assert set(spread_by_share(sc)) == {"2", "8"}


def test_coverage_scaling_matches_the_grid_on_real_data() -> None:
    from electionforensics.ingest.cbs import load_cbs
    from electionforensics.seats.estimate import SCALING_COVERAGE, _external_valid

    files = {p.name: p for p in snapshot_files("expb")}
    if PINNED_SNAPSHOT not in files:
        pytest.skip("pinned snapshot not on disk")
    cbs = load_cbs()
    d25, d26 = load_election(25), load_election(26, files[PINNED_SNAPSHOT])
    f25, f26 = build_features(d25, cbs), build_features(d26, cbs)
    cov = build_coverages(f25, f26, (SCALING_COVERAGE,), external_valid=_external_valid(d26))[SCALING_COVERAGE]
    blocs = load_blocs(26)
    eng = TransferEngine(d26.stations, f26.stations, cov.station_mask, d26.parties, blocs["change"])
    main = variants([p for p in blocs["change"] if p in d26.parties])[0]
    factual = election_allocation(26, d26.stations, d26.parties)
    agreements, _ = load_agreements(26)
    sc = coverage_scaling(eng, main, cov, f26.stations, locality_table(f25, f26), factual, blocs, THRESHOLD_BY_KNESSET[26],
                          agreements, fractions=(0.5, 1.0))
    full = eng.run(5.0, main).allocate(THRESHOLD_BY_KNESSET[26], agreements)
    expect = factual.bloc_seats(blocs)["coalition"] - full.bloc_seats(blocs)["coalition"]
    at_full = sc[(sc["target_share"] == 1.0) & (sc["delta"] == 5.0)]
    assert (at_full["coalition_seats_moved"] == expect).all() and (at_full["covered_share_of_set"] > 0.999).all()
    # the premise's own ordering and random orders agree to within one seat (research note of 01.11.2026, § 2)
    assert max(spread_by_share(sc).values()) <= 1


def test_coverage_sets_on_real_data_match_the_round_3_note() -> None:
    from electionforensics.ingest.cbs import load_cbs

    files = {p.name: p for p in snapshot_files("expb")}
    if PINNED_SNAPSHOT not in files:
        pytest.skip("pinned snapshot not on disk")
    cbs = load_cbs()
    f25 = build_features(load_election(25), cbs)
    f26 = build_features(load_election(26, files[PINNED_SNAPSHOT]), cbs)
    cov = build_coverages(f25, f26, ("A", "B", "B_haredi"))
    assert cov["A"].n_localities == 361 and round(100 * cov["A"].share_regular, 1) == 50.0
    assert round(100 * cov["B_haredi"].share_regular, 1) == 82.2
    assert cov["B"].n_stations < cov["B_haredi"].n_stations
    assert cov["B"].station_mask.index.equals(f26.stations.index)
    # B is defined on the 2022 sector labels; a locality that became haredi by the 2022 vote may carry a trace (< 0.1 %)
    assert cov["B"].by_sector.get("haredi", 0.0) < 0.001


def test_estimate_end_to_end_on_synthetic_elections(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    rows_prev = random_rows(rng, n_localities=25, stations_per=5)
    rows_curr = [dict(r) for r in rows_prev]
    codes = sorted({int(r["locality_code"]) for r in rows_prev})  # type: ignore[call-overload]
    cbs = tiny_cbs(codes)
    # the synthetic lists (מחל, פה, ג, ודעם) exist in the 24th/25th Knesset bloc tables, so those are the years used
    d24, d25 = make_election(rows_prev, 24), make_election(rows_curr, 25)
    f24, f25 = build_features(d24, cbs), build_features(d25, cbs)
    res = run_estimate(deltas=(0.0, 1.0, 5.0), n_draws=500, seed=1, coverage_keys=("A", "B", "B_haredi"), prev=(d24, f24), curr=(d25, f25))
    assert {v["key"] for v in res["variants"]} == {"main", "station", "turnout"}  # no single-victim list present
    assert res["factual"]["blocs"]["coalition"] + res["factual"]["blocs"]["change"] + res["factual"]["blocs"]["arab"] + res["factual"]["blocs"]["other"] == 120
    zero = [r for r in res["grid"] if r["delta"] == 0.0]
    assert zero and all(r["d_likud"] == 0 and all(v == 0 for v in r["d_blocs"].values()) for r in zero)
    assert all(not r["eligible_changed"] for r in res["grid"])
    assert set(res["mc"]) == {"A", "B", "B_haredi"} and abs(sum(res["mc"]["A"]["pmf_seats_lost"].values()) - 1) < 1e-9
    # the robustness table: every distribution of δ, every integrated coverage; the main one equals `mc` draw for draw
    assert set(res["mc_priors"]) == set(PRIORS) and res["main_prior"] in res["mc_priors"]
    assert all(set(per) == {"A", "B", "B_haredi"} for per in res["mc_priors"].values())
    assert res["mc_priors"][res["main_prior"]]["A"]["p_ge"] == res["mc"]["A"]["p_ge"]
    assert all(abs(sum(m["pmf_seats_lost"].values()) - 1) < 1e-9 for per in res["mc_priors"].values() for m in per.values())
    assert res["frames"] == {} and res["factual"]["frames"] == {} and all(r["frames"] == {} for r in res["grid"])  # no frame for 25
    sc = res["coverage_scaling"]
    assert sc["coverage"] == "B_haredi" and sc["deltas"] == [5.0] and set(sc["marks"]) == {"A", "B"}
    full = [r for r in sc["rows"] if r["target_share"] == 1.0 and r["delta"] == 5.0]
    cell = next(r for r in res["grid"] if r["coverage"] == "B_haredi" and r["variant"] == "main" and r["delta"] == 5.0)
    assert full and all(r["coalition_seats_moved"] == -cell["d_blocs"]["coalition"] for r in full)
    paths = save_results(res, tmp_path)
    saved = json.loads(paths["results"].read_text(encoding="utf-8"))
    assert saved["code_sha256"] == code_fingerprint() and len(saved["code_sha256"]) == 64
    assert (tmp_path / "tables" / "grid_main.csv").exists() and (tmp_path / "tables" / "curves_main.csv").exists()
    assert (tmp_path / "tables" / "coverage_scaling.csv").exists()
    assert saved["data"]["current"]["sha256"] == "synthetic"
    # the published results file carries no machine path and says whether the snapshot was the newest on disk
    assert saved["data"]["raw_root"] == "data/raw"
    assert saved["data"]["current"]["is_latest"] is None  # synthetic data: no snapshot directory involved


def test_figures_are_byte_reproducible(tmp_path: Path) -> None:
    """Two renders of the same results give identical SVG and PNG bytes (replication package)."""
    from electionforensics.seats.estimate import ARTICLE_DIR, load_results
    from electionforensics.seats.figures import build_figures

    if not (ARTICLE_DIR / "results.json").exists():
        pytest.skip("no article results on disk")
    results = load_results(ARTICLE_DIR)
    first = {p.name: p.read_bytes() for p in build_figures(results, tmp_path / "a")}
    second = {p.name: p.read_bytes() for p in build_figures(results, tmp_path / "b")}
    assert first.keys() == second.keys() and len(first) == 10  # five figures, SVG and PNG each
    assert all(first[k] == second[k] for k in first), [k for k in first if first[k] != second[k]]


def test_article_results_carry_no_machine_path() -> None:
    """The committed results.json is part of the replication package: no drive letters, no user names."""
    from electionforensics.seats.estimate import ARTICLE_DIR

    p = ARTICLE_DIR / "results.json"
    if not p.exists():
        pytest.skip("no article results on disk")
    text = p.read_text(encoding="utf-8")
    assert "C:\\" not in text and "/Users/" not in text and "worktrees" not in text
