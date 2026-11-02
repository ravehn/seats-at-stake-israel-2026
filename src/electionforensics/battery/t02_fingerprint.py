"""T02 — the turnout × leader-share fingerprint (Klimek et al. 2012), by sector.

A 2D histogram of stations by turnout and by the leader's share. Homogeneous electorates produce
one compact cloud; ballot stuffing drags mass towards the (100 %, 100 %) corner and correlates the
two axes. Israel's sectors have different clouds by construction, so the fingerprint is computed
per sector and judged only against the same sector in previous elections (identical panels). The
statistics kept per sector: Pearson r(turnout, leader share), mass in the high-high corner, and the
coarse histogram itself (for the report panels).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features

BINS = np.linspace(0, 1, 41)  # 2.5-point bins
CORNER = (0.90, 0.80)         # turnout >= 90 % and leader share >= 80 %


@register("T02")
def t02_fingerprint(feat: Features, min_eligible: int = 50) -> TestResult:
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & st["turnout"].notna() & st["leader_share"].notna()
    s = st[ok]
    t = s["turnout"].astype(float).clip(0, 1).to_numpy()
    sec = s["sector"].to_numpy()
    rows = []
    hists: dict[str, np.ndarray] = {}
    for name in ["all", *sorted(set(sec))]:
        m = np.ones_like(t, dtype=bool) if name == "all" else (sec == name)
        if m.sum() < 20:
            continue
        # the sector's own leading list: the national leader is meaningless inside the Arab or Haredi sector
        vsum = s.loc[m, [f"v_{p}" for p in feat.parties]].sum()
        leader = str(vsum.idxmax())[2:]
        y = s[f"s_{leader}"].astype(float).clip(0, 1).to_numpy()
        r = float(np.corrcoef(t[m], y[m])[0, 1]) if m.sum() > 2 else float("nan")
        corner = float(np.mean((t[m] >= CORNER[0]) & (y[m] >= CORNER[1])))
        h, _, _ = np.histogram2d(t[m], y[m], bins=[BINS, BINS])
        hists[name] = h
        rows.append({"knesset": feat.knesset, "sector": name, "n": int(m.sum()), "leader": leader,
                     "turnout_mean": float(t[m].mean()), "turnout_sd": float(t[m].std()),
                     "leader_mean": float(y[m].mean()), "leader_sd": float(y[m].std()),
                     "r_turnout_leader": r, "corner_mass": corner,
                     "turnout_skew": float(stats.skew(t[m])), "turnout_kurt": float(stats.kurtosis(t[m]))})
    units = pd.DataFrame(rows)
    if not hists:
        return TestResult("T02", "Turnout × leader-share fingerprint", feat.knesset, "sector", units,
                          {"skipped": "no turnout available (electorate missing)"})
    flat = pd.DataFrame({
        "sector": np.repeat(list(hists), BINS.size - 1),
        "turnout_bin": np.tile(BINS[:-1], len(hists)),
        **{f"y{j}": np.concatenate([hists[k][:, j] for k in hists]) for j in range(BINS.size - 1)},
    })
    summary = {"leader": feat.leader, "by_sector": units.set_index("sector")[["n", "leader", "r_turnout_leader", "corner_mass"]].to_dict("index")}
    return TestResult("T02", "Turnout × leader-share fingerprint", feat.knesset, "sector", units, summary,
                      {"bins": int(BINS.size - 1), "corner": CORNER, "min_eligible": min_eligible}, extra={"hist": flat})
