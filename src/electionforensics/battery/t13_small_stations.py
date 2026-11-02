"""T13 — small stations against the large ones of the same locality (Jiménez et al. 2017).

Rigging is cheapest where few people watch: a small station needs one committee, one observer who
left early, one protocol. Jiménez, Hidalgo, Klimek, Thurner and Hernández (Science Advances 2017)
showed that in clean elections the mean turnout and the winner's share of small stations converge
to those of large stations as size grows (only the spread widens), while rigged elections show
"the smaller, the more extreme" — small stations systematically above the large ones on both.

Israel's small stations are mostly in small localities, so the comparison is made *within* the
locality: every small station (electorate < `small_max`) is set against the pooled normal-size
stations (≥ `large_min`) of its own locality (the CEC keeps stations at roughly 400–800 voters, so
stations under 300 are the bottom 1–2 % and a reference of ≥ 350 is "everyone else"), for turnout and for the share of the locality's own leading
list (the list with most votes at the large stations, so that Arab and Haredi localities are judged
by their own winner). Station z's are binomial; only the excess side is scored. At election level
the test reports the within-locality slope of turnout and leader share on log electorate (points
per doubling) with its t-statistic — the quantity the calibration compares across elections.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features


def _within_slope(y: np.ndarray, x: np.ndarray, group: np.ndarray) -> tuple[float, float, int]:
    """OLS slope of y on x after removing group means (locality fixed effects); returns (slope, se, n)."""
    df = pd.DataFrame({"y": y, "x": x, "g": group}).dropna()
    if len(df) < 10:
        return float("nan"), float("nan"), len(df)
    yd = df["y"] - df.groupby("g")["y"].transform("mean")
    xd = df["x"] - df.groupby("g")["x"].transform("mean")
    sxx = float((xd**2).sum())
    if sxx <= 0:
        return float("nan"), float("nan"), len(df)
    b = float((xd * yd).sum() / sxx)
    resid = yd - b * xd
    dof = len(df) - df["g"].nunique() - 1
    se = float(np.sqrt((resid**2).sum() / max(dof, 1) / sxx))
    return b, se, len(df)


@register("T13")
def t13_small_stations(feat: Features, small_max: int = 300, large_min: int = 350, min_large: int = 3,
                       min_eligible: int = 20) -> TestResult:
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & (st["valid"] > 0) & (st["voters"] > 0) & (st["turnout"].fillna(2) <= 1)
    s = st[ok].copy()
    e = s["eligible"].to_numpy(dtype=float)
    s["is_small"] = e < small_max
    s["is_large"] = e >= large_min
    major = [p for p in feat.major if f"v_{p}" in s.columns]
    vcols = [f"v_{p}" for p in major]

    large = s[s["is_large"]]
    agg = large.groupby("locality_code").agg(n_large=("station", "size"), e_l=("eligible", "sum"), v_l=("voters", "sum"),
                                              va_l=("valid", "sum"), **{c: (c, "sum") for c in vcols}).reset_index()
    agg = agg[agg["n_large"] >= min_large].copy()
    if agg.empty or not major:
        return TestResult("T13", "Small vs large stations", feat.knesset, "station", pd.DataFrame(),
                          {"skipped": "no locality with enough large stations"})
    lead_idx = agg[vcols].to_numpy(dtype=float).argmax(axis=1)
    agg["loc_leader"] = [major[i] for i in lead_idx]
    agg["lead_l"] = agg[vcols].to_numpy(dtype=float)[np.arange(len(agg)), lead_idx]
    agg["q_turnout"] = agg["v_l"] / agg["e_l"]
    agg["q_leader"] = agg["lead_l"] / agg["va_l"]
    ref = agg[["locality_code", "n_large", "loc_leader", "q_turnout", "q_leader"]]

    sm = s[s["is_small"]].merge(ref, on="locality_code", how="inner")
    e_s = sm["eligible"].to_numpy(dtype=float)
    v_s = sm["voters"].to_numpy(dtype=float)
    va_s = sm["valid"].to_numpy(dtype=float)
    lead_s = np.array([sm.at[i, f"v_{p}"] for i, p in zip(sm.index, sm["loc_leader"], strict=True)], dtype=float) if len(sm) else np.array([])
    qt = sm["q_turnout"].to_numpy(dtype=float)
    ql = sm["q_leader"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z_t = (v_s - e_s * qt) / np.sqrt(e_s * qt * (1 - qt))
        z_l = (lead_s - va_s * ql) / np.sqrt(va_s * ql * (1 - ql))
    units = pd.DataFrame({
        "knesset": sm["knesset"].to_numpy(), "locality_code": sm["locality_code"].to_numpy(),
        "locality_name": sm["locality_name"].to_numpy(), "station": sm["station"].to_numpy(), "sector": sm["sector"].to_numpy(),
        "eligible": e_s.astype(int), "voters": v_s.astype(int), "valid": va_s.astype(int), "n_large": sm["n_large"].to_numpy(),
        "loc_leader": sm["loc_leader"].to_numpy(), "turnout": v_s / e_s, "large_turnout": qt, "d_turnout": v_s / e_s - qt, "z_turnout": z_t,
        "leader_share": lead_s / va_s, "large_leader_share": ql, "d_leader": lead_s / va_s - ql, "z_leader": z_l,
    })
    units["score_turnout"] = units["z_turnout"].clip(lower=0)
    units["score_leader"] = units["z_leader"].clip(lower=0)
    units["max_score"] = units[["score_turnout", "score_leader"]].max(axis=1)
    units = units.sort_values("max_score", ascending=False).reset_index(drop=True)

    # locality level: pooled small stations against the large ones
    loc_rows = []
    for code, d in units.groupby("locality_code"):
        es, vs, vas = d["eligible"].sum(), d["voters"].sum(), d["valid"].sum()
        qt0, ql0 = float(d["large_turnout"].iloc[0]), float(d["large_leader_share"].iloc[0])
        ls = float((d["leader_share"] * d["valid"]).sum())
        zt = (vs - es * qt0) / np.sqrt(es * qt0 * (1 - qt0)) if 0 < qt0 < 1 else np.nan
        zl = (ls - vas * ql0) / np.sqrt(vas * ql0 * (1 - ql0)) if 0 < ql0 < 1 else np.nan
        loc_rows.append({"knesset": feat.knesset, "locality_code": code, "locality_name": d["locality_name"].iloc[0], "sector": d["sector"].iloc[0],
                         "n_small": len(d), "n_large": int(d["n_large"].iloc[0]), "loc_leader": d["loc_leader"].iloc[0],
                         "d_turnout_pooled": vs / es - qt0, "z_turnout_pooled": zt, "d_leader_pooled": ls / vas - ql0, "z_leader_pooled": zl})
    localities = pd.DataFrame(loc_rows)

    # election level: within-locality slopes on log electorate, all stations of localities with a reference
    allst = s.merge(ref, on="locality_code", how="inner")
    lead_all = np.array([allst.at[i, f"v_{p}"] for i, p in zip(allst.index, allst["loc_leader"], strict=True)], dtype=float) if len(allst) else np.array([])
    x = np.log2(allst["eligible"].to_numpy(dtype=float))
    g = allst["locality_code"].to_numpy()
    bt, set_, n_t = _within_slope(allst["turnout"].to_numpy(dtype=float), x, g)
    bl, sel, _n_l = _within_slope(lead_all / allst["valid"].to_numpy(dtype=float), x, g)
    by_sector: dict[str, Any] = {}
    for sec, d in allst.groupby("sector"):
        xs = np.log2(d["eligible"].to_numpy(dtype=float))
        b1, s1, n1 = _within_slope(d["turnout"].to_numpy(dtype=float), xs, d["locality_code"].to_numpy())
        by_sector[str(sec)] = {"n": n1, "slope_turnout_pp_per_doubling": 100 * b1, "t_turnout": b1 / s1 if s1 and s1 > 0 else float("nan"),
                               "n_small": int((d["eligible"] < small_max).sum())}
    summary = {
        "small_max": small_max, "large_min": large_min,
        "n_small_scored": len(units), "n_localities": int(units["locality_code"].nunique()) if len(units) else 0,
        "mean_d_turnout_small": float(units["d_turnout"].mean()) if len(units) else float("nan"),
        "mean_d_leader_small": float(units["d_leader"].mean()) if len(units) else float("nan"),
        "n_z_turnout_above_4": int((units["z_turnout"] > 4).sum()), "n_z_leader_above_4": int((units["z_leader"] > 4).sum()),
        "n_both_above_3": int(((units["z_turnout"] > 3) & (units["z_leader"] > 3)).sum()),
        "slope_turnout_pp_per_doubling": 100 * bt, "slope_turnout_se": 100 * set_, "t_turnout": bt / set_ if set_ and set_ > 0 else float("nan"),
        "slope_leader_pp_per_doubling": 100 * bl, "slope_leader_se": 100 * sel, "t_leader": bl / sel if sel and sel > 0 else float("nan"),
        "n_slope": n_t, "by_sector": by_sector,
    }
    params = {"small_max": small_max, "large_min": large_min, "min_large": min_large, "min_eligible": min_eligible}
    return TestResult("T13", "Small vs large stations", feat.knesset, "station", units, summary, params, extra={"localities": localities})
