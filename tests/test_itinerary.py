"""The prime minister's campaign itinerary behind reading I (Appendix G of the article).

Two things are checked: the form of the hand-curated table `docs/article/itinerary_sources_k26.csv` (the window, the
locality codes, the sources) and its consequence in the package — the threshold τ of reading I is the lowest 2022 Likud
share among the ordinary stops, the set it selects does not depend on whether τ is taken exactly or as printed, and the
shares of the visited localities agree with the values computed independently from the station file when the
itinerary was compiled (31.10.2026).
"""

from __future__ import annotations

import math
import re
from datetime import date

import pytest

from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import load_election
from electionforensics.ingest.registry import snapshot_files
from electionforensics.model.features import build_features
from electionforensics.seats.coverage import ITINERARY_TAU, build_coverages
from electionforensics.seats.itinerary import (
    ITINERARY_FILE,
    URL_COLUMNS,
    WINDOW,
    itinerary_records,
    itinerary_summary,
    itinerary_table,
    load_itinerary,
)

PINNED_SNAPSHOT = "expb_20261030T121730Z.csv"
#: 2022 Likud share (%), 2026 share (%) and 2026 valid votes per visited locality, computed on 31.10.2026 directly from
#: the station files (`ingest.cec.load_election`, regular stations) when the itinerary was compiled — an independent path
EXPECTED = {
    2200: (50.9, 51.9, 20_976), 2800: (49.5, 48.0, 12_259), 2100: (46.1, 47.5, 18_237), 6800: (42.8, 41.0, 36_308),
    1031: (41.5, 39.4, 20_474), 9000: (40.3, 39.2, 113_940), 8500: (39.4, 38.0, 44_211), 2630: (36.3, 39.0, 37_231),
    6200: (35.7, 33.5, 71_087), 7400: (35.5, 35.5, 129_557), 6600: (34.7, 32.7, 111_064), 502: (34.4, 31.9, 7_652),
    240: (33.1, 30.1, 15_200), 7600: (32.8, 30.3, 30_540), 31: (32.1, 33.4, 20_555), 70: (28.9, 30.2, 127_121),
    7000: (28.5, 24.9, 45_317), 3000: (19.1, 16.9, 299_415), 5000: (17.0, 14.1, 309_040), 2610: (15.5, 13.9, 65_511),
    3765: (15.5, 10.2, 2_037),
}


def test_itinerary_table_is_well_formed() -> None:
    t = load_itinerary()
    assert ITINERARY_FILE.name == "itinerary_sources_k26.csv" and len(t) == 29
    assert t["appearance"].is_unique and t["appearance"].str.fullmatch(r"A\d+").all()
    lo, hi = (date.fromisoformat(d) for d in WINDOW)
    assert all(lo <= date.fromisoformat(d) <= hi for d in t["date"])
    assert list(t["date"]) == sorted(t["date"])  # chronological
    ordinary = t[t["in_rule"]]
    assert len(ordinary) == 18 and ordinary["locality_code"].notna().all() and ordinary["locality_code"].nunique() == 17
    assert (ordinary["exclusion_reason"].str.strip() == "").all()
    excluded = t[~t["in_rule"]]
    assert len(excluded) == 11 and (excluded["exclusion_reason"].str.strip() != "").all()
    assert t["locality_code"].isna().sum() == 1 and t.loc[t["locality_code"].isna(), "locality_en"].item() == "Tel Hai"
    for col in ("locality_he", "locality_en", "venue", "format", "sources", "url_1"):
        assert (t[col].str.strip() != "").all(), col
    urls = [u for c in URL_COLUMNS for u in t[c] if u]
    assert all(re.fullmatch(r"https://[^\s]+", u) for u in urls)
    assert not any("bldtna" in u for u in urls)  # the project's blocklisted host never appears as a live address
    # Hebrew names are spelt as the Committee's file spells them, so that the join by code and the name agree
    assert set(t["locality_he"]) >= {"קרית גת", "קרית אתא", "קרית שמונה", "יקנעם עילית", "באר שבע"}


