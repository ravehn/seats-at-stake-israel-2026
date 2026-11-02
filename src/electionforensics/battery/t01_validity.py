"""T01 — data validity.

Arithmetic and logical integrity of each published station: turnout above 100 %, turnout of
exactly 100 % at a station with a real electorate, zero voters with a real electorate, counts
that do not add up, duplicated station identifiers, missing electorate. These are not anomaly
scores: they are facts about the file, reported first so that every later test can state which
rows it excluded and why.
"""

from __future__ import annotations

import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features


@register("T01")
def t01_validity(feat: Features) -> TestResult:
    st = feat.stations
    vcols = [f"v_{p}" for p in feat.parties]
    f = pd.DataFrame({k: st[k] for k in ["knesset", "locality_code", "locality_name", "station", "sector", "eligible", "voters", "invalid", "valid"]})
    f["turnout"] = st["turnout"]
    f["flag_turnout_gt_100"] = (st["voters"] > st["eligible"]).fillna(False)
    f["flag_turnout_eq_100"] = ((st["voters"] == st["eligible"]) & (st["eligible"] >= 50)).fillna(False)
    f["flag_zero_voters"] = ((st["voters"] == 0) & (st["eligible"] > 0)).fillna(False)
    f["flag_sum_parties_ne_valid"] = st[vcols].sum(axis=1) != st["valid"]
    f["flag_valid_plus_invalid_ne_voters"] = (st["valid"] + st["invalid"]) != st["voters"]
    f["flag_missing_eligible"] = st["eligible"].isna()
    f["flag_duplicate_station"] = st.duplicated(subset=["locality_code", "station"], keep=False)
    f["flag_invalid_share_gt_10pct"] = (st["invalid_share"] > 0.10).fillna(False)
    flag_cols = [c for c in f.columns if c.startswith("flag_")]
    f["n_flags"] = f[flag_cols].sum(axis=1)
    summary = {c: int(f[c].sum()) for c in flag_cols}
    summary["n_stations"] = len(f)
    summary["n_flagged"] = int((f["n_flags"] > 0).sum())
    return TestResult("T01", "Data validity", feat.knesset, "station", f[f["n_flags"] > 0].reset_index(drop=True), summary)
