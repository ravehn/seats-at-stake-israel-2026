"""T12 — a station against its own history.

For each station matched to the previous election (same locality, same station number, comparable
electorate) we compute the change in turnout and in each major list's share, subtract the change
of the station's own locality (so that genuine local swings cancel), and express the residual as a
robust z within the station's sector (median / MAD across all matched stations of that sector).
Large residuals mean the station moved unlike its neighbours: the Macro Center's 2019 criterion,
made comparable across elections.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import load_election
from electionforensics.ingest.registry import ELECTIONS
from electionforensics.model.features import Features, build_features
from electionforensics.model.history import link

MAD_FLOOR = 0.01  # one percentage point: below this the sector is effectively constant and z would explode


def _peer_gap(m: pd.DataFrame, e_col: str, v_col: str, site_col: str) -> pd.Series:
    """Turnout of the station minus leave-one-out turnout of the other stations sharing its site."""
    if site_col not in m.columns:
        return pd.Series(np.nan, index=m.index)
    key = m["locality_code"].astype(str) + ":" + m[site_col].astype(str)
    e = m[e_col].astype(float)
    v = m[v_col].astype(float)
    se = e.groupby(key).transform("sum") - e
    sv = v.groupby(key).transform("sum") - v
    cnt = e.groupby(key).transform("size")
    peer = sv / se.where(se > 0)
    gap = v / e.where(e > 0) - peer
    return gap.where((cnt >= 3) & m[site_col].notna())


def _robust_z(x: pd.Series, by: pd.Series) -> pd.Series:
    med = x.groupby(by).transform("median")
    mad = (x - med).abs().groupby(by).transform("median") * 1.4826
    mad = mad.clip(lower=MAD_FLOOR)
    return (x - med) / mad


@register("T12")
def t12_history_shift(feat: Features, prev: Features | None = None) -> TestResult:
    k = feat.knesset
    if prev is None:
        if (k - 1) not in ELECTIONS:
            return TestResult("T12", "Shift against own history", k, "station", pd.DataFrame(), {"skipped": "no previous election"})
        prev = build_features(load_election(k - 1), load_cbs())
    m = link(prev, feat)
    m = m[m["good_match"]].copy()
    if m.empty:
        return TestResult("T12", "Shift against own history", k, "station", pd.DataFrame(), {"skipped": "no comparable stations"})
    # remove the locality's own swing
    m["d_turnout_res"] = m["d_turnout"] - m.groupby("locality_code")["d_turnout"].transform("mean")
    m["z_turnout"] = _robust_z(m["d_turnout_res"], m["sector"])
    zcols = ["z_turnout"]
    # peer drift: the station's turnout gap to its own building peers now, minus the same gap in the
    # previous election. A station that was always 50 points above its neighbours (different
    # population behind the same door) is normal; one whose gap opened by 30 points is not.
    gap_now = _peer_gap(m, "eligible", "voters", "site")
    gap_prev = _peer_gap(m, "eligible_prev", "voters_prev", "site_prev")
    m["d_gap_vs_peers"] = gap_now - gap_prev
    m["z_peerdrift"] = _robust_z(m["d_gap_vs_peers"], m["sector"])
    m.loc[m["d_gap_vs_peers"].isna(), "z_peerdrift"] = np.nan
    common = [p for p in feat.major if f"d_s_{p}" in m.columns]
    for p in common:
        m[f"d_s_{p}_res"] = m[f"d_s_{p}"] - m.groupby("locality_code")[f"d_s_{p}"].transform("mean")
        m[f"z_{p}"] = _robust_z(m[f"d_s_{p}_res"], m["sector"])
        zcols.append(f"z_{p}")
    zmat = m[zcols].to_numpy(dtype=float)
    absz = np.abs(np.nan_to_num(zmat, nan=0.0))
    m["max_abs_z"] = absz.max(axis=1)
    arg = absz.argmax(axis=1)
    m["top_dimension"] = [zcols[i][2:] for i in arg]
    m["top_z"] = zmat[np.arange(len(m)), arg]
    m["knesset"] = k
    m["station"] = m["station_num"].astype(str)
    keep = ["knesset", "locality_code", "locality_name", "station", "sector", "n_boxes", "eligible", "eligible_prev",
            "turnout", "turnout_prev", "d_turnout", "d_turnout_res", "z_turnout", "d_gap_vs_peers", "z_peerdrift",
            *[c for p in common for c in (f"s_{p}", f"s_{p}_prev", f"d_s_{p}_res", f"z_{p}")], "max_abs_z", "top_dimension", "top_z"]
    units = m[keep].reset_index(drop=True)
    mz = units["max_abs_z"]
    summary = {
        "previous_knesset": prev.knesset, "n_matched": len(units), "n_current_stations": len(feat.stations),
        "d_turnout_mean": float(m["d_turnout"].mean()), "d_turnout_sd": float(m["d_turnout"].std()),
        "max_abs_z_quantiles": {q: float(mz.quantile(q)) for q in (0.5, 0.9, 0.99, 0.999)},
        "n_above_4": int((mz > 4).sum()), "n_above_6": int((mz > 6).sum()), "n_above_8": int((mz > 8).sum()),
        "turnout_up_10pp_vs_locality": int((m["d_turnout_res"] > 0.10).sum()),
        "n_with_peer_drift": int(m["z_peerdrift"].notna().sum()),
        "peer_gap_up_15pp": int((m["d_gap_vs_peers"] > 0.15).sum()),
    }
    return TestResult("T12", "Shift against own history", k, "station", units, summary)
