"""How sensitive the existing retrospective design is: matched twin cities against an injected city-wide effect.

Design A of the measurement document is what the project already runs after the fact (`link.exposure`,
`ef exposure-compare`): registered cities against their matched twins, pooled per tier, with a permutation
reference drawn from the untreated pool. Here `m` random cities of one sector receive a city-wide effect of
δ points (every station: δ % of the valid votes move from the change bloc's lists to מחל), the pooled test
runs on the altered election exactly as it would on a real register, and the share of replicates that reject
is the power of the retrospective design for that `m` and δ. Everything that limits the real comparison —
undocumented clearing in the "untreated" pool, the locality-level noise, the binary dose — limits it here too,
which is the point of the comparison with the prospective designs.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
import pandas as pd

from electionforensics.design.effect import allocate
from electionforensics.design.panel import LIKUD
from electionforensics.link.city_effect import blocs
from electionforensics.link.exposure import REGISTER_COLUMNS, exposure_comparison
from electionforensics.model.features import Features

RETRO_OUTCOMES = (f"s_{LIKUD}", "s_change", "s_coalition")


def inject_cities(feat: Features, codes: Sequence[int], delta: float, change_lists: Sequence[str]) -> Features:
    """A copy of `feat` where δ % of every station's valid votes in `codes` move from the change bloc to מחל."""
    st = feat.stations.copy()
    mask = st["locality_code"].isin(list(codes)).to_numpy()
    cols = [f"v_{p}" for p in change_lists if f"v_{p}" in st.columns]
    if not cols or not mask.any():
        return Features(feat.knesset, st, feat.parties, feat.major, feat.leader, feat.localities)
    change = st.loc[mask, cols].to_numpy(dtype=float)
    change = np.nan_to_num(change).astype(np.int64)
    valid = np.nan_to_num(st.loc[mask, "valid"].to_numpy(dtype=float)).astype(np.int64)
    k = np.minimum(np.rint(delta / 100.0 * valid).astype(np.int64), change.sum(axis=1))
    taken = allocate(k, change, cap=True)
    st.loc[mask, cols] = change - taken
    likud = np.nan_to_num(st.loc[mask, f"v_{LIKUD}"].to_numpy(dtype=float)).astype(np.int64)
    st.loc[mask, f"v_{LIKUD}"] = likud + k
    val = st["valid"].astype("Float64").mask(st["valid"] == 0)
    for p in [*change_lists, LIKUD]:
        if f"v_{p}" in st.columns:
            st[f"s_{p}"] = (st[f"v_{p}"] / val).astype("Float64")
    return Features(feat.knesset, st, feat.parties, feat.major, feat.leader, feat.localities)


def _register(codes: Sequence[int], names: dict[int, str]) -> pd.DataFrame:
    rows = [dict.fromkeys(REGISTER_COLUMNS, "") | {"locality_code": int(code), "locality_name": names.get(int(code), ""),
                                                 "tier": "A", "what": "simulated city-wide effect"} for code in codes]
    return pd.DataFrame(rows, columns=REGISTER_COLUMNS)


def candidate_cities(feats: dict[int, Features], prev: int, curr: int, sector: str = "jewish", min_stations: int = 8) -> list[int]:
    """Localities of the sector present in both elections with at least `min_stations` stations in the current one."""
    a = feats[prev].localities
    b = feats[curr].localities
    ok = b[(b["sector"] == sector) & (b["n_stations"] >= min_stations)]
    both = set(a["locality_code"].astype(int)) & set(ok["locality_code"].astype(int))
    return sorted(both)


def retro_power(feats: dict[int, Features], ms: Sequence[int] = (6, 12, 24), deltas: Sequence[float] = (0.0, 1.0, 2.0, 3.0),
                reps: int = 20, n_perm: int = 1000, seed: int = 0, prev: int = 25, curr: int = 26, sector: str = "jewish",
                min_stations: int = 8, k: int = 12, bennett_as_change: bool = True) -> pd.DataFrame:
    """One row per (m, δ, replicate, outcome): pooled mean gap, permutation p-value and the number of cities pooled."""
    rng = np.random.default_rng(seed)
    pool = candidate_cities(feats, prev, curr, sector, min_stations)
    names = dict(zip(feats[curr].localities["locality_code"].astype(int), feats[curr].localities["locality_name"], strict=True))
    change_lists = blocs(curr, bennett_as_change)["change"]
    rows: list[dict[str, Any]] = []
    for m in ms:
        if m > len(pool):
            raise ValueError(f"m = {m} exceeds the {len(pool)} candidate cities of sector {sector}")
        for delta in deltas:
            for rep in range(reps):
                t0 = time.time()
                codes = [int(c) for c in rng.choice(pool, size=m, replace=False)]
                shocked = inject_cities(feats[curr], codes, delta, change_lists)
                res = exposure_comparison(_register(codes, names), prev, curr, tiers=("A",), k=k, extra_parties=(LIKUD,),
                                          n_perm=n_perm, seed=int(rng.integers(2**31 - 1)),
                                          feats={prev: feats[prev], curr: shocked})
                pooled = res.pooled[res.pooled["sector"] == sector].set_index("outcome") if not res.pooled.empty else pd.DataFrame()
                for outcome in RETRO_OUTCOMES:
                    if outcome in pooled.index:
                        r = cast(Any, pooled.loc[outcome])
                        rows.append({"m": m, "delta": delta, "rep": rep, "outcome": outcome, "n_cities": int(r["n_cities"]),
                                     "mean_gap_pp": 100 * float(r["mean_gap"]), "p_perm": float(r["p_perm"]),
                                     "perm_lo_pp": 100 * float(r["perm_lo"]), "perm_hi_pp": 100 * float(r["perm_hi"]),
                                     "n_skipped": len(res.skipped), "seconds": round(time.time() - t0, 1)})
                    else:
                        rows.append({"m": m, "delta": delta, "rep": rep, "outcome": outcome, "n_cities": 0,
                                     "mean_gap_pp": float("nan"), "p_perm": float("nan"), "perm_lo_pp": float("nan"),
                                     "perm_hi_pp": float("nan"), "n_skipped": len(res.skipped),
                                     "seconds": round(time.time() - t0, 1)})
    return pd.DataFrame(rows)


def summarise_retro(df: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """Power (share of replicates with p < α) and the mean pooled gap per (m, δ, outcome)."""
    g = df.groupby(["m", "delta", "outcome"])
    out = g.agg(reps=("rep", "size"), n_cities=("n_cities", "mean"), mean_gap_pp=("mean_gap_pp", "mean"),
                band_lo_pp=("perm_lo_pp", "mean"), band_hi_pp=("perm_hi_pp", "mean"),
                power=("p_perm", lambda p: float(np.mean(p.dropna() < alpha)) if p.notna().any() else float("nan")))
    return out.reset_index()
