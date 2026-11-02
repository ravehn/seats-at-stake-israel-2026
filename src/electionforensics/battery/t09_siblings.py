"""T09 — sibling stations in the same building (Cantú 2014; CEC's own 2019 checks).

Stations that share a polling site (`ריכוז` within a locality) draw voters from the same
neighbourhood, so their turnout and party shares should differ only by sampling noise plus the
real street-to-street variation. For each station we pool the other stations of its site
(leave-one-out) and compute binomial z-scores for turnout and for every major list. The nominal
binomial scale understates real heterogeneity; the calibration layer rescales z by the empirical
spread observed in 2019–2022. Where the file has no site key (before the 22nd Knesset) the peer
group is the locality and the result is labelled accordingly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features


def _loo_z(x: np.ndarray, n: np.ndarray, group: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Leave-one-out binomial z of successes x out of n against the pooled rate of the other group members."""
    df = pd.DataFrame({"x": x, "n": n, "g": group})
    sx = df.groupby("g")["x"].transform("sum").to_numpy(dtype=float)
    sn = df.groupby("g")["n"].transform("sum").to_numpy(dtype=float)
    cnt = df.groupby("g")["x"].transform("size").to_numpy()
    px, pn = sx - x, sn - n
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where(pn > 0, px / pn, np.nan)
        var = n * q * (1 - q)
        z = np.where(var > 0, (x - n * q) / np.sqrt(var), np.nan)
    z = np.where(cnt >= 2, z, np.nan)
    return z, q, cnt - 1


@register("T09")
def t09_siblings(feat: Features, min_group: int = 3, min_eligible: int = 50) -> TestResult:
    st = feat.stations
    has_site = st["site_key"].notna().any()
    peer_level = "site" if has_site else "locality"
    key = st["site_key"] if has_site else st["locality_code"].astype(str)
    ok = (st["eligible"].fillna(0) >= min_eligible) & (st["valid"] > 0) & key.notna()
    s = st[ok].copy()
    g = key[ok].to_numpy()

    e = s["eligible"].to_numpy(dtype=float)
    v = s["voters"].to_numpy(dtype=float)
    va = s["valid"].to_numpy(dtype=float)
    z_t, q_t, n_peers = _loo_z(v, e, g)

    units = pd.DataFrame({
        "knesset": s["knesset"].to_numpy(), "locality_code": s["locality_code"].to_numpy(),
        "locality_name": s["locality_name"].to_numpy(), "station": s["station"].to_numpy(),
        "sector": s["sector"].to_numpy(), "peer_key": g, "peer_level": peer_level, "n_peers": n_peers,
        "eligible": e.astype(int), "voters": v.astype(int), "valid": va.astype(int),
        "turnout": v / e, "peer_turnout": q_t, "z_turnout": z_t,
    })
    zcols = ["z_turnout"]
    for p in feat.major:
        zp, qp, _ = _loo_z(s[f"v_{p}"].to_numpy(dtype=float), va, g)
        units[f"share_{p}"] = s[f"v_{p}"].to_numpy(dtype=float) / va
        units[f"peer_share_{p}"] = qp
        units[f"z_{p}"] = zp
        zcols.append(f"z_{p}")
    zmat = units[zcols].to_numpy(dtype=float)
    absz = np.abs(np.nan_to_num(zmat, nan=0.0))
    units["max_abs_z"] = absz.max(axis=1)
    arg = absz.argmax(axis=1)
    units["top_dimension"] = [zcols[i][2:] for i in arg]
    units["top_z"] = zmat[np.arange(len(units)), arg]
    units = units[units["n_peers"] >= (min_group - 1)].reset_index(drop=True)

    # site-level heterogeneity of turnout: chi-square across the site's stations
    grp = units.groupby("peer_key")
    chi = []
    for k, d in grp:
        pg = d["voters"].sum() / d["eligible"].sum()
        if 0 < pg < 1 and len(d) >= min_group:
            x = ((d["voters"] - d["eligible"] * pg) ** 2 / (d["eligible"] * pg * (1 - pg))).sum()
            chi.append((k, len(d), float(x), float(stats.chi2.sf(x, len(d) - 1))))
    sites = pd.DataFrame(chi, columns=["peer_key", "n_stations", "chi2_turnout", "p_chi2"])

    mz = units["max_abs_z"]
    summary = {
        "peer_level": peer_level, "n_stations": len(units), "n_sites": int(units["peer_key"].nunique()),
        "max_abs_z_quantiles": {q: float(mz.quantile(q)) for q in (0.5, 0.9, 0.99, 0.999)},
        "n_above_4": int((mz > 4).sum()), "n_above_6": int((mz > 6).sum()), "n_above_8": int((mz > 8).sum()),
        "sites_p_chi2_below_1e-6": int((sites["p_chi2"] < 1e-6).sum()) if len(sites) else 0,
    }
    return TestResult("T09", "Sibling stations (same site)", feat.knesset, "station", units, summary,
                      {"min_group": min_group, "min_eligible": min_eligible}, extra={"sites": sites})
