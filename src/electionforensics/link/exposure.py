"""Documented exposure by city (a hand-kept register of sign clearing and related incidents) against the count.

Design 3 of docs/methods/poster-theft-effect.md in its weakest honest form: the dose is binary. A city either
has a documented incident of a given tier in the register or it has none. Every registered city of the tier
gets its own matched-twins gap (link.city_effect: change between two elections minus the weighted change of
similar cities of the same sector). The pooled statistic of a tier, within one sector, is the mean gap over its
cities. The reference distribution draws the same number of cities at random from the placebo gaps of that
sector's untreated donor pool (every registered city removed, whatever its tier); the share of draws whose mean
is at least as far from zero as the observed mean is the permutation p-value.

What this can and cannot say. "Untreated" means "no documented incident", never "no clearing": undocumented
clearing in control cities pulls the pooled gap towards zero. Documentation itself is selective (cities with
active volunteers and local press report more), the twins of one registered city may include another, and the
gaps are descriptive. Nothing here is a causal estimate; see `params["causal_interpretation"]`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from electionforensics.ingest.cbs import load_cbs
from electionforensics.ingest.cec import load_election
from electionforensics.link.city_effect import CityEffect, city_effect
from electionforensics.model.features import Features, build_features

REGISTER_COLUMNS = ["locality_code", "locality_name", "tier", "date", "victim_side", "what", "alleged_actor",
                    "official", "sources", "case_slug"]
MIN_REFERENCE_SPARE = 5  # untreated reference cities beyond the number pooled, else no pooled row for that sector


def load_register(path: Path) -> pd.DataFrame:
    """The exposure register: one row per documented incident, CBS locality code required for every row."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing = [c for c in REGISTER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"exposure register lacks columns {missing}")
    codes = pd.to_numeric(df["locality_code"], errors="coerce")
    if codes.isna().any():
        bad = df.loc[codes.isna(), "locality_name"].tolist()
        raise ValueError(f"exposure register rows without a numeric locality code: {bad}")
    df["locality_code"] = codes.astype(int)
    df["tier"] = df["tier"].str.strip().str.upper()
    return df


@dataclass
class ExposureResult:
    prev: int
    curr: int
    cities: pd.DataFrame    # one row per registered city x outcome: its matched-twins gap and placebo band
    pooled: pd.DataFrame    # one row per tier x sector x outcome: mean gap and permutation reference
    skipped: pd.DataFrame   # registered cities that could not be compared or pooled, with the reason
    params: dict[str, Any] = field(default_factory=dict)


def _perm_means(gaps: np.ndarray, m: int, n_perm: int, rng: np.random.Generator) -> np.ndarray:
    return np.array([rng.choice(gaps, size=m, replace=False).mean() for _ in range(n_perm)])


