"""A treatment effect written into the real counts of the current election, one station at a time.

The experiment's arms are directions of one-sided campaigning at the fence of a building:

- `d = +1`  the change bloc does not post, the coalition does → the counted result moves towards Likud;
- `d = -1`  the coalition does not post, the change bloc does → it moves away from Likud;
- `d = 0`   both post (control, or a building whose assigned side did not comply).

Two mechanisms carry the dose δ (percentage points of the station's valid votes):

- `switch`   δ % of the valid votes leave the losing side's lists and are counted for the gaining side
             (the change bloc's lists pro rata → מחל, or מחל → the change bloc's lists pro rata);
- `abstain`  δ % of the valid votes that the losing side would have received are not cast at all
             (voters and valid fall; the gaining side's share rises by less than δ).

Every injection keeps Σ lists = valid, valid + invalid = voters and voters ≤ eligible, exactly as
`calibrate.inject` does for the power bench of the battery. Vote splits use the largest-remainder rule, so the
result is integer, deterministic and never exceeds what a list actually holds. The arithmetic lives in
`inject_arrays` on plain arrays; `apply_effect` is the same operation on the station table (used by the tests
and by anything that wants the full table back), and `StationArrays` is the compact form the simulator keeps.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from electionforensics.design.panel import LIKUD, OUTCOMES

MECHANISMS = ("switch", "abstain")


def allocate(k: np.ndarray, weights: np.ndarray, cap: bool = True) -> np.ndarray:
    """Split k[i] units over row i in proportion to `weights` (non-negative integers), largest remainder.

    With `cap` the split never exceeds the weights themselves (a donor list cannot give more than it has;
    callers keep k[i] ≤ weights[i].sum()). Rows whose weights are all zero split equally (recipients only).
    """
    k = np.asarray(k, dtype=np.int64)
    w_int = np.asarray(weights, dtype=np.int64)
    if w_int.ndim != 2 or k.shape != (w_int.shape[0],):
        raise ValueError("weights must be n×m and k of length n")
    n, m = w_int.shape
    if n == 0:
        return np.zeros((0, m), dtype=np.int64)
    w = w_int.astype(float)
    s = w.sum(axis=1)
    zero = s == 0
    w[zero] = 1.0
    s[zero] = m
    q = k[:, None] * w / s[:, None]
    base = np.floor(q + 1e-9).astype(np.int64)
    if cap:
        base = np.minimum(base, w_int)
    rem = k - base.sum(axis=1)
    frac = q - base
    if cap:
        frac = np.where(w_int - base >= 1, frac, -1.0)
    order = np.argsort(-frac, axis=1, kind="stable")
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.broadcast_to(np.arange(m), (n, m)), axis=1)
    add = (ranks < rem[:, None]).astype(np.int64)
    out: np.ndarray = base + add
    if cap and (out > w_int).any():
        raise AssertionError("allocation exceeded a donor list")
    return out


@dataclass
class StationArrays:
    """The counts the injection touches, as arrays over stations (one row per station of the panel)."""
    valid: np.ndarray            # int, current election
    voters: np.ndarray
    likud: np.ndarray            # votes of מחל
    change: np.ndarray           # n × m votes of the change bloc's lists
    coalition_other: np.ndarray  # votes of the coalition lists other than מחל (unchanged by any injection)
    valid_prev: np.ndarray       # previous election, for the swings
    likud_prev: np.ndarray
    coalition_prev: np.ndarray
    change_prev: np.ndarray

    @classmethod
    def from_stations(cls, stations: pd.DataFrame, lists: dict[str, list[str]]) -> StationArrays:
        change_cols = [f"v_{p}" for p in lists["change"]]
        other = [f"v_{p}" for p in lists["coalition"] if p != LIKUD]
        i64 = np.int64
        return cls(valid=stations["valid"].to_numpy(i64), voters=stations["voters"].to_numpy(i64),
                   likud=stations[f"v_{LIKUD}"].to_numpy(i64), change=stations[change_cols].to_numpy(i64),
                   coalition_other=stations[other].to_numpy(i64).sum(axis=1) if other else np.zeros(len(stations), i64),
                   valid_prev=stations["valid_prev"].to_numpy(i64), likud_prev=stations["v_likud_prev"].to_numpy(i64),
                   coalition_prev=stations["v_coalition_prev"].to_numpy(i64), change_prev=stations["v_change_prev"].to_numpy(i64))


def inject_arrays(arr: StationArrays, rows: np.ndarray, d: np.ndarray, delta: np.ndarray, mechanism: str = "switch",
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Counts of the stations `rows` after the injection: (valid, voters, likud, change, moved), copies."""
    if mechanism not in MECHANISMS:
        raise ValueError(f"unknown mechanism {mechanism!r}; choose from {MECHANISMS}")
    d = np.asarray(d, dtype=int)
    delta = np.asarray(delta, dtype=float)
    if (delta < 0).any():
        raise ValueError("delta must be non-negative; the sign is carried by d")
    valid, voters = arr.valid[rows].copy(), arr.voters[rows].copy()
    likud, change = arr.likud[rows].copy(), arr.change[rows].copy()
    k = np.rint(delta / 100.0 * valid).astype(np.int64)
    k[d == 0] = 0
    moved = np.zeros(len(rows), dtype=np.int64)
    plus = d > 0
    if plus.any():
        k_plus = np.minimum(k[plus], change[plus].sum(axis=1))
        change[plus] -= allocate(k_plus, change[plus], cap=True)
        if mechanism == "switch":
            likud[plus] += k_plus
        else:
            valid[plus] -= k_plus
            voters[plus] -= k_plus
        moved[plus] = k_plus
    minus = d < 0
    if minus.any():
        k_minus = np.minimum(k[minus], likud[minus])
        likud[minus] -= k_minus
        if mechanism == "switch":
            change[minus] += allocate(k_minus, change[minus], cap=False)
        else:
            valid[minus] -= k_minus
            voters[minus] -= k_minus
        moved[minus] = k_minus
    return valid, voters, likud, change, moved


