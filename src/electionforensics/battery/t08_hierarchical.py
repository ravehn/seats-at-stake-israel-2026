"""T08 — hierarchical residuals: a station against what its locality, its size and its own history predict.

T09 compares a station with the other boxes in its building and T12 with its own past; T08 puts the
pieces into one model and scores every station, including those without building peers or without
a past. For turnout and for the share of the locality's own leading list, within each sector:

    y_i = α_ℓ(i) + β · log₂(electorate_i) + γ · h_i + ε_i

where α_ℓ is a locality effect, h_i the station's deviation from its locality in the previous
election (zero with a missing indicator where no comparable station exists), β the size gradient
and γ the persistence of a station's idiosyncrasy. β and γ come from a within-locality (fixed
effects) regression; the locality effect is the leave-one-out mean of the other stations' partial
residuals, shrunk towards the sector mean with the empirical-Bayes weight n/(n + κ), κ = within /
between variance (Zhang 2019-style two-level model without the MCMC). The residual is standardised
by a robust sector scale; the station's score is |z| for turnout and for the leader share. Read
against the 2013–2022 baseline; the model's own variance decomposition and γ are reported so that a
reader sees how much of a station's oddity is simply persistent.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import load_election
from electionforensics.ingest.registry import ELECTIONS
from electionforensics.model.features import Features, build_features
from electionforensics.model.history import collapse_split_boxes, link

MAD_FLOOR = 0.01


def _loo_mean(x: pd.Series, by: pd.Series) -> tuple[pd.Series, pd.Series]:
    s = x.groupby(by).transform("sum")
    n = x.groupby(by).transform("count")
    return (s - x) / (n - 1).where(n > 1), n - 1


def _within_ols(y: pd.Series, design: pd.DataFrame, by: pd.Series) -> np.ndarray:
    """OLS of y on the design after removing the group means of both (locality fixed effects)."""
    yd = (y - y.groupby(by).transform("mean")).to_numpy(dtype=float)
    xd = (design - design.groupby(by).transform("mean")).to_numpy(dtype=float)
    ok = np.isfinite(yd) & np.isfinite(xd).all(axis=1)
    if ok.sum() < design.shape[1] + 10:
        return np.zeros(design.shape[1])
    beta, *_ = np.linalg.lstsq(xd[ok], yd[ok], rcond=None)
    return np.asarray(beta, dtype=float)


def hierarchical_residuals(y: pd.Series, loc: pd.Series, log_size: pd.Series, h: pd.Series) -> dict[str, Any]:
    """Residual z of y given locality, size and history; returns z, components and fitted coefficients."""
    miss = h.isna().astype(float)
    design = pd.DataFrame({"log_size": log_size, "h": h.fillna(0.0), "miss": miss})
    beta = _within_ols(y, design, loc)
    partial = y - design.to_numpy(dtype=float) @ beta
    loo, n_other = _loo_mean(partial, loc)
    sector_mean = float(partial.mean())
    # variance components: within from the LOO residual, between from locality means
    within = float(((partial - loo).dropna() ** 2).mean()) if loo.notna().any() else float("nan")
    loc_means = partial.groupby(loc).mean()
    between = max(float(loc_means.var()) - within / max(float(loc.value_counts().mean()), 1.0), 1e-6) if len(loc_means) > 1 else 1e-6
    kappa = within / between if between > 0 and np.isfinite(within) else 1.0
    w = n_other / (n_other + kappa)
    alpha = w * loo.fillna(sector_mean) + (1 - w) * sector_mean
    alpha = alpha.where(n_other > 0, sector_mean)
    resid = partial - alpha
    med = float(resid.median())
    mad = max(float((resid - med).abs().median()) * 1.4826, MAD_FLOOR)
    z = (resid - med) / mad
    return {"z": z, "resid": resid, "alpha": alpha, "beta_size": float(beta[0]), "gamma_history": float(beta[1]),
            "within_sd": float(np.sqrt(within)) if np.isfinite(within) else float("nan"), "between_sd": float(np.sqrt(between)),
            "kappa": float(kappa), "scale": mad}


def _history(feat: Features, prev: Features | None) -> pd.DataFrame | None:
    """Previous-election deviation of each station (by station number) from its locality: turnout and the locality leader's share."""
    k = feat.knesset
    if prev is None:
        if (k - 1) not in ELECTIONS:
            return None
        try:
            prev = build_features(load_election(k - 1), load_cbs())
        except FileNotFoundError:
            return None
    m = link(prev, feat)
    m = m[m["good_match"]]
    if m.empty:
        return None
    pc = collapse_split_boxes(prev)
    pc["h_turnout"] = pc["turnout"] - pc.groupby("locality_code")["turnout"].transform("mean")
    out = m[["locality_code", "station_num"]].merge(pc[["locality_code", "station_num", "h_turnout"]], on=["locality_code", "station_num"], how="left")
    # leader share history is filled by the caller per locality leader (needs the current leader); keep prev shares
    scols = [c for c in pc.columns if c.startswith("s_")]
    out = out.merge(pc[["locality_code", "station_num", *scols]].rename(columns={c: f"prev_{c}" for c in scols}),
                    on=["locality_code", "station_num"], how="left")
    return out


