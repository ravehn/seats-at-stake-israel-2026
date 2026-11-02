"""Power bench: detection rate of each station-level test at a fixed false-alarm budget.

Procedure for one (baseline election, injection type, fraction, intensity):
1. run the test on the clean election → clean score distribution per station;
2. the alarm threshold is the clean 99.9th percentile (one false alarm per thousand stations);
3. inject, run the test on the altered copy, and count altered stations whose score exceeds the
   threshold (power) and unaltered stations that newly exceed it (induced false alarms).
Scores: T09 and T12 use max |z|; T10 uses −log10 p at site level (best party per station); T11 uses
the excess z of the invalid share over the peers / over the station's history; T05 is election-level
and reports the shift of its z instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.t05_integer import t05_integer
from electionforensics.battery.t08_hierarchical import t08_hierarchical
from electionforensics.battery.t09_siblings import t09_siblings
from electionforensics.battery.t10_zero_peers import t10_zero_peers
from electionforensics.battery.t11_invalid import t11_invalid
from electionforensics.battery.t12_history_shift import t12_history_shift
from electionforensics.calibrate.baseline import station_scores
from electionforensics.calibrate.inject import inject
from electionforensics.config import DATA_PROCESSED
from electionforensics.model.features import Features

KEY = ["locality_code", "station"]
ALARM_PCTL = 0.999            # the flag budget: one false alarm per thousand stations
ALARM_PCTLS = (0.99, 0.999)   # also the watchlist budget


def _series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.Series(df[col].to_numpy(dtype=float), index=pd.MultiIndex.from_frame(df[KEY]))


def _keys(s: pd.Series) -> list[tuple[int, str]]:
    f = s.index.to_frame(index=False)
    return list(zip(f.iloc[:, 0].astype(int), f.iloc[:, 1].astype(str), strict=True))


def _scores(feat: Features, prev: Features | None) -> dict[str, pd.Series]:
    """Per-dimension scores keyed 'T09:turnout', 'T09:party', 'T10:party', 'T12:turnout', 'T12:party'."""
    out: dict[str, pd.Series] = {}
    sc = station_scores(t09_siblings(feat).units, "T09")
    if sc is not None:
        out["T09:turnout"], out["T09:party"] = _series(sc, "score_turnout"), _series(sc, "score_party")
    sc = station_scores(t10_zero_peers(feat).units, "T10")
    if sc is not None:
        out["T10:party"] = _series(sc, "score")
    sc = station_scores(t08_hierarchical(feat, prev).units, "T08")
    if sc is not None:
        out["T08:turnout"], out["T08:leader"] = _series(sc, "score_turnout"), _series(sc, "score_leader")
    sc = station_scores(t11_invalid(feat, prev).units, "T11")
    if sc is not None:
        out["T11:peers"] = _series(sc, "score_peers")
        if prev is not None:
            out["T11:history"] = _series(sc, "score_history")
    if prev is not None:
        sc = station_scores(t12_history_shift(feat, prev).units, "T12")
        if sc is not None:
            out["T12:turnout"], out["T12:party"] = _series(sc, "score_turnout"), _series(sc, "score_party")
    return out


def bench(feat: Features, prev: Features | None, kind: str, fraction: float, intensity: float = 0.5,
          target: str | None = None, sector: str | None = None, seed: int = 0, n_draws_t05: int = 500) -> dict[str, Any]:
    clean = _scores(feat, prev)
    altered, inj = inject(feat, kind, fraction, intensity, target, sector, seed)
    dirty = _scores(altered, prev)
    truth = set(inj.keys)
    res: dict[str, Any] = {"base_knesset": feat.knesset, "kind": kind, "fraction": fraction, "intensity": intensity,
                           "target": inj.target, "sector": sector, "seed": seed, "n_altered": len(truth), "tests": {}}
    for tid, c in clean.items():
        d = dirty.get(tid)
        if d is None:
            continue
        keys = _keys(d)
        vals = d.to_numpy(dtype=float)
        is_truth = np.array([k in truth for k in keys])
        covered = int(is_truth.sum())
        entry: dict[str, Any] = {"altered_scored": covered,
                                 "median_score_altered": float(np.nanmedian(vals[is_truth])) if covered else None}
        for pctl in ALARM_PCTLS:
            thr = float(np.nanquantile(c.to_numpy(dtype=float), pctl))
            above = vals > thr
            entry[f"p{pctl}"] = {
                "threshold": thr, "detected": int((above & is_truth).sum()),
                "power": float((above & is_truth).sum() / covered) if covered else None,
                "false_alarms_clean": int((c > thr).sum()), "false_alarms_dirty": int((above & ~is_truth).sum()),
            }
        entry["power"] = entry[f"p{ALARM_PCTL}"]["power"]
        res["tests"][tid] = entry
    z_clean = t05_integer(feat, n_draws=n_draws_t05, seed=seed).summary["turnout"]["z"]
    z_dirty = t05_integer(altered, n_draws=n_draws_t05, seed=seed).summary["turnout"]["z"]
    res["tests"]["T05"] = {"z_clean": z_clean, "z_dirty": z_dirty, "shift": z_dirty - z_clean}
    return res


def save(results: list[dict[str, Any]], root: Path = DATA_PROCESSED) -> Path:
    d = root / "power"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"power_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    p.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p