def apply_effect(stations: pd.DataFrame, assign: pd.DataFrame, lists: dict[str, list[str]],
                 mechanism: str = "switch") -> pd.DataFrame:
    """Return the stations of the assigned buildings with the effect written into their counts.

    `assign` is indexed by `site_key` with columns `d` (−1, 0, +1) and `delta` (pp ≥ 0, the dose of a complier).
    The returned frame adds `moved`: the number of ballots the injection moved or withheld at the station.
    """
    sub = stations[stations["site_key"].isin(assign.index)].copy()
    if sub.empty:
        return sub.assign(moved=pd.Series(dtype=int))
    arr = StationArrays.from_stations(sub, lists)
    d = assign["d"].reindex(sub["site_key"]).to_numpy(dtype=int)
    delta = assign["delta"].reindex(sub["site_key"]).to_numpy(dtype=float)
    valid, voters, likud, change, moved = inject_arrays(arr, np.arange(len(sub)), d, delta, mechanism)
    sub[[f"v_{p}" for p in lists["change"]]] = change
    sub[f"v_{LIKUD}"] = likud
    sub["valid"], sub["voters"], sub["moved"] = valid, voters, moved
    sub["v_likud"] = likud
    sub["v_change"] = change.sum(axis=1)
    sub["v_coalition"] = likud + arr.coalition_other
    return sub


def building_outcomes(sub: pd.DataFrame) -> pd.DataFrame:
    """Building-level shares and swings (pp) recomputed from a (possibly modified) station table."""
    cols = ["valid", "valid_prev"] + [f"v_{n}" for n in OUTCOMES] + [f"v_{n}_prev" for n in OUTCOMES]
    g = sub.groupby("site_key")[cols].sum()
    out = pd.DataFrame(index=g.index)
    for n in OUTCOMES:
        out[f"share_{n}"] = 100 * g[f"v_{n}"] / g["valid"]
        out[f"swing_{n}"] = out[f"share_{n}"] - 100 * g[f"v_{n}_prev"] / g["valid_prev"]
    out["valid"] = g["valid"]
    if "moved" in sub.columns:
        out["moved"] = sub.groupby("site_key")["moved"].sum()
    return out


def check_identities(sub: pd.DataFrame, parties: list[str]) -> None:
    """Raise if any arithmetic identity of the station table is broken (used by the tests and the bench)."""
    vcols = [f"v_{p}" for p in parties if f"v_{p}" in sub.columns]
    if not (sub[vcols].sum(axis=1) == sub["valid"]).all():
        raise AssertionError("Σ lists ≠ valid")
    if not ((sub["valid"] + sub["invalid"]) == sub["voters"]).all():
        raise AssertionError("valid + invalid ≠ voters")
    if not (sub["voters"] <= sub["eligible"]).all():
        raise AssertionError("voters > eligible")
    if (sub[vcols] < 0).any().any():
        raise AssertionError("negative votes")
