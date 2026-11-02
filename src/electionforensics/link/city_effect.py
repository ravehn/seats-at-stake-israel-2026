"""Effect of a city-level event on that city's result: matched donors and placebo inference.

Design (docs/methods/poster-theft-effect.md, design 2): the city's change between two elections
(turnout, bloc shares) is compared with the change of a weighted set of similar cities of the same
sector (nearest neighbours in previous-election turnout, bloc shares and size). The gap is the
descriptive gap. Placebo runs give a reference distribution: every donor city is treated as if it were
the target, and the share of placebo gaps at least as large as the real one is the p-value. No
Gaussian assumption; the band describes comparison-city gaps, not a confidence interval for an
incident's causal effect. Common exposure, unobserved changes and failed parallel trends remain possible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import yaml

from electionforensics.config import TAXONOMY
from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import load_election
from electionforensics.ingest.places import load_places
from electionforensics.model.features import Features, build_features

BLOCS = ("coalition", "change", "arab")


def completeness(feat: Features) -> tuple[pd.Series, str]:
    """Current box coverage against the same election's register, never the previous box count."""
    try:
        places = load_places(feat.knesset)
    except FileNotFoundError:
        if feat.knesset == 26:
            raise ValueError("current polling-place register required to assess count completeness") from None
        return pd.Series(1.0, index=feat.stations["locality_code"].unique()), "archived final, assumed complete"
    expected = places.set_index(["locality_code", "station_num"])["n_boxes"]
    observed = feat.stations.groupby(["locality_code", "station_num"]).size()
    covered = observed.reindex(expected.index, fill_value=0).clip(upper=expected)
    return covered.groupby(level=0).sum() / expected.groupby(level=0).sum(), "same-election CEC polling-place register"


def blocs(knesset: int, bennett_as_change: bool = True) -> dict[str, list[str]]:
    doc = yaml.safe_load((Path(TAXONOMY) / "blocs.yaml").read_text(encoding="utf-8")) or {}
    e = dict(doc.get(knesset) or {})
    out = {b: list(e.get(b, [])) for b in BLOCS}
    if bennett_as_change:
        out["change"] = out["change"] + list(e.get("main_run_change_extra", []))
    return out


def locality_table(feat: Features, bl: dict[str, list[str]], parties: Sequence[str] = ()) -> pd.DataFrame:
    """Locality aggregates: bloc votes and shares, plus one share column per list in `parties` (ballot letters)."""
    st = feat.stations
    g = st.groupby("locality_code").agg(locality_name=("locality_name", "first"), sector=("sector", "first"),
                                       n_stations=("station", "size"), eligible=("eligible", "sum"),
                                       voters=("voters", "sum"), valid=("valid", "sum"))
    for b in BLOCS:
        cols = [f"v_{p}" for p in bl[b] if f"v_{p}" in st.columns]
        g[f"v_{b}"] = st.groupby("locality_code")[cols].sum().sum(axis=1) if cols else 0
        g[f"s_{b}"] = g[f"v_{b}"] / g["valid"].replace(0, np.nan)
    for party in parties:
        col = f"v_{party}"
        g[col] = st.groupby("locality_code")[col].sum() if col in st.columns else np.nan
        g[f"s_{party}"] = g[col] / g["valid"].replace(0, np.nan)
    g["turnout"] = g["voters"] / g["eligible"].replace(0, np.nan)
    return g


@dataclass
class CityEffect:
    locality_code: int
    locality_name: str
    prev: int
    curr: int
    outcomes: dict[str, dict[str, float]]  # outcome -> {target, counterfactual, effect, p_placebo, band_lo, band_hi}
    donors: pd.DataFrame
    placebo: pd.DataFrame
    params: dict[str, Any] = field(default_factory=dict)