@register("T08")
def t08_hierarchical(feat: Features, prev: Features | None = None, min_eligible: int = 50, min_locality: int = 3) -> TestResult:
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & (st["valid"] > 0) & (st["voters"] > 0) & (st["turnout"].fillna(2) <= 1)
    s = st[ok].copy()
    cnt = s.groupby("locality_code")["station"].transform("size")
    s = s[cnt >= min_locality].copy()
    if s.empty:
        return TestResult("T08", "Hierarchical residuals", feat.knesset, "station", pd.DataFrame(), {"skipped": "no localities with enough stations"})
    major = [p for p in feat.major if f"v_{p}" in s.columns]
    tot = s.groupby("locality_code")[[f"v_{p}" for p in major]].sum()
    loc_leader = pd.Series([major[i] for i in tot.to_numpy(dtype=float).argmax(axis=1)], index=tot.index)
    s["loc_leader"] = s["locality_code"].map(loc_leader)
    share_mat = s[[f"s_{p}" for p in major]].astype(float).to_numpy()
    lead_idx = np.array([major.index(p) for p in s["loc_leader"]], dtype=int)
    s["leader_share_loc"] = share_mat[np.arange(len(s)), lead_idx]
    s["log_size"] = np.log2(s["eligible"].astype(float))

    hist = _history(feat, prev)
    s["h_turnout"] = np.nan
    s["h_leader"] = np.nan
    if hist is not None:
        hm = s[["locality_code", "station_num"]].merge(hist, on=["locality_code", "station_num"], how="left")
        s["h_turnout"] = hm["h_turnout"].to_numpy()
        prev_mat = np.column_stack([hm[f"prev_s_{p}"].astype(float).to_numpy() if f"prev_s_{p}" in hm.columns else np.full(len(hm), np.nan)
                                    for p in major])
        s["prev_leader_share"] = prev_mat[np.arange(len(hm)), lead_idx]
        s["h_leader"] = s["prev_leader_share"] - s.groupby("locality_code")["prev_leader_share"].transform("mean")

    units = s[["knesset", "locality_code", "locality_name", "station", "sector", "eligible", "turnout", "leader_share_loc", "loc_leader",
               "h_turnout", "h_leader"]].copy()
    units["turnout"] = units["turnout"].astype(float)
    units["z_turnout"] = np.nan
    units["z_leader"] = np.nan
    units["alpha_turnout"] = np.nan
    units["alpha_leader"] = np.nan
    comps: dict[str, Any] = {}
    for sec, idx in s.groupby("sector").indices.items():
        d = s.iloc[idx]
        if len(d) < 30:
            continue
        rt = hierarchical_residuals(d["turnout"].astype(float), d["locality_code"], d["log_size"], d["h_turnout"])
        rl = hierarchical_residuals(d["leader_share_loc"].astype(float), d["locality_code"], d["log_size"], d["h_leader"])
        units.loc[d.index, "z_turnout"] = rt["z"].to_numpy()
        units.loc[d.index, "z_leader"] = rl["z"].to_numpy()
        units.loc[d.index, "alpha_turnout"] = rt["alpha"].to_numpy()
        units.loc[d.index, "alpha_leader"] = rl["alpha"].to_numpy()
        comps[str(sec)] = {"n": len(d), "turnout": {k2: rt[k2] for k2 in ("beta_size", "gamma_history", "within_sd", "between_sd", "kappa", "scale")},
                           "leader": {k2: rl[k2] for k2 in ("beta_size", "gamma_history", "within_sd", "between_sd", "kappa", "scale")}}
    units["score_turnout"] = units["z_turnout"].abs()
    units["score_leader"] = units["z_leader"].abs()
    units["max_abs_z"] = units[["score_turnout", "score_leader"]].max(axis=1)
    units = units.dropna(subset=["max_abs_z"]).sort_values("max_abs_z", ascending=False).reset_index(drop=True)
    mz = units["max_abs_z"]
    summary = {
        "n_stations": len(units), "n_with_history": int(units["h_turnout"].notna().sum()),
        "max_abs_z_quantiles": {q: float(mz.quantile(q)) for q in (0.5, 0.9, 0.99, 0.999)} if len(mz) else {},
        "n_above_4": int((mz > 4).sum()), "n_above_6": int((mz > 6).sum()), "n_above_8": int((mz > 8).sum()),
        "n_both_above_3": int(((units["z_turnout"] > 3) & (units["z_leader"] > 3)).sum()),
        "components": comps,
    }
    return TestResult("T08", "Hierarchical residuals", feat.knesset, "station", units, summary,
                      {"min_eligible": min_eligible, "min_locality": min_locality})
