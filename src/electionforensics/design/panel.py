"""The population of the experiment: polling buildings with their matched stations of two elections.

A building (ריכוז) is the unit of randomisation because its stations share one fence and one entrance: whatever
hangs there is seen by every voter of every station inside. Stations are matched between the two elections by
`model.history.link` (same locality, same station number, electorate ratio within tolerance); a building enters
the panel with the matched stations it holds in the current election, and its swing is the change of the bloc
share counted over those stations. Blocs come from `taxonomy/blocs.yaml` through `link.city_effect.blocs`.

The panel carries the counts themselves, not only the swings, so that `design.effect` can write a treatment into
the votes of the current election and the arithmetic identities (Σ lists = valid, valid + invalid = voters,
voters ≤ eligible) stay checkable after every injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import ElectionData, load_election
from electionforensics.ingest.places import load_sites
from electionforensics.link.city_effect import blocs
from electionforensics.model.features import Features, build_features
from electionforensics.model.history import link

LIKUD = "מחל"
#: Outcome blocs: the share of each is a building-level outcome; `likud` is the single list מחל.
OUTCOMES = ("likud", "coalition", "change")
#: Baseline covariates of the pre-specified analysis (previous election, percentage points; log electorate).
COVARIATES = ("likud_prev", "coalition_prev", "turnout_prev", "log_elig")
DEFAULT_SECTORS = ("jewish",)
MIN_VALID = 100
#: A building whose bloc share moved by more than this between the elections is treated as a matching error
#: (a renumbered station with the same number and a comparable electorate), not as a swing.
MAX_ABS_SWING = 30.0


@dataclass
class Panel:
    prev: int
    curr: int
    buildings: pd.DataFrame          # one row per building: counts of both elections, swings (pp), covariates
    stations: pd.DataFrame           # matched stations with the building key; the table the injection edits
    lists: dict[str, list[str]]      # bloc -> ballot letters in the current election
    lists_prev: dict[str, list[str]] # bloc -> ballot letters in the previous election
    source: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)  # per-panel precomputations

    @property
    def n_buildings(self) -> int:
        return len(self.buildings)


def _bloc_lists(knesset: int, parties: list[str], bennett_as_change: bool) -> dict[str, list[str]]:
    b = blocs(knesset, bennett_as_change)
    out = {"likud": [LIKUD] if LIKUD in parties else [],
           "coalition": [p for p in b["coalition"] if p in parties],
           "change": [p for p in b["change"] if p in parties]}
    if not out["likud"] or not out["change"]:
        raise ValueError(f"Knesset {knesset}: the panel needs מחל and at least one change-bloc list in the file")
    return out


def _sum(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    return df[cols].astype(float).sum(axis=1) if cols else pd.Series(0.0, index=df.index)


def prev_col(party: str, curr_parties: list[str]) -> str:
    """`link` suffixes a previous-election column with `_prev` only when the list ran in both elections."""
    return f"v_{party}_prev" if party in curr_parties else f"v_{party}"


def build_panel(prev: Features, curr: Features, sectors: tuple[str, ...] = DEFAULT_SECTORS, min_valid: int = MIN_VALID,
                sites: pd.DataFrame | None = None, bennett_as_change: bool = True,
                source: dict[str, Any] | None = None, max_abs_swing: float = MAX_ABS_SWING,
                lists: dict[str, list[str]] | None = None, lists_prev: dict[str, list[str]] | None = None) -> Panel:
    """Buildings of the requested locality sectors with their matched stations and bloc swings.

    `lists` / `lists_prev` override the taxonomy blocs (keys likud, coalition, change; ballot letters)."""
    if prev.knesset >= curr.knesset:
        raise ValueError("prev must be the earlier election")
    lists_prev = lists_prev or _bloc_lists(prev.knesset, prev.parties, bennett_as_change)
    lists = lists or _bloc_lists(curr.knesset, curr.parties, bennett_as_change)
    m = link(prev, curr)
    keep = m["good_match"] & m["sector"].isin(list(sectors)) & m["site"].notna()
    keep &= (m["valid"] >= min_valid) & (m["valid_prev"] >= min_valid)
    st = m.loc[keep].copy()
    if st.empty:
        raise ValueError("no matched stations after the filters")
    st["site"] = st["site"].astype(int)
    st["site_key"] = st["locality_code"].astype(int).astype(str) + ":" + st["site"].astype(str)
    for name, cols in lists.items():
        st[f"v_{name}"] = _sum(st, [f"v_{p}" for p in cols])
    for name, cols in lists_prev.items():
        st[f"v_{name}_prev"] = _sum(st, [prev_col(p, curr.parties) for p in cols])
    count_cols = ["eligible", "voters", "invalid", "valid", "eligible_prev", "voters_prev", "invalid_prev", "valid_prev"]
    for c in count_cols:
        st[c] = st[c].astype(float).round().astype(int)
    for c in [f"v_{p}" for p in curr.parties] + [prev_col(p, curr.parties) for p in prev.parties]:
        st[c] = st[c].astype(float).fillna(0).round().astype(int)

    agg: dict[str, Any] = {"locality_code": ("locality_code", "first"), "locality_name": ("locality_name", "first"),
                           "sector": ("sector", "first"), "n_stations": ("station_num", "size")}
    agg.update({c: (c, "sum") for c in count_cols})
    agg.update({f"v_{n}": (f"v_{n}", "sum") for n in OUTCOMES})
    agg.update({f"v_{n}_prev": (f"v_{n}_prev", "sum") for n in OUTCOMES})
    b = st.groupby("site_key").agg(**agg)
    b["locality_code"] = b["locality_code"].astype(int)
    b["site"] = b.index.str.split(":").str[1].astype(int)
    for n in OUTCOMES:
        b[f"share_{n}"] = 100 * b[f"v_{n}"] / b["valid"]
        b[f"share_{n}_prev"] = 100 * b[f"v_{n}_prev"] / b["valid_prev"]
        b[f"swing_{n}"] = b[f"share_{n}"] - b[f"share_{n}_prev"]
    b["turnout"] = 100 * b["voters"] / b["eligible"].replace(0, np.nan)
    b["turnout_prev"] = 100 * b["voters_prev"] / b["eligible_prev"].replace(0, np.nan)
    b["swing_turnout"] = b["turnout"] - b["turnout_prev"]
    b["likud_prev"], b["coalition_prev"], b["change_prev"] = b["share_likud_prev"], b["share_coalition_prev"], b["share_change_prev"]
    b["log_elig"] = np.log(b["eligible"].clip(lower=1))
    b["x"], b["y"] = np.nan, np.nan
    if sites is not None and {"locality_code", "site", "x", "y"} <= set(sites.columns):
        xy = sites.dropna(subset=["locality_code", "site"]).copy()
        xy["site_key"] = xy["locality_code"].astype(int).astype(str) + ":" + xy["site"].astype(int).astype(str)
        xy = xy.drop_duplicates("site_key").set_index("site_key")[["x", "y"]].astype(float)
        b["x"] = xy["x"].reindex(b.index).to_numpy()
        b["y"] = xy["y"].reindex(b.index).to_numpy()
        b.loc[(b["x"] <= 0) | (b["y"] <= 0), ["x", "y"]] = np.nan   # the register codes a missing position as 0
    b = b.dropna(subset=["turnout_prev", "likud_prev", "coalition_prev"])
    swing_cols = [f"swing_{n}" for n in OUTCOMES]
    outliers = int((b[swing_cols].abs().max(axis=1) > max_abs_swing).sum())
    b = b[b[swing_cols].abs().max(axis=1) <= max_abs_swing].sort_values(["locality_code", "site"])
    st = st[st["site_key"].isin(b.index)].sort_values(["locality_code", "site", "station_num"]).reset_index(drop=True)
    src = dict(source or {})
    src.update({"prev": src.get("prev", {"knesset": prev.knesset}), "curr": src.get("curr", {"knesset": curr.knesset}),
                "sectors": list(sectors), "min_valid": min_valid, "bennett_as_change": bennett_as_change,
                "max_abs_swing": max_abs_swing, "n_outliers_dropped": outliers,
                "n_stations": len(st), "n_buildings": len(b), "n_localities": int(b["locality_code"].nunique()),
                "built": datetime.now(UTC).isoformat(timespec="seconds")})
    return Panel(prev.knesset, curr.knesset, b, st, lists, lists_prev, src)


def _version(data: ElectionData) -> dict[str, Any]:
    return {"knesset": data.knesset, "file": data.source.name if data.source else None, "sha256": data.sha256,
            "snapshot_ts": data.snapshot_ts}


def load_panel(prev: int = 25, curr: int = 26, snapshot: Path | None = None, sectors: tuple[str, ...] = DEFAULT_SECTORS,
               min_valid: int = MIN_VALID, with_sites: bool = True, max_abs_swing: float = MAX_ABS_SWING) -> Panel:
    """Build the panel from the CEC files (the latest 2026 snapshot unless `snapshot` is given) with the data version."""
    cbs = load_cbs()
    ep, ec = load_election(prev), load_election(curr, snapshot)
    fp, fc = build_features(ep, cbs), build_features(ec, cbs)
    sites = None
    if with_sites:
        try:
            sites = load_sites(curr)
        except FileNotFoundError:
            sites = None
    return build_panel(fp, fc, sectors, min_valid, sites, source={"prev": _version(ep), "curr": _version(ec)},
                       max_abs_swing=max_abs_swing)


def noise_summary(panel: Panel) -> pd.DataFrame:
    """Dispersion of building swings per outcome: overall, within locality (the planning noise of the design note)."""
    rows = []
    b = panel.buildings
    multi = b.groupby("locality_code")["swing_likud"].transform("size") >= 2
    for n in OUTCOMES:
        y = b[f"swing_{n}"]
        g = b.loc[multi].groupby("locality_code")[f"swing_{n}"]
        resid = b.loc[multi, f"swing_{n}"] - g.transform("mean")
        n_g = g.transform("size")
        corr = float(np.sqrt((n_g / (n_g - 1)).mean()))
        rows.append({"outcome": n, "n_buildings": len(b), "sd_all": float(y.std()),
                     "sd_within_locality": float(resid.std() * corr), "mean": float(y.mean())})
    return pd.DataFrame(rows)