def _knn_weights(x: np.ndarray, pool: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    d = np.sqrt(((pool - x) ** 2).sum(axis=1))
    idx = np.argsort(d)[:k]
    w = 1.0 / (d[idx] + 1e-6)
    return idx, w / w.sum()


def city_effect(locality_code: int, prev: int, curr: int, k: int = 12, min_stations: int = 8,
                min_completeness: float = 0.95, bennett_as_change: bool = True,
                feats: dict[int, Features] | None = None, snapshot: Path | None = None,
                extra_parties: Sequence[str] = ()) -> CityEffect:
    """Gap of one city's change (turnout, bloc shares and, with `extra_parties`, single lists by ballot letters)
    from its matched twins, with placebo reference bands. A list absent from either election yields no outcome
    for it: its rows are NaN and the donor pool drops them."""
    if k < 1 or not 0 < min_completeness <= 1:
        raise ValueError("k must be positive and min_completeness in (0, 1]")
    feats = feats or {}
    cbs = load_cbs()
    fp = feats.get(prev) or build_features(load_election(prev), cbs)
    fc = feats.get(curr) or build_features(load_election(curr, snapshot), cbs)
    a = locality_table(fp, blocs(prev, bennett_as_change), extra_parties)
    b = locality_table(fc, blocs(curr, bennett_as_change), extra_parties)
    m = a.join(b, how="inner", lsuffix="_p", rsuffix="_c")
    coverage, coverage_basis = completeness(fc)
    m["completeness"] = coverage.reindex(m.index).to_numpy(dtype=float)
    if locality_code not in m.index:
        raise ValueError(f"locality {locality_code} not present in both elections")
    target = m.loc[locality_code]
    if pd.isna(target["completeness"]) or target["completeness"] < min_completeness:
        raise ValueError(f"target locality count is incomplete: {target['completeness']}; required {min_completeness}")
    outcomes = {"turnout": ("turnout_p", "turnout_c"), **{f"s_{bl}": (f"s_{bl}_p", f"s_{bl}_c") for bl in BLOCS},
                **{f"s_{p}": (f"s_{p}_p", f"s_{p}_c") for p in extra_parties}}
    for name, (pc, cc) in outcomes.items():
        m[f"d_{name}"] = m[cc] - m[pc]
    pool = m[(m.index != locality_code) & (m["sector_p"] == target["sector_p"]) & (m["n_stations_p"] >= min_stations)
             & (m["completeness"] >= min_completeness)].copy()
    feat_cols = ["turnout_p", "s_coalition_p", "s_change_p", "s_arab_p"]
    pool["log_elig"] = np.log(pool["eligible_p"].astype(float))
    xcols = [*feat_cols, "log_elig"]
    pool = pool.replace([np.inf, -np.inf], np.nan).dropna(subset=[*xcols, *[f"d_{n}" for n in outcomes]])
    if len(pool) < 5:
        raise ValueError(f"at least five finite, complete donor localities required; found {len(pool)}")
    mu, sd = pool[xcols].mean(), pool[xcols].std().replace(0, 1)
    z_pool = ((pool[xcols] - mu) / sd).to_numpy(dtype=float)
    tv = cast(Any, target)
    tx = pd.Series({**{c: float(tv[c]) for c in feat_cols}, "log_elig": float(np.log(float(tv["eligible_p"])))})
    z_t = ((tx - mu) / sd).to_numpy(dtype=float)

    def estimate(z_x: np.ndarray, deltas: pd.DataFrame, exclude: int | None) -> dict[str, float]:
        mask = np.ones(len(pool), dtype=bool)
        if exclude is not None:
            mask &= pool.index != exclude
        idx, w = _knn_weights(z_x, z_pool[mask], k)
        sub = deltas[mask].iloc[idx]
        return {name: float((sub[f"d_{name}"].to_numpy(dtype=float) * w).sum()) for name in outcomes}

    deltas = pool[[f"d_{n}" for n in outcomes]]
    cf = estimate(z_t, deltas, None)
    idx, w = _knn_weights(z_t, z_pool, k)
    donors = pool.iloc[idx][["locality_name_p", "n_stations_p", "turnout_p", "s_coalition_p", "s_change_p", *[f"d_{n}" for n in outcomes]]].copy()
    donors["weight"] = w
    real = {name: float(tv[cc] - tv[pc]) for name, (pc, cc) in outcomes.items()}
    # placebo: each donor-pool city as pseudo-target
    rows = []
    for j, (code_h, row) in enumerate(pool.iterrows()):
        code = int(cast(Any, code_h))
        cf_j = estimate(z_pool[j], deltas, code)
        rows.append({"locality_code": code, "locality_name": row["locality_name_p"],
                     **{f"gap_{n}": float(row[f"d_{n}"] - cf_j[n]) for n in outcomes}})
    placebo = pd.DataFrame(rows)
    res: dict[str, dict[str, float]] = {}
    for n in outcomes:
        gap = real[n] - cf[n]
        pg = placebo[f"gap_{n}"].to_numpy(dtype=float)
        res[n] = {"target_delta": real[n], "counterfactual_delta": cf[n], "effect": gap,
                  "p_placebo": float((1 + (np.abs(pg) >= abs(gap)).sum()) / (len(pg) + 1)),
                  "band_lo": float(np.quantile(pg, 0.025)), "band_hi": float(np.quantile(pg, 0.975)),
                  "n_placebo": int(pg.size)}
    params = {"k": k, "min_stations": min_stations, "min_completeness": min_completeness,
              "bennett_as_change": bennett_as_change, "extra_parties": list(extra_parties),
              "donor_pool": len(pool), "sector": str(target["sector_p"]),
              "target_completeness": float(tv["completeness"]), "completeness_basis": coverage_basis, "match_features": xcols,
              "causal_interpretation": False, "band_kind": "placebo_reference_not_effect_confidence_interval",
              "input_feature_sha256": {str(f.knesset): hashlib.sha256(pd.util.hash_pandas_object(f.stations, index=True).values.tobytes()).hexdigest() for f in (fp, fc)},
              "snapshot": str(snapshot) if snapshot else None}
    return CityEffect(locality_code, str(target["locality_name_p"]), prev, curr, res, donors, placebo, params)


def save(result: CityEffect, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    result.donors.to_csv(directory / "donors.csv", encoding="utf-8-sig")
    result.placebo.to_csv(directory / "placebos.csv", index=False, encoding="utf-8-sig")
    p = directory / "estimate.json"
    p.write_text(json.dumps({"locality_code": result.locality_code, "locality_name": result.locality_name,
                             "prev": result.prev, "curr": result.curr, "outcomes": result.outcomes,
                             "params": result.params}, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
