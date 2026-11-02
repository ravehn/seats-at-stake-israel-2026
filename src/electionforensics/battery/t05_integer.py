"""T05 — integer percentages (Kobak, Shpilkin & Pshenichnikov 2016).

Hand-written results tend to land on round percentages: a station reporting exactly 70.0 % turnout
or exactly 50.0 % for one list more often than arithmetic allows. We count stations whose turnout
(voters / eligible) and leader share (leader / valid) fall within ±`window` percentage points of an
integer, and compare with a Monte-Carlo null in which every station's own counts are smeared with
binomial noise (voters ~ Bin(eligible, turnout); leader ~ Bin(valid', share)). Israel's stations are
small (≤ 900 voters), so chance hits are frequent; only the excess over the null is informative.
Exclusions follow the paper: eligible < `min_eligible`, turnout > `max_turnout`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features


def _near_integer(x: np.ndarray, window: float) -> np.ndarray:
    return np.abs(x - np.rint(x)) < window


@register("T05")
def t05_integer(
    feat: Features,
    n_draws: int = 2000,
    window: float = 0.05,
    min_eligible: int = 100,
    max_turnout: float = 0.99,
    seed: int = 0,
    chunk: int = 200,
) -> TestResult:
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & (st["turnout"].fillna(2) <= max_turnout) & (st["valid"] > 0) & (st["voters"] > 0)
    s = st[ok]
    e = s["eligible"].to_numpy(dtype=np.int64)
    v = s["voters"].to_numpy(dtype=np.int64)
    va = s["valid"].to_numpy(dtype=np.int64)
    lead = s[f"v_{feat.leader}"].to_numpy(dtype=np.int64)

    turnout_pct = 100.0 * v / e
    leader_pct = 100.0 * lead / va
    hit_t = _near_integer(turnout_pct, window)
    hit_l = _near_integer(leader_pct, window)
    obs_t, obs_l, obs_both = int(hit_t.sum()), int(hit_l.sum()), int((hit_t & hit_l).sum())

    rng = np.random.default_rng(seed)
    p_t = v / e
    p_l = lead / va
    valid_frac = va / v
    draws_t = np.empty(n_draws, dtype=np.int64)
    draws_l = np.empty(n_draws, dtype=np.int64)
    draws_b = np.empty(n_draws, dtype=np.int64)
    for start in range(0, n_draws, chunk):
        m = min(chunk, n_draws - start)
        v2 = rng.binomial(e, p_t, size=(m, e.size))
        va2 = np.maximum(np.rint(v2 * valid_frac).astype(np.int64), 1)
        l2 = rng.binomial(va2, np.clip(p_l, 0, 1))
        ht = _near_integer(100.0 * v2 / e, window)
        hl = _near_integer(100.0 * l2 / va2, window)
        draws_t[start:start + m] = ht.sum(axis=1)
        draws_l[start:start + m] = hl.sum(axis=1)
        draws_b[start:start + m] = (ht & hl).sum(axis=1)

    def stats(obs: int, draws: np.ndarray) -> dict[str, float]:
        mu, sd = float(draws.mean()), float(draws.std(ddof=1))
        return {
            "observed": obs, "expected": mu, "sd": sd,
            "z": (obs - mu) / sd if sd > 0 else float("nan"),
            "p_mc": float((np.sum(draws >= obs) + 1) / (draws.size + 1)),
            "excess": obs - mu,
        }

    summary = {
        "n_stations_tested": int(ok.sum()),
        "turnout": stats(obs_t, draws_t),
        "leader_share": stats(obs_l, draws_l),
        "both": stats(obs_both, draws_b),
        "leader": feat.leader,
    }
    # stratified observed/expected by sector (expected approximated by the per-station hit frequency under the null)
    by_sector: dict[str, dict[str, float]] = {}
    for sec, idx in s.groupby("sector").indices.items():
        by_sector[str(sec)] = {"n": len(idx), "turnout_hits": int(hit_t[idx].sum()), "leader_hits": int(hit_l[idx].sum())}
    summary["by_sector"] = by_sector

    units = pd.DataFrame({
        "knesset": s["knesset"].to_numpy(), "locality_code": s["locality_code"].to_numpy(),
        "locality_name": s["locality_name"].to_numpy(), "station": s["station"].to_numpy(), "sector": s["sector"].to_numpy(),
        "eligible": e, "voters": v, "valid": va, "leader_votes": lead,
        "turnout_pct": turnout_pct, "leader_pct": leader_pct, "hit_turnout": hit_t, "hit_leader": hit_l,
    })
    params = {"n_draws": n_draws, "window": window, "min_eligible": min_eligible, "max_turnout": max_turnout, "seed": seed}
    return TestResult("T05", "Integer percentages", feat.knesset, "station", units, summary, params)
