"""Station-level features and locality strata for one election.

Strata are what keeps Israel's heterogeneity out of the anomaly lists: every test that compares a
station with "others" compares it with others of the same stratum (sector × size class) or with
its own building/locality peers. Sector comes from the CBS localities file (jewish / arab /
bedouin / mixed); `haredi` is assigned from voting history (UTJ + Shas share in the previous
election, falling back to the same election), never hand-labelled.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from electionforensics.ingest.cbs import load_cbs, sector_from_cbs
from electionforensics.ingest.cec import ElectionData, load_election
from electionforensics.ingest.registry import ELECTIONS

HAREDI_LISTS = ("ג", "שס")
HAREDI_SHARE_MIN = 0.5
MAJOR_SHARE_MIN = 0.01
SIZE_BINS = [0, 200, 400, 600, 10_000]
SIZE_LABELS = ["<200", "200-399", "400-599", "600+"]


@dataclass
class Features:
    knesset: int
    stations: pd.DataFrame  # regular stations with feature columns
    parties: list[str]
    major: list[str]        # parties with national share >= MAJOR_SHARE_MIN
    leader: str             # plurality list (ballot letters)
    localities: pd.DataFrame  # locality-level aggregates + strata


def party_shares_national(data: ElectionData) -> pd.Series:
    cols = [f"v_{p}" for p in data.parties]
    tot = data.stations[cols].sum()
    s = tot / tot.sum()
    s.index = [c[2:] for c in s.index]
    return s.sort_values(ascending=False)


def _haredi_localities(knesset: int) -> set[int]:
    """Localities where UTJ + Shas took >= 50% of valid votes in the previous election (same election for the first)."""
    ref = knesset - 1 if (knesset - 1) in ELECTIONS else knesset
    try:
        d = load_election(ref)
    except FileNotFoundError:
        d = load_election(knesset)
    reg = d.regular
    cols = [f"v_{p}" for p in HAREDI_LISTS if f"v_{p}" in reg.columns]
    if not cols:
        return set()
    g = reg.groupby("locality_code").agg(valid=("valid", "sum"), **{f"h{i}": (c, "sum") for i, c in enumerate(cols)})
    share = g[[f"h{i}" for i in range(len(cols))]].sum(axis=1) / g["valid"].replace(0, np.nan)
    return {int(c) for c in share[share >= HAREDI_SHARE_MIN].index}


def _druze_localities() -> set[int]:
    """Druze localities from taxonomy/druze_localities.yaml (the CBS file does not distinguish them)."""
    import yaml

    from electionforensics.config import TAXONOMY

    p = TAXONOMY / "druze_localities.yaml"
    if not p.exists():
        return set()
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {int(e["code"]) for e in doc.get("druze", [])}


def build_features(data: ElectionData, cbs: pd.DataFrame | None = None) -> Features:
    cbs = load_cbs() if cbs is None else cbs
    reg = data.regular.copy()
    parties = data.parties
    vcols = [f"v_{p}" for p in parties]

    nat = party_shares_national(data)
    leader = str(nat.index[0])
    major = [str(p) for p, s in nat.items() if s >= MAJOR_SHARE_MIN]

    eligible = reg["eligible"].astype("Float64")
    reg["turnout"] = (reg["voters"] / eligible.mask(eligible == 0)).astype("Float64")
    voters = reg["voters"].astype("Float64")
    reg["invalid_share"] = (reg["invalid"] / voters.mask(voters == 0)).astype("Float64")
    valid = reg["valid"].astype("Float64")
    valid = valid.mask(valid == 0)
    for p in parties:
        reg[f"s_{p}"] = (reg[f"v_{p}"] / valid).astype("Float64")
    reg["leader_share"] = reg[f"s_{leader}"]
    reg["size_class"] = pd.cut(reg["eligible"].astype("float"), bins=SIZE_BINS, labels=SIZE_LABELS, right=False)

    # locality strata
    sec = cbs[["locality_code", "cbs_name", "district", "district_code", "municipal_status", "population", "arab_share"]].copy()
    sec["sector"] = sector_from_cbs(cbs)
    sec["locality_code"] = sec["locality_code"].astype("Int64")
    haredi = _haredi_localities(data.knesset)
    sec.loc[sec["locality_code"].isin(list(haredi)) & (sec["sector"] == "jewish"), "sector"] = "haredi"
    sec.loc[sec["locality_code"].isin(list(_druze_localities())), "sector"] = "druze"
    sec["settlement"] = sec["district"].astype(str).str.contains("יהודה ושומרון", na=False)

    reg = reg.merge(sec, on="locality_code", how="left")
    reg["sector"] = reg["sector"].fillna("unknown")
    reg["site_key"] = reg["locality_code"].astype(str) + ":" + reg["site"].astype(str)
    reg.loc[reg["site"].isna(), "site_key"] = pd.NA

    loc = reg.groupby("locality_code").agg(
        locality_name=("locality_name", "first"), n_stations=("station", "size"),
        eligible=("eligible", "sum"), voters=("voters", "sum"), invalid=("invalid", "sum"), valid=("valid", "sum"),
        **{c: (c, "sum") for c in vcols},
    ).reset_index()
    loc["turnout"] = loc["voters"] / loc["eligible"].replace(0, np.nan)
    for p in parties:
        loc[f"s_{p}"] = loc[f"v_{p}"] / loc["valid"].replace(0, np.nan)
    loc = loc.merge(sec, on="locality_code", how="left")
    loc["sector"] = loc["sector"].fillna("unknown")
    return Features(data.knesset, reg, parties, major, leader, loc)