def exposure_comparison(register: pd.DataFrame, prev: int = 25, curr: int = 26, tiers: Sequence[str] = ("A",),
                        k: int = 12, extra_parties: Sequence[str] = ("מחל",), n_perm: int = 4000,
                        seed: int = 20261029, snapshot: Path | None = None,
                        feats: dict[int, Features] | None = None) -> ExposureResult:
    """Per-city gaps for every registered city of the requested tiers and the pooled permutation test per tier and sector."""
    if n_perm < 100:
        raise ValueError("n_perm must be at least 100")
    tiers = tuple(t.upper() for t in tiers)
    reg_tier = register["tier"].astype(str).str.strip().str.upper()
    registered = sorted({int(c) for c in register["locality_code"]})
    if feats is None:
        cbs = load_cbs()
        feats = {prev: build_features(load_election(prev), cbs), curr: build_features(load_election(curr, snapshot), cbs)}
    rng = np.random.default_rng(seed)
    city_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    references: dict[str, CityEffect] = {}      # sector -> one run whose placebo table covers that sector's pool
    runs: dict[tuple[str, str], list[CityEffect]] = {}
    for tier in tiers:
        codes = sorted({int(c) for c in register.loc[reg_tier == tier, "locality_code"]})
        for code in codes:
            try:
                ce = city_effect(code, prev, curr, k=k, feats=feats, snapshot=snapshot, extra_parties=extra_parties)
            except ValueError as exc:
                skipped.append({"tier": tier, "locality_code": code, "reason": str(exc)})
                continue
            sector = str(ce.params["sector"])
            references.setdefault(sector, ce)
            runs.setdefault((tier, sector), []).append(ce)
            twins_registered = sum(int(c) in registered for c in ce.donors.index)
            for name, r in ce.outcomes.items():
                city_rows.append({"tier": tier, "locality_code": code, "locality_name": ce.locality_name, "sector": sector,
                                  "outcome": name, "target_delta": r["target_delta"], "counterfactual_delta": r["counterfactual_delta"],
                                  "gap": r["effect"], "p_placebo": r["p_placebo"], "band_lo": r["band_lo"], "band_hi": r["band_hi"],
                                  "twins_registered": twins_registered, "n_twins": len(ce.donors)})
    pooled_rows: list[dict[str, Any]] = []
    n_reference: dict[str, int] = {}
    for (tier, sector), group in runs.items():
        ref_run = references[sector]
        untreated = ref_run.placebo[~ref_run.placebo["locality_code"].isin(registered)]
        n_reference[sector] = len(untreated)
        if len(untreated) < len(group) + MIN_REFERENCE_SPARE:
            for ce in group:
                skipped.append({"tier": tier, "locality_code": ce.locality_code,
                                "reason": f"not pooled: sector {sector} has {len(untreated)} untreated reference cities for {len(group)} pooled"})
            continue
        for name in ref_run.outcomes:
            obs = np.array([ce.outcomes[name]["effect"] for ce in group])
            ref = untreated[f"gap_{name}"].to_numpy(dtype=float)
            ref = ref[np.isfinite(ref)]
            perm = _perm_means(ref, len(obs), n_perm, rng)
            mean = float(obs.mean())
            pooled_rows.append({"tier": tier, "sector": sector, "outcome": name, "n_cities": len(obs), "mean_gap": mean,
                                "median_gap": float(np.median(obs)), "share_negative": float((obs < 0).mean()),
                                "p_perm": float((1 + (np.abs(perm) >= abs(mean)).sum()) / (n_perm + 1)),
                                "perm_lo": float(np.quantile(perm, 0.025)), "perm_hi": float(np.quantile(perm, 0.975)),
                                "n_reference": len(ref)})
    params = {"prev": prev, "curr": curr, "tiers": list(tiers), "k": k, "extra_parties": list(extra_parties),
              "n_perm": n_perm, "seed": seed, "snapshot": str(snapshot) if snapshot else None,
              "registered_localities": registered, "n_untreated_reference": n_reference,
              "min_reference_spare": MIN_REFERENCE_SPARE,
              "dose": "binary: documented incident of the tier in the register",
              "controls": "no documented incident, not no clearing; undocumented clearing biases the pooled gap towards zero",
              "causal_interpretation": False}
    if references:
        first = next(iter(references.values()))
        params["city_effect_params"] = {kk: v for kk, v in first.params.items() if kk not in ("input_feature_sha256", "sector")}
        params["input_feature_sha256"] = first.params.get("input_feature_sha256")
    return ExposureResult(prev, curr, pd.DataFrame(city_rows), pd.DataFrame(pooled_rows), pd.DataFrame(skipped), params)


def save(result: ExposureResult, directory: Path, register_path: Path | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    result.cities.to_csv(directory / "cities.csv", index=False, encoding="utf-8-sig")
    result.pooled.to_csv(directory / "pooled.csv", index=False, encoding="utf-8-sig")
    result.skipped.to_csv(directory / "skipped.csv", index=False, encoding="utf-8-sig")
    params = dict(result.params)
    if register_path is not None:
        params["register"] = {"path": str(register_path), "sha256": hashlib.sha256(register_path.read_bytes()).hexdigest()}
    (directory / "params.json").write_text(json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
    return directory
