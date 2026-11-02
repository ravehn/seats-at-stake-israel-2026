"""T11 — invalid ballots against building peers and own history (Leemann & Bochsler 2014).

Rejected envelopes are a lever. A committee that wants to shave a list's result can invalidate
envelopes selectively; a committee that fabricates a protocol tends to forget that real stations
spoil a percent or two. The station's invalid share (invalid / voters) is compared (a) with the
pooled share of the other stations in the same building — leave-one-out binomial z, as in T09 —
and (b) with the station's own previous election, net of the locality's change, as a robust z
within the sector, as in T12. Only the excess side feeds the tiers: an unusually clean station is
kept in the table but not scored. Where the file has no site key the peer group is the locality.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.battery.t09_siblings import _loo_z
from electionforensics.battery.t12_history_shift import _robust_z
from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import load_election
from electionforensics.ingest.registry import ELECTIONS
from electionforensics.model.features import Features, build_features
from electionforensics.model.history import link


def _history_z(feat: Features, prev: Features | None) -> pd.DataFrame | None:
    """Per (locality_code, station_num): change of invalid share net of the locality, as a robust z within the sector."""
    k = feat.knesset
    if prev is None:
        if (k - 1) not in ELECTIONS:
            return None
        try:
            prev = build_features(load_election(k - 1), load_cbs())
        except FileNotFoundError:
            return None
    m = link(prev, feat)
    m = m[m["good_match"] & m["invalid_share"].notna() & m["invalid_share_prev"].notna()].copy()
    if m.empty:
        return None
    m["d_invalid_res"] = m["d_invalid_share"] - m.groupby("locality_code")["d_invalid_share"].transform("mean")
    m["z_history"] = _robust_z(m["d_invalid_res"], m["sector"])
    return m[["locality_code", "station_num", "invalid_share_prev", "d_invalid_share", "d_invalid_res", "z_history"]]


@register("T11")
def t11_invalid(feat: Features, prev: Features | None = None, min_group: int = 3, min_voters: int = 50) -> TestResult:
    st = feat.stations
    has_site = st["site_key"].notna().any()
    peer_level = "site" if has_site else "locality"
    key = st["site_key"] if has_site else st["locality_code"].astype(str)
    ok = (st["voters"].fillna(0) >= min_voters) & st["invalid"].notna() & key.notna()
    s = st[ok].copy()
    g = key[ok].to_numpy()
    v = s["voters"].to_numpy(dtype=float)
    inv = s["invalid"].to_numpy(dtype=float)
    z_p, q_p, n_peers = _loo_z(inv, v, g)

    units = pd.DataFrame({
        "knesset": s["knesset"].to_numpy(), "locality_code": s["locality_code"].to_numpy(),
        "locality_name": s["locality_name"].to_numpy(), "station": s["station"].to_numpy(),
        "station_num": s["station_num"].to_numpy(), "sector": s["sector"].to_numpy(),
        "peer_key": g, "peer_level": peer_level, "n_peers": n_peers,
        "voters": v.astype(int), "invalid": inv.astype(int), "invalid_share": inv / v,
        "peer_invalid_share": q_p, "z_peers": z_p,
    })
    units = units[units["n_peers"] >= (min_group - 1)].reset_index(drop=True)

    hist = _history_z(feat, prev)
    if hist is not None:
        units = units.merge(hist, on=["locality_code", "station_num"], how="left")
    else:
        for c in ("invalid_share_prev", "d_invalid_share", "d_invalid_res", "z_history"):
            units[c] = np.nan
    units["score_peers"] = units["z_peers"].clip(lower=0)
    units["score_history"] = units["z_history"].clip(lower=0)
    units = units.drop(columns=["station_num"])

    zp = units["z_peers"]
    zh = units["z_history"].dropna()
    by_sector = {str(sec): {"n": len(d), "invalid_share": float(d["invalid"].sum() / d["voters"].sum()),
                            "z_peers_above_4": int((d["z_peers"] > 4).sum())} for sec, d in units.groupby("sector")}
    summary = {
        "peer_level": peer_level, "n_stations": len(units),
        "invalid_share_national": float(st["invalid"].sum() / st["voters"].sum()) if st["voters"].sum() else float("nan"),
        "z_peers_quantiles": {q: float(zp.quantile(q)) for q in (0.5, 0.9, 0.99, 0.999)},
        "n_z_peers_above_4": int((zp > 4).sum()), "n_z_peers_above_6": int((zp > 6).sum()), "n_z_peers_above_8": int((zp > 8).sum()),
        "n_z_peers_below_-4": int((zp < -4).sum()),
        "n_with_history": len(zh), "n_z_history_above_4": int((zh > 4).sum()), "n_z_history_above_6": int((zh > 6).sum()),
        "share_above_10pct": int((units["invalid_share"] > 0.10).sum()),
        "by_sector": by_sector,
    }
    return TestResult("T11", "Invalid ballots vs peers and history", feat.knesset, "station", units, summary,
                      {"min_group": min_group, "min_voters": min_voters})
