"""T10 — a list at zero where its peers give it many votes (ballot-slip removal signature).

In Israel voters pick a printed slip of their list inside the booth. If a list's slips are removed
or hidden, the list collapses to zero at that station while the neighbouring stations of the same
site or locality give it its usual share. For every station and every major list we compute the
expected votes from the leave-one-out peer share and the binomial probability of observing at most
the recorded count. Only stations with a meaningful expectation (`min_expected`) are listed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features


def _loo_share(x: np.ndarray, n: np.ndarray, group: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    df = pd.DataFrame({"x": x, "n": n, "g": group})
    sx = df.groupby("g")["x"].transform("sum").to_numpy(dtype=float)
    sn = df.groupby("g")["n"].transform("sum").to_numpy(dtype=float)
    cnt = df.groupby("g")["x"].transform("size").to_numpy()
    px, pn = sx - x, sn - n
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where(pn > 0, px / pn, np.nan)
    return q, cnt - 1


@register("T10")
def t10_zero_peers(feat: Features, min_expected: float = 10.0, max_votes: int = 2, min_peers_site: int = 2) -> TestResult:
    st = feat.stations
    ok = (st["valid"] > 0) & st["locality_code"].notna()
    s = st[ok]
    va = s["valid"].to_numpy(dtype=float)
    loc_key = s["locality_code"].astype(str).to_numpy()
    site_key = s["site_key"].astype(str).where(s["site_key"].notna(), None).to_numpy()
    rows: list[pd.DataFrame] = []
    for p in feat.major:
        x = s[f"v_{p}"].to_numpy(dtype=float)
        q_loc, n_loc = _loo_share(x, va, loc_key)
        if s["site_key"].notna().any():
            q_site, n_site = _loo_share(x, va, np.where(site_key == None, "none:" + loc_key, site_key))  # noqa: E711
            use_site = (n_site >= min_peers_site) & ~np.isnan(q_site)
        else:
            q_site, use_site = np.full_like(q_loc, np.nan), np.zeros(len(s), dtype=bool)
        q = np.where(use_site, q_site, q_loc)
        expected = va * q
        cand = (expected >= min_expected) & (x <= max_votes)
        if not cand.any():
            continue
        pv = stats.binom.cdf(x[cand], va[cand].astype(int), np.clip(q[cand], 0, 1))
        rows.append(pd.DataFrame({
            "knesset": s["knesset"].to_numpy()[cand], "locality_code": s["locality_code"].to_numpy()[cand],
            "locality_name": s["locality_name"].to_numpy()[cand], "station": s["station"].to_numpy()[cand],
            "sector": s["sector"].to_numpy()[cand], "party": p, "votes": x[cand].astype(int), "valid": va[cand].astype(int),
            "peer_share": q[cand], "expected": expected[cand], "p_binom": pv,
            "peer_level": np.where(use_site[cand], "site", "locality"),
            "n_peers": np.where(use_site[cand], n_site[cand] if s["site_key"].notna().any() else 0, n_loc[cand]),
        }))
    units = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["knesset", "locality_code", "locality_name", "station", "sector", "party", "votes", "valid",
                 "peer_share", "expected", "p_binom", "peer_level", "n_peers"])
    units = units.sort_values("p_binom").reset_index(drop=True)
    summary = {
        "n_candidates": len(units),
        "n_stations": int(units[["locality_code", "station"]].drop_duplicates().shape[0]) if len(units) else 0,
        "by_party": units.groupby("party").size().to_dict() if len(units) else {},
        "by_sector": units.groupby("sector").size().to_dict() if len(units) else {},
        "n_p_below_1e-6": int((units["p_binom"] < 1e-6).sum()) if len(units) else 0,
    }
    return TestResult("T10", "Zero votes against peers", feat.knesset, "station_party", units, summary,
                      {"min_expected": min_expected, "max_votes": max_votes, "min_peers_site": min_peers_site})
