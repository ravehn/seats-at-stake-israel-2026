"""Empirical null and tiers.

Every station-level score of the election under test is placed inside the distribution of the same
score in the baseline elections (same test, same sector). Nothing is called unusual because of a
textbook threshold: a score is "watch" when it sits above the 99.9th percentile of what the same
sector produced in 2019–2022, and "flag" when it exceeds everything seen in the baseline *and*
at least two tests agree on the same station (convergence). Tests share inputs and are not independent.
Flags additionally require Bonferroni station tails and BY across all scored stations. Interpretation
requires historical/current exchangeability; a screening result is not a probability of an offence.
Election-level statistics
(T05) are compared with the baseline mean and spread the same way.

Baseline elections: 22–25 for tests that need the site key (T09, T10 at site level) and 19–25 for
the rest. The 17th (no electorate) and the 16th/18th (no site key, older schema) are kept out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

from electionforensics.config import DATA_PROCESSED
from electionforensics.provenance import input_manifest, pinned_election, sha256

SITE_BASELINE = [22, 23, 24, 25]
WIDE_BASELINE = [19, 20, 21, 22, 23, 24, 25]
ALL_BASELINE = [16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
WATCH_PCTL = 0.999
FDR_ALPHA = 0.05
MIN_REFERENCE = 100
KEY = ["locality_code", "station"]
LOC_KEY = ["locality_code"]

#: Election-level statistics compared with the baseline: test → {name: (path inside the summary, baseline)}.
ELECTION_STATS: dict[str, dict[str, tuple[tuple[str, ...], list[int]]]] = {
    "T03": {f"{stat}_{sec}": (("by_sector", sec, stat), WIDE_BASELINE) for sec in ("jewish", "arab", "haredi") for stat in ("f_i", "f_e")},
    "T05": {"turnout": (("turnout", "z"), WIDE_BASELINE), "leader_share": (("leader_share", "z"), WIDE_BASELINE)},
    "T06": {"turnout": (("turnout", "z"), WIDE_BASELINE), "leader_share": (("leader_share", "z"), WIDE_BASELINE)},
    "T07": {"z_05": (("z_05",), ALL_BASELINE), "z_spike05": (("z_spike05",), ALL_BASELINE), "tv": (("tv",), ALL_BASELINE),
            "voters_z_spike05": (("voters_last_digit", "z_spike05"), ALL_BASELINE)},
    "T13": {"t_turnout": (("t_turnout",), WIDE_BASELINE), "t_leader": (("t_leader",), WIDE_BASELINE),
            "slope_turnout": (("slope_turnout_pp_per_doubling",), WIDE_BASELINE), "slope_leader": (("slope_leader_pp_per_doubling",), WIDE_BASELINE)},
    "T15": {"external_share": (("external_share",), WIDE_BASELINE), "invalid_ratio": (("invalid_ratio",), WIDE_BASELINE),
            "max_abs_gap_shift": (("max_abs_gap_shift_pp",), WIDE_BASELINE)},
    # T17: the 2003–2013 final files still contain uncorrected slips (120–410 per million); the modern CEC files
    # 2019b–2022 have at most 5 per million — that is the standard a final 2026 file is held to.
    "T17": {"displaced_per_million": (("displaced_per_million",), SITE_BASELINE), "n_cases": (("n_cases",), SITE_BASELINE)},
}
#: Locality-level tests: their `score` column is placed in the baseline distribution of locality scores.
LOCALITY_TESTS: dict[str, list[int]] = {"T07": ALL_BASELINE}


@dataclass
class Calibration:
    knesset: int
    stations: pd.DataFrame          # one row per station: scores, percentiles, tier
    election_level: dict[str, Any]  # T05 and other election-wide comparisons
    params: dict[str, Any] = field(default_factory=dict)
    localities: pd.DataFrame = field(default_factory=pd.DataFrame)  # one row per locality: locality-level test scores


def _get(d: Any, path: tuple[str, ...]) -> Any:
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _load(knesset: int, test_id: str, root: Path = DATA_PROCESSED) -> pd.DataFrame | None:
    p = root / f"knesset{knesset}" / f"{test_id}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def _load_summary(knesset: int, test_id: str, root: Path = DATA_PROCESSED) -> dict[str, Any] | None:
    p = root / f"knesset{knesset}" / f"{test_id}.json"
    return json.loads(p.read_text(encoding="utf-8"))["summary"] if p.exists() else None


def _pctl_within_sector(target: pd.DataFrame, baseline: pd.DataFrame, col: str) -> tuple[pd.Series, pd.Series]:
    """Empirical percentile of each target score within the baseline distribution of its sector; and baseline max."""
    pct = np.full(len(target), np.nan)
    bmax = np.full(len(target), np.nan)
    for sec, idx in target.groupby("sector").indices.items():
        b = baseline.loc[baseline["sector"] == sec, col].dropna().to_numpy()
        b = b[np.isfinite(b)]
        if b.size < MIN_REFERENCE:
            continue
        b = np.sort(b)
        pos = np.asarray(idx)
        x = target[col].to_numpy(dtype=float)[pos]
        p = np.searchsorted(b, x, side="left") / b.size
        pct[pos] = np.where(np.isnan(x), np.nan, p)
        bmax[pos] = b.max() if b.size else np.nan
    return pd.Series(pct, index=target.index), pd.Series(bmax, index=target.index)


def empirical_tails(target: pd.DataFrame, baseline: pd.DataFrame, col: str) -> tuple[pd.Series, pd.Series]:
    """Upper rank tails (1 + #reference >= score)/(N+1), including ties. Sparse sectors stay missing."""
    p = np.full(len(target), np.nan)
    counts = np.zeros(len(target), dtype=int)
    for sec, idx in target.groupby("sector").indices.items():
        b = baseline.loc[baseline["sector"] == sec, col].to_numpy(dtype=float)
        b = np.sort(b[np.isfinite(b)])
        pos = np.asarray(idx)
        counts[pos] = len(b)
        if len(b) < MIN_REFERENCE:
            continue
        x = target[col].to_numpy(dtype=float)[pos]
        tails = (1 + len(b) - np.searchsorted(b, x, side="left")) / (len(b) + 1)
        p[pos] = np.where(np.isfinite(x), tails, np.nan)
    return pd.Series(p, index=target.index), pd.Series(counts, index=target.index)


def apply_fdr_tiers(table: pd.DataFrame, tier_dims: tuple[str, ...], alpha: float = FDR_ALPHA) -> pd.DataFrame:
    """Bonferroni over configured dimensions, BY over all stations (missing p=1).

    BY tolerates arbitrary dependence given valid p-values. It cannot repair violations of historical
    exchangeability, adaptive selection or unmeasured complete-rule detection power.
    """
    out = table.copy()
    tails = pd.DataFrame({d: out.get(f"{d}_p", pd.Series(1.0, index=out.index)) for d in tier_dims})
    out["p_station"] = (tails.fillna(1.0).min(axis=1) * len(tier_dims)).clip(upper=1.0) if tier_dims else 1.0
    out["q_by"] = multipletests(out["p_station"].to_numpy(), method="fdr_by")[1] if len(out) else pd.Series(dtype=float)
    out["tier_candidate"] = (out["n_tests_above_max"] >= 1) & (out["n_tests_watch"] >= 2)
    out["tier"] = "none"
    out.loc[out["n_tests_watch"] >= 1, "tier"] = "watch"
    out.loc[out["tier_candidate"] & (out["q_by"] <= alpha), "tier"] = "flag"
    return out


def station_scores(units: pd.DataFrame | None, test_id: str) -> pd.DataFrame | None:
    """One row per station with one or two score columns.

    T09/T12 are split into two dimensions — `turnout` (|z| of turnout) and `party` (largest |z| over the
    major lists) — because legitimate heterogeneity of party shares between neighbouring stations is far
    larger than that of turnout, and a single max over both would let a turnout anomaly drown.
    T10 is one dimension: −log10 p of the most extreme list at site level. T11 has two: the excess of the
    invalid share over the building peers (`peers`) and over the station's own history (`history`), both z ≥ 0.
    """
    u = units
    if u is None or u.empty:
        return None
    if test_id == "T10":
        u = u[u["peer_level"] == "site"].copy()
        if u.empty:
            return None
        u["score"] = -np.log10(u["p_binom"].clip(lower=1e-300))
        u = u.sort_values("score", ascending=False).drop_duplicates(KEY)
        u["detail"] = u["party"]
        return u[[*KEY, "locality_name", "sector", "score", "detail"]]
    if test_id == "T08":
        u = u.copy()
        u["detail"] = ("явка z " + u["z_turnout"].round(1).astype(str) + "; " + u["loc_leader"].astype(str) + " z " + u["z_leader"].round(1).astype(str)
                       + np.where(u["h_turnout"].notna(), "", "; без истории"))
        return u[[*KEY, "locality_name", "sector", "score_turnout", "score_leader", "detail"]]
    if test_id == "T15":
        u = u.copy()
        u["score"] = u["max_abs_z"]
        u["detail"] = "пачка: " + u["top_dimension"].astype(str) + " z " + u["top_z"].round(1).astype(str)
        return u[[*KEY, "locality_name", "sector", "score", "detail"]]
    if test_id == "T13":
        u = u.copy()
        u["detail"] = ("явка " + (100 * u["turnout"]).round(0).astype(int).astype(str) + " % против крупных " + (100 * u["large_turnout"]).round(0).astype(int).astype(str)
                       + " % (z " + u["z_turnout"].round(1).astype(str) + "); " + u["loc_leader"] + " " + (100 * u["leader_share"]).round(0).astype(int).astype(str)
                       + " % против " + (100 * u["large_leader_share"]).round(0).astype(int).astype(str) + " % (z " + u["z_leader"].round(1).astype(str) + ")")
        return u[[*KEY, "locality_name", "sector", "score_turnout", "score_leader", "detail"]]
    if test_id == "T11":
        u = u.copy()
        u["detail"] = ("брак " + (100 * u["invalid_share"]).round(1).astype(str) + " % против соседей "
                       + (100 * u["peer_invalid_share"]).round(1).astype(str) + " %; z " + u["z_peers"].round(1).astype(str))
        has_h = u["z_history"].notna()
        u.loc[has_h, "detail"] = u.loc[has_h, "detail"] + "; история z " + u.loc[has_h, "z_history"].round(1).astype(str)
        return u[[*KEY, "locality_name", "sector", "score_peers", "score_history", "detail"]]
    if test_id in ("T09", "T12"):
        u = u.copy()
        pcols = [c for c in u.columns if c.startswith("z_") and c not in ("z_turnout", "z_peerdrift")]
        zp = u[pcols].abs() if pcols else pd.DataFrame(index=u.index)
        u["score_turnout"] = u["z_turnout"].abs()
        u["score_party"] = zp.max(axis=1) if pcols else np.nan
        if "z_peerdrift" in u.columns:
            u["score_peerdrift"] = u["z_peerdrift"].abs()
        top = zp.fillna(-1).idxmax(axis=1).astype(str).str[2:] if pcols else ""
        topz = zp.max(axis=1).round(1).astype(str) if pcols else ""
        u["detail"] = ("явка " + u["z_turnout"].round(1).astype(str) + "; " + top + " " + topz)
        cols = [*KEY, "locality_name", "sector", "score_turnout", "score_party", "detail"]
        if "score_peerdrift" in u.columns:
            u["detail"] = u["detail"] + "; дрейф к соседям " + u["z_peerdrift"].round(1).astype(str)
            cols.insert(-1, "score_peerdrift")
        return u[cols]
    return None


def _station_score(test_id: str, knesset: int, root: Path) -> pd.DataFrame | None:
    return station_scores(_load(knesset, test_id, root), test_id)


#: Score dimensions whose percentiles decide the tiers. T12's plain turnout/party shifts are computed
#: and shown but kept out of the tiers for the 26th Knesset: its baseline pairs are 1–2 years apart
#: while 2022→2026 is four years, so station drift (boundary changes, new neighbourhoods) is larger
#: than anything the baseline contains. The within-building peer drift (T12_peerdrift) is immune to
#: that and does count.
TIER_DIMS = ("T09_turnout", "T09_party", "T10_score", "T11_peers", "T12_peerdrift")


def expand_history_units(target: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """A collapsed history score belongs to its constituent boxes, with explicit shared scope."""
    mapping = universe[[*KEY, "station_num"]].copy()
    mapping["history_station"] = mapping["station_num"].astype(str)
    scores = target.rename(columns={"station": "history_station"})
    result = mapping.merge(scores, on=["locality_code", "history_station"], how="inner", validate="many_to_one")
    result["detail"] = result["detail"] + "; история объединённых урн № " + result["history_station"]
    return result.drop(columns=["station_num", "history_station"])


def calibrate(knesset: int = 26, root: Path = DATA_PROCESSED, tier_dims: tuple[str, ...] = TIER_DIMS) -> Calibration:
    inputs = input_manifest(knesset, root)
    universe = pinned_election(knesset, inputs["source"]).regular
    tests = {"T08": WIDE_BASELINE, "T09": SITE_BASELINE, "T10": SITE_BASELINE, "T11": SITE_BASELINE, "T12": WIDE_BASELINE,
             "T13": WIDE_BASELINE, "T15": WIDE_BASELINE}
    tier_tests = sorted({d.split("_")[0] for d in tier_dims})
    merged = universe[[*KEY, "locality_name"]].copy()
    merged["sector"] = pd.Series(None, index=merged.index, dtype=object)
    for tid, base_ks in tests.items():
        target = _station_score(tid, knesset, root)
        if target is None:
            continue
        if tid == "T12":
            target = expand_history_units(target, universe)
        if tid == "T15":  # external batches are a different population
            continue
        known = pd.MultiIndex.from_frame(universe[KEY])
        if not pd.MultiIndex.from_frame(target[KEY]).isin(known).all():
            raise ValueError(f"{tid}: score keys outside the pinned station universe")
        bases = [b for k in base_ks if k != knesset and (b := _station_score(tid, k, root)) is not None]
        base = pd.concat(bases, ignore_index=True) if bases else target.iloc[:0]
        score_cols = [c for c in target.columns if c.startswith("score")]
        t = target.rename(columns={"detail": f"{tid}_detail"})
        for col in score_cols:
            dim = col.replace("score", "").strip("_") or "score"
            name = f"{tid}_{dim}" if dim != "score" else f"{tid}_score"
            pct, bmax = _pctl_within_sector(target, base, col)
            tail, reference_n = empirical_tails(target, base, col)
            t = t.rename(columns={col: name})
            t[f"{name}_pctl"] = pct.to_numpy()
            t[f"{name}_p"] = tail.to_numpy()
            t[f"{name}_reference_n"] = reference_n.to_numpy()
            t[f"{name}_above_baseline_max"] = (target[col].to_numpy() > bmax.to_numpy())
        merged = merged.merge(t, on=KEY, how="left", suffixes=("", "_r"), validate="one_to_one")
        for c in ("locality_name", "sector"):
            merged[c] = merged[c].fillna(merged[f"{c}_r"])
            merged = merged.drop(columns=[f"{c}_r"])
    pctl_cols = [c for c in merged.columns if c.endswith("_pctl")]
    max_cols = [c for c in merged.columns if c.endswith("_above_baseline_max")]
    # convergence counts TESTS, not dimensions: a station with turnout and party both extreme in T09 is one test
    present = set(merged.columns)

    def _dims(tid: str, suffix: str) -> list[str]:
        return [f"{d}{suffix}" for d in tier_dims if d.startswith(tid) and f"{d}{suffix}" in present]

    tests_watch = pd.DataFrame({tid: (merged[_dims(tid, "_pctl")] >= WATCH_PCTL).any(axis=1) for tid in tier_tests})
    tests_max = pd.DataFrame({tid: merged[_dims(tid, "_above_baseline_max")].fillna(False).any(axis=1) for tid in tier_tests})
    merged["n_tests_watch"] = tests_watch.sum(axis=1) if pctl_cols else 0
    merged["n_tests_above_max"] = tests_max.sum(axis=1) if max_cols else 0
    merged = apply_fdr_tiers(merged, tier_dims)
    merged["max_pctl"] = merged[pctl_cols].max(axis=1) if pctl_cols else np.nan
    merged = merged.sort_values(["tier", "n_tests_watch", "max_pctl"], ascending=[True, False, False])
    merged["tier"] = pd.Categorical(merged["tier"], ["flag", "watch", "none"])
    merged = merged.sort_values(["tier", "n_tests_watch", "max_pctl"], ascending=[True, False, False]).reset_index(drop=True)

    # election-level statistics against the baseline spread (T05 integer percentages, T07 last digits, ...)
    el: dict[str, Any] = {}
    for tid, stats_ in ELECTION_STATS.items():
        s_now = _load_summary(knesset, tid, root)
        if not s_now or s_now.get("skipped"):
            continue
        for name, (path, base_ks) in stats_.items():
            z = _get(s_now, path)
            zs = [float(v) for k in base_ks if k != knesset and (s := _load_summary(k, tid, root)) and (v := _get(s, path)) is not None
                  and not (isinstance(v, float) and np.isnan(v))]
            if z is None or len(zs) < 2:
                continue
            z = float(z)
            mu, sd = float(np.mean(zs)), float(np.std(zs, ddof=1))
            entry: dict[str, Any] = {"z": z, "baseline_mean": mu, "baseline_sd": sd,
                                     "baseline_min": float(np.min(zs)), "baseline_max": float(np.max(zs)),
                                     "baseline_n": len(zs), "baseline_z": (z - mu) / sd if sd > 0 else float("nan"),
                                     "within_baseline_range": bool(np.min(zs) <= z <= np.max(zs))}
            if tid in ("T05", "T06"):
                entry.update({k2: s_now[name][k2] for k2 in ("observed", "expected", "excess")})
            el[f"{tid}_{name}"] = entry

    # locality-level tests: each locality's score inside the baseline distribution of locality scores
    loc: pd.DataFrame | None = None
    for tid, base_ks in LOCALITY_TESTS.items():
        target = _load(knesset, tid, root)
        if target is None or target.empty or "score" not in target.columns:
            continue
        bases = [b for k in base_ks if k != knesset and (b := _load(k, tid, root)) is not None and not b.empty]
        base = pd.concat(bases, ignore_index=True) if bases else target.iloc[:0]
        pct, bmax = _pctl_within_sector(target, base, "score")
        t = target[[*LOC_KEY, "locality_name", "sector", "score"]].rename(columns={"score": f"{tid}_score"})
        t[f"{tid}_score_pctl"] = pct.to_numpy()
        t[f"{tid}_above_baseline_max"] = target["score"].to_numpy() > bmax.to_numpy()
        for c in ("n", "share_05", "spike_05", "tv", "p_chi2"):
            if c in target.columns:
                t[f"{tid}_{c}"] = target[c].to_numpy()
        loc = t if loc is None else loc.merge(t, on=LOC_KEY, how="outer", suffixes=("", "_r"))
    if loc is None:
        loc = pd.DataFrame(columns=LOC_KEY)
    else:
        lp = [c for c in loc.columns if c.endswith("_pctl")]
        loc["max_pctl"] = loc[lp].max(axis=1) if lp else np.nan
        loc = loc.sort_values("max_pctl", ascending=False).reset_index(drop=True)

    params = {"watch_percentile": WATCH_PCTL, "site_baseline": SITE_BASELINE, "wide_baseline": WIDE_BASELINE,
              "all_baseline": ALL_BASELINE, "tier_dims": list(tier_dims), "tier_tests": list(tier_tests),
              "election_stats": sorted(el), "locality_tests": sorted(LOCALITY_TESTS),
              "flag_rule": "BY q <= .05 AND above baseline max in >=1 tier test AND >=2 tests above watch percentile",
              "fdr_method": "Bonferroni dimensions then Benjamini-Yekutieli stations", "fdr_alpha": FDR_ALPHA,
              "fdr_family_size": len(merged), "minimum_reference_n": MIN_REFERENCE,
              "limitations": ["Historical/current exchangeability is assumed, not established by adjustment.",
                              "Tests share inputs; convergence does not establish independence.",
                              "Power bench tests individual scores, not the complete flag rule.",
                              "T17 was developed after inspecting K26 and remains exploratory."]}
    params["inputs"] = inputs
    return Calibration(knesset, merged, el, params, loc)


def save(cal: Calibration, root: Path = DATA_PROCESSED) -> Path:
    d = root / f"knesset{cal.knesset}"
    d.mkdir(parents=True, exist_ok=True)
    cal.stations.to_parquet(d / "CAL_stations.parquet", index=False)
    cal.localities.to_parquet(d / "CAL_localities.parquet", index=False)
    lp = [c for c in cal.localities.columns if c.endswith("_pctl")]
    lm = [c for c in cal.localities.columns if c.endswith("_above_baseline_max")]
    loc_summary = {"n": len(cal.localities),
                   "watch": int((cal.localities[lp] >= WATCH_PCTL).any(axis=1).sum()) if lp else 0,
                   "above_baseline_max": int(cal.localities[lm].fillna(False).any(axis=1).sum()) if lm else 0}
    (d / "CAL.json").write_text(json.dumps({"election_level": cal.election_level, "params": cal.params,
                                             "calibration_tables": {name: sha256(d / name) for name in ("CAL_stations.parquet", "CAL_localities.parquet")},
                                             "tiers": cal.stations["tier"].value_counts().to_dict(),
                                             "localities": loc_summary},
                                            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return d / "CAL_stations.parquet"
