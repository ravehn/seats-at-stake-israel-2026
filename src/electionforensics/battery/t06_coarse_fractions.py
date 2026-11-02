"""T06 — coarse vote-share fractions (Rozenas 2017).

A hand that fixes a protocol tends to aim at a round target: half the valid votes, two thirds,
three quarters, 60 %. Rozenas (Political Analysis 2017) detects this as excess density of vote
shares at fractions with small denominators, against a null that keeps every station's own counts
and only redraws them binomially — which matters in Israel, where valid counts of a few hundred
make exact coarse fractions common by arithmetic alone. We count stations whose turnout and whose
leader share fall within ±`window` of a fraction k/d with d ≤ `max_denominator` and compare the
count with the binomial Monte-Carlo null, overall, by sector and by fraction. T05 asks the same
question for every integer percentage; T06 concentrates the prior on the few fractions a person
would actually aim at, so it has power where a forger used targets like 2/3 that T05 dilutes.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features


def coarse_fractions(max_denominator: int = 10) -> np.ndarray:
    fr = sorted({Fraction(k, d) for d in range(2, max_denominator + 1) for k in range(1, d)})
    return np.array([float(f) for f in fr])


def _nearest_hit(x: np.ndarray, fr: np.ndarray, window: float) -> tuple[np.ndarray, np.ndarray]:
    """For each x: is it within `window` of a coarse fraction, and which (index into fr, −1 if none)."""
    j = np.clip(np.searchsorted(fr, x), 1, fr.size - 1)
    left, right = fr[j - 1], fr[j]
    use_right = np.abs(right - x) < np.abs(x - left)
    idx = np.where(use_right, j, j - 1)
    dist = np.abs(fr[idx] - x)
    hit = dist < window
    return hit, np.where(hit, idx, -1)


@register("T06")
def t06_coarse_fractions(
    feat: Features,
    n_draws: int = 1000,
    window: float = 0.0005,
    max_denominator: int = 10,
    min_eligible: int = 100,
    max_turnout: float = 0.99,
    seed: int = 0,
    chunk: int = 200,
) -> TestResult:
    fr = coarse_fractions(max_denominator)
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & (st["turnout"].fillna(2) <= max_turnout) & (st["valid"] > 0) & (st["voters"] > 0)
    s = st[ok]
    e = s["eligible"].to_numpy(dtype=np.int64)
    v = s["voters"].to_numpy(dtype=np.int64)
    va = s["valid"].to_numpy(dtype=np.int64)
    lead = s[f"v_{feat.leader}"].to_numpy(dtype=np.int64)

    hit_t, idx_t = _nearest_hit(v / e, fr, window)
    hit_l, idx_l = _nearest_hit(lead / va, fr, window)
    obs_t, obs_l = int(hit_t.sum()), int(hit_l.sum())
    obs_by_fr_t = np.bincount(idx_t[idx_t >= 0], minlength=fr.size)
    obs_by_fr_l = np.bincount(idx_l[idx_l >= 0], minlength=fr.size)

    rng = np.random.default_rng(seed)
    p_t, p_l, valid_frac = v / e, np.clip(lead / va, 0, 1), va / v
    draws_t = np.empty(n_draws, dtype=np.int64)
    draws_l = np.empty(n_draws, dtype=np.int64)
    exp_by_fr_t = np.zeros(fr.size)
    exp_by_fr_l = np.zeros(fr.size)
    for start in range(0, n_draws, chunk):
        m = min(chunk, n_draws - start)
        v2 = rng.binomial(e, p_t, size=(m, e.size))
        va2 = np.maximum(np.rint(v2 * valid_frac).astype(np.int64), 1)
        l2 = rng.binomial(va2, p_l)
        ht, it = _nearest_hit((v2 / e).ravel(), fr, window)
        hl, il = _nearest_hit((l2 / va2).ravel(), fr, window)
        draws_t[start:start + m] = ht.reshape(m, -1).sum(axis=1)
        draws_l[start:start + m] = hl.reshape(m, -1).sum(axis=1)
        exp_by_fr_t += np.bincount(it[it >= 0], minlength=fr.size)
        exp_by_fr_l += np.bincount(il[il >= 0], minlength=fr.size)
    exp_by_fr_t /= n_draws
    exp_by_fr_l /= n_draws

    def stats(obs: int, draws: np.ndarray) -> dict[str, float]:
        mu, sd = float(draws.mean()), float(draws.std(ddof=1))
        return {"observed": obs, "expected": mu, "sd": sd, "z": (obs - mu) / sd if sd > 0 else float("nan"),
                "p_mc": float((np.sum(draws >= obs) + 1) / (draws.size + 1)), "excess": obs - mu}

    fractions = pd.DataFrame({"fraction": [str(Fraction(x).limit_denominator(max_denominator)) for x in fr], "value": fr,
                              "turnout_observed": obs_by_fr_t, "turnout_expected": exp_by_fr_t,
                              "leader_observed": obs_by_fr_l, "leader_expected": exp_by_fr_l})
    fractions["turnout_excess"] = fractions["turnout_observed"] - fractions["turnout_expected"]
    fractions["leader_excess"] = fractions["leader_observed"] - fractions["leader_expected"]
    by_sector: dict[str, Any] = {}
    for sec, idx in s.groupby("sector").indices.items():
        by_sector[str(sec)] = {"n": len(idx), "turnout_hits": int(hit_t[idx].sum()), "leader_hits": int(hit_l[idx].sum())}
    summary = {"n_stations_tested": int(ok.sum()), "n_fractions": int(fr.size), "turnout": stats(obs_t, draws_t),
               "leader_share": stats(obs_l, draws_l), "leader": feat.leader, "by_sector": by_sector,
               "top_excess_fraction_leader": str(fractions["fraction"].iloc[int(fractions["leader_excess"].to_numpy().argmax())]) if len(fractions) else None}
    units = pd.DataFrame({
        "knesset": s["knesset"].to_numpy(), "locality_code": s["locality_code"].to_numpy(), "locality_name": s["locality_name"].to_numpy(),
        "station": s["station"].to_numpy(), "sector": s["sector"].to_numpy(), "eligible": e, "voters": v, "valid": va, "leader_votes": lead,
        "turnout": v / e, "leader_share": lead / va, "hit_turnout": hit_t, "hit_leader": hit_l,
        "fraction_turnout": np.where(idx_t >= 0, fractions["fraction"].to_numpy()[np.clip(idx_t, 0, None)], ""),
        "fraction_leader": np.where(idx_l >= 0, fractions["fraction"].to_numpy()[np.clip(idx_l, 0, None)], ""),
    })
    params = {"n_draws": n_draws, "window": window, "max_denominator": max_denominator, "min_eligible": min_eligible,
              "max_turnout": max_turnout, "seed": seed}
    return TestResult("T06", "Coarse vote-share fractions", feat.knesset, "station", units, summary, params, extra={"fractions": fractions})