def _features() -> tuple[object, object]:
    files = {p.name: p for p in snapshot_files("expb")}
    if PINNED_SNAPSHOT not in files:
        pytest.skip("pinned snapshot not on disk")
    cbs = load_cbs()
    return build_features(load_election(25), cbs), build_features(load_election(26, files[PINNED_SNAPSHOT]), cbs)


def test_itinerary_threshold_and_shares_on_real_data() -> None:
    f25, f26 = _features()
    table = itinerary_table(load_itinerary(), f25, f26)  # type: ignore[arg-type]
    coded = table[table["locality_code"].notna()].drop_duplicates("locality_code")
    assert set(coded["locality_code"].astype(int)) == set(EXPECTED)
    for _, r in coded.iterrows():
        prev, curr, valid = EXPECTED[int(r["locality_code"])]
        assert round(100 * r["likud_share_prev"], 1) == prev, r["locality_en"]
        assert round(100 * r["likud_share_curr"], 1) == curr, r["locality_en"]
        assert int(r["valid_curr"]) == valid, r["locality_en"]
    tel_hai = table[table["locality_en"] == "Tel Hai"].iloc[0]
    assert math.isnan(tel_hai["likud_share_prev"]) and math.isnan(tel_hai["likud_share_curr"])

    s = itinerary_summary(table, f26, f25)  # type: ignore[arg-type]
    assert s["n_appearances"] == 29 and s["n_ordinary_appearances"] == 18 and s["n_ordinary_localities"] == 17
    assert s["min"]["locality_en"] == "Lod" and s["max"]["locality_en"] == "Dimona"
    # τ: the lowest share (Lod, 28.53 %) rounded down to a tenth of a point — the printed threshold is the one applied,
    # the lowest stop is inside the set by construction, and the value equals the fallback constant
    assert s["tau"] == ITINERARY_TAU == 0.285 and s["tau"] <= s["min"]["share_prev"] < s["tau"] + 0.001
    assert round(s["median_share_prev"], 3) == 0.357
    assert s["yokneam_rank"] == 13 and round(s["yokneam_share_prev"], 3) == 0.331
    # the rule evaluated two ways — here and as coverage I: the same set, and it contains every ordinary stop
    cov = build_coverages(f25, f26, ("I",), itinerary_tau=s["tau"])["I"]
    assert cov.n_localities == s["rule_at_tau"]["n_localities"] and cov.n_stations == s["rule_at_tau"]["n_stations"]
    assert math.isclose(cov.share_regular, s["rule_at_tau"]["share_regular"], rel_tol=1e-12)
    ordinary_codes = {int(c) for c in table.loc[table["in_rule"], "locality_code"]}
    assert ordinary_codes <= cov.localities and 7000 in ordinary_codes  # Lod, the lowest stop
    assert 0.35 < s["rule_at_tau"]["share_regular"] < 0.38
    # the alternative with the sectoral stops counted: the threshold drops to Eli's share and the rule widens towards reading B
    alt = s["rule_at_tau_with_sectoral_stops"]
    assert s["tau_with_sectoral_stops"] == 0.154 and alt["tau"] == s["tau_with_sectoral_stops"]
    assert 3765 in build_coverages(f25, f26, ("I",), itinerary_tau=alt["tau"])["I"].localities  # Eli is inside
    assert alt["share_regular"] > s["rule_at_tau"]["share_regular"] and 0.70 < alt["share_regular"] < 0.80
    assert 0 < s["ordinary_share_regular"] < s["visited_share_regular"] < 0.35

    recs = itinerary_records(table)
    assert len(recs) == 29 and all(len(r["urls"]) >= 1 for r in recs)
    th = next(r for r in recs if r["locality_en"] == "Tel Hai")
    assert th["locality_code"] is None and th["likud_share_prev"] is None and th["in_rule"] is False
    assert all(isinstance(r["likud_share_prev"], float) for r in recs if r["locality_code"] is not None)
