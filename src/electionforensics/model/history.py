"""Link polling stations across consecutive elections.

Israel numbers stations within a locality; numbers are broadly stable between elections but boxes
get split (14 → 14.1, 14.2) and renumbered when a locality grows. We therefore link at the
(locality_code, station_num) level after summing split boxes, and accept a link only when the
electorate is comparable (ratio within [0.7, 1.4]). Links are evidence-weighted, never assumed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from electionforensics.model.features import Features

ELIGIBLE_RATIO = (0.7, 1.4)


def collapse_split_boxes(feat: Features) -> pd.DataFrame:
    """Sum split boxes to whole stations; keep counts, recompute shares."""
    reg = feat.stations
    vcols = [f"v_{p}" for p in feat.parties]
    g = reg.groupby(["locality_code", "station_num"], dropna=True).agg(
        locality_name=("locality_name", "first"), sector=("sector", "first"), site=("site", "first"),
        n_boxes=("station", "size"), eligible=("eligible", "sum"), voters=("voters", "sum"),
        invalid=("invalid", "sum"), valid=("valid", "sum"), **{c: (c, "sum") for c in vcols},
    ).reset_index()
    g["turnout"] = g["voters"] / g["eligible"].replace(0, np.nan)
    g["invalid_share"] = g["invalid"] / g["voters"].replace(0, np.nan)
    for p in feat.parties:
        g[f"s_{p}"] = g[f"v_{p}"] / g["valid"].replace(0, np.nan)
    return g


def link(prev: Features, curr: Features) -> pd.DataFrame:
    """Return matched station pairs with turnout/share deltas and a match-quality flag."""
    a = collapse_split_boxes(prev)
    b = collapse_split_boxes(curr)
    m = b.merge(a, on=["locality_code", "station_num"], suffixes=("", "_prev"), how="inner")
    ratio = m["eligible"] / m["eligible_prev"].replace(0, np.nan)
    m["eligible_ratio"] = ratio
    m["good_match"] = ratio.between(*ELIGIBLE_RATIO)
    m["d_turnout"] = m["turnout"] - m["turnout_prev"]
    m["d_invalid_share"] = m["invalid_share"] - m["invalid_share_prev"]
    common = [p for p in curr.parties if p in prev.parties]
    for p in common:
        m[f"d_s_{p}"] = m[f"s_{p}"] - m[f"s_{p}_prev"]
    return m
