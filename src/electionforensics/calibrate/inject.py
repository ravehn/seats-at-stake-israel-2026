"""Synthetic distortions injected into real elections — the power bench.

A test is only as good as its measured sensitivity. Here a known distortion of a known type and
intensity is written into a copy of a real (baseline) election, the battery is run on the copy, and
we measure how many of the altered stations the test ranks above the level that the *clean* election
never exceeds. Types mirror the incident categories:

- stuffing       : add ballots for the leader from the non-voters (incremental fraud, Klimek)
- transfer       : move a share of other lists' votes to the leader (vote stealing)
- slip_theft     : one target list collapses to zero, its votes go to the other lists pro rata
- rounding       : adjust voters so that turnout lands exactly on an integer percentage
- protocol_swap  : replace a station's counts with a fabricated extreme protocol (turnout 95 %, leader 90 %)
- spoiling       : move a share of the valid ballots into the invalid pile, pro rata over the lists (selective invalidation)

Every injection keeps the arithmetic identities (valid + invalid = voters, Σ lists = valid).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd

from electionforensics.model.features import Features

TYPES = ("stuffing", "transfer", "slip_theft", "rounding", "protocol_swap", "spoiling")


@dataclass
class Injection:
    kind: str
    fraction: float
    intensity: float
    target: str                      # ballot letters of the list that gains (or loses, for slip_theft)
    keys: set[tuple[int, str]] = field(default_factory=set)   # (locality_code, station) altered


def _geti(st: pd.DataFrame, i: Any, col: str) -> int:
    return int(cast(Any, st.at[i, col]))


def _weights(st: pd.DataFrame, i: Any, parties: list[str]) -> np.ndarray:
    w = np.asarray([_geti(st, i, f"v_{p}") for p in parties], dtype=np.float64)
    if w.sum() == 0:
        w = np.ones(len(parties), dtype=np.float64)
    return w / w.sum()


def _recompute(st: pd.DataFrame, parties: list[str], leader: str) -> pd.DataFrame:
    vcols = [f"v_{p}" for p in parties]
    st["valid"] = st[vcols].sum(axis=1).astype("Int64")
    st["voters"] = (st["valid"] + st["invalid"]).astype("Int64")
    elig = st["eligible"].astype("Float64")
    st["turnout"] = (st["voters"] / elig.mask(elig == 0)).astype("Float64")
    voters = st["voters"].astype("Float64")
    st["invalid_share"] = (st["invalid"] / voters.mask(voters == 0)).astype("Float64")
    valid = st["valid"].astype("Float64").mask(st["valid"] == 0)
    for p in parties:
        st[f"s_{p}"] = (st[f"v_{p}"] / valid).astype("Float64")
    st["leader_share"] = st[f"s_{leader}"]
    return st


def inject(feat: Features, kind: str, fraction: float, intensity: float = 0.5, target: str | None = None,
           sector: str | None = None, seed: int = 0) -> tuple[Features, Injection]:
    """Return a modified copy of `feat` and the ground truth of altered stations."""
    if kind not in TYPES:
        raise ValueError(f"unknown injection type {kind!r}; choose from {TYPES}")
    rng = np.random.default_rng(seed)
    st = feat.stations.copy()
    parties = feat.parties
    leader = feat.leader
    target = target or leader
    pool = st.index.to_numpy()
    if sector:
        pool = st.index[st["sector"] == sector].to_numpy()
    pool = pool[(st.loc[pool, "eligible"].fillna(0) > 0).to_numpy()]
    n = max(1, round(fraction * pool.size))
    chosen = rng.choice(pool, size=n, replace=False)
    inj = Injection(kind, fraction, intensity, target)

    for i in chosen:
        row = st.loc[i]
        e, v = _geti(st, i, "eligible"), _geti(st, i, "voters")
        if kind == "stuffing":
            room = max(0, e - v)
            add = round(room * intensity * rng.uniform(0.5, 1.0))
            st.at[i, f"v_{target}"] = _geti(st, i, f"v_{target}") + add
        elif kind == "transfer":
            for p in parties:
                if p == target:
                    continue
                take = round(_geti(st, i, f"v_{p}") * intensity)
                st.at[i, f"v_{p}"] = _geti(st, i, f"v_{p}") - take
                st.at[i, f"v_{target}"] = _geti(st, i, f"v_{target}") + take
        elif kind == "slip_theft":
            lost = _geti(st, i, f"v_{target}")
            if lost == 0:
                continue
            others = [p for p in parties if p != target]
            alloc = rng.multinomial(lost, _weights(st, i, others))
            st.at[i, f"v_{target}"] = 0
            for p, a in zip(others, alloc, strict=True):
                st.at[i, f"v_{p}"] = _geti(st, i, f"v_{p}") + int(a)
        elif kind == "rounding":
            if e <= 0 or v <= 0:
                continue
            # integer percentages reachable with this electorate are multiples of 100/gcd(e, 100)
            step = 100 // math.gcd(e, 100)
            pct = int(round(100 * v / e / step) * step)
            if pct <= 0 or pct > 100:
                continue
            new_v = e * pct // 100
            delta = new_v - v
            nv = _geti(st, i, f"v_{target}") + delta
            if nv < 0:
                continue
            st.at[i, f"v_{target}"] = nv
        elif kind == "protocol_swap":
            new_v = round(e * 0.95)
            new_inv = round(new_v * 0.01)
            valid = new_v - new_inv
            lead = round(valid * 0.90)
            rest = valid - lead
            others = [p for p in parties if p != target]
            alloc = rng.multinomial(rest, _weights(st, i, others))
            st.at[i, f"v_{target}"] = lead
            for p, a in zip(others, alloc, strict=True):
                st.at[i, f"v_{p}"] = int(a)
            st.at[i, "invalid"] = new_inv
        elif kind == "spoiling":
            valid = sum(_geti(st, i, f"v_{p}") for p in parties)
            take = round(valid * 0.2 * intensity)  # intensity 1.0 spoils a fifth of the valid ballots
            if take <= 0:
                continue
            alloc = rng.multinomial(take, _weights(st, i, parties))
            spoiled = 0
            for p, a in zip(parties, alloc, strict=True):
                t = min(int(a), _geti(st, i, f"v_{p}"))
                st.at[i, f"v_{p}"] = _geti(st, i, f"v_{p}") - t
                spoiled += t
            st.at[i, "invalid"] = _geti(st, i, "invalid") + spoiled
        inj.keys.add((int(row["locality_code"]), str(row["station"])))

    st = _recompute(st, parties, leader)
    new = Features(feat.knesset, st, parties, feat.major, leader, feat.localities)
    return new, inj
