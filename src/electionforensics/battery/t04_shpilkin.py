"""T04 — excess votes of the leader along the turnout axis (Shpilkin's method).

Votes are summed in one-point turnout bins separately for the leader and for all other lists. In
an electorate where support does not depend on turnout, the leader's curve is a scaled copy of the
others' curve; the scale k is estimated where the others' curve peaks and to its left. Leader votes
above k × others at higher turnout are the "excess" — a lower bound of votes that cannot be
explained by the common turnout profile. In Israel the assumption fails by construction across
sectors (Haredi towns vote at 80 % for Haredi lists), so the test runs per sector and is read
against the same sector in previous elections, never as an absolute number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features

BINS = np.linspace(0, 1, 101)


def _excess(t: np.ndarray, lead: np.ndarray, other: np.ndarray) -> dict[str, float]:
    lh, _ = np.histogram(t, bins=BINS, weights=lead)
    oh, _ = np.histogram(t, bins=BINS, weights=other)
    if oh.sum() == 0:
        return {"excess_votes": float("nan"), "k": float("nan"), "peak_bin": float("nan")}
    peak = int(np.argmax(oh))
    left = slice(max(0, peak - 15), peak + 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(oh[left] > 0, lh[left] / oh[left], np.nan)
    k = float(np.nanmedian(ratio)) if np.isfinite(ratio).any() else float(lh.sum() / oh.sum())
    excess = float(np.maximum(0, lh[peak + 1:] - k * oh[peak + 1:]).sum())
    return {"excess_votes": excess, "k": k, "peak_bin": float(BINS[peak]),
            "excess_share_of_leader": excess / lh.sum() if lh.sum() else float("nan"),
            "leader_votes": float(lh.sum()), "other_votes": float(oh.sum())}


@register("T04")
def t04_shpilkin(feat: Features, min_eligible: int = 50) -> TestResult:
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & st["turnout"].notna()
    s = st[ok]
    if s.empty:
        return TestResult("T04", "Leader excess along turnout", feat.knesset, "sector", pd.DataFrame(), {"skipped": "no turnout"})
    t = s["turnout"].astype(float).clip(0, 1).to_numpy()
    rows = []
    curves = []
    for name in ["all", *sorted(s["sector"].unique())]:
        m = np.ones(len(s), dtype=bool) if name == "all" else (s["sector"] == name).to_numpy()
        if m.sum() < 30:
            continue
        vsum = s.loc[m, [f"v_{p}" for p in feat.parties]].sum()
        leader = str(vsum.idxmax())[2:]
        lead = s[f"v_{leader}"].astype(float).to_numpy()[m]
        other = (s["valid"].astype(float).to_numpy()[m] - lead)
        r = _excess(t[m], lead, other)
        rows.append({"knesset": feat.knesset, "sector": name, "leader": leader, "n": int(m.sum()), **r})
        lh, _ = np.histogram(t[m], bins=BINS, weights=lead)
        oh, _ = np.histogram(t[m], bins=BINS, weights=other)
        curves.append(pd.DataFrame({"sector": name, "turnout_bin": BINS[:-1], "leader": lh, "others": oh}))
    units = pd.DataFrame(rows)
    summary = {"by_sector": units.set_index("sector")[["leader", "n", "k", "excess_votes", "excess_share_of_leader"]].to_dict("index")}
    return TestResult("T04", "Leader excess along turnout", feat.knesset, "sector", units, summary,
                      {"min_eligible": min_eligible, "bins": 100}, extra={"curves": pd.concat(curves, ignore_index=True)})
