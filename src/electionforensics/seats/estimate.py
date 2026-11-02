"""The seat estimate: coverage × δ × variant grid, step curves, Monte-Carlo over δ, provenance, tables.

Everything the article states as a number is computed here and written to `docs/article/results.json` and
`docs/article/tables/*.csv`; the article template reads that file (`seats/article.py`). Re-running after the official
results are gazetted is one command: `uv run ef seats build --snapshot <file>`.

δ grid (percentage points of the unit's valid votes moved from Likud to the change bloc) with its literature anchors
(`docs/research/outdoor-advertising-effects-2026-10-30.md` §§ 2–3): 0.4 — lower bound, two television-order effects for
the remaining day; 1.0 — the per-SD presence effect of street posters in Barcelona rounded up (0.61–0.96); 1.2 — loss of one
side's presence plus gain of the other's, one SD each (Barcelona); 2.0 — the single-party-exposure effect weighted to the
last day (centre of the note); 3.4 — twice the pooled lawn-sign RCT effect of 1.7, a full campaign on both sides; 5.0 —
the single-party-exposure effect for a whole two-week campaign (upper bound). The Monte-Carlo draws δ from a log-normal
whose 5th and 95th percentiles are the note's lower and upper bounds (0.4 and 5.0): median √(0.4·5) ≈ 1.41,
σ = ln(5/0.4)/(2·1.645) ≈ 0.77.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from electionforensics import __version__
from electionforensics.config import DATA_RAW, ROOT, TAXONOMY, THRESHOLD_BY_KNESSET
from electionforensics.ingest.cbs import CBS_FILE, load_cbs
from electionforensics.ingest.cec import ElectionData, load_election
from electionforensics.ingest.registry import snapshot_files
from electionforensics.model.features import Features, build_features
from electionforensics.seats.allocation import (
    Allocation,
    allocate_totals,
    election_allocation,
    frame_seats,
    load_agreements,
    load_blocs,
    load_frames,
    totals_from_stations,
)
from electionforensics.seats.counterfactual import TransferEngine, Variant, describe, variants
from electionforensics.seats.coverage import (
    ALL_KEYS,
    ITINERARY_TAU,
    LIKUD,
    MAIN_KEYS,
    build_coverages,
    locality_table,
)
from electionforensics.seats.itinerary import (
    ITINERARY_FILE,
    itinerary_records,
    itinerary_summary,
    itinerary_table,
    load_itinerary,
)
from electionforensics.seats.scaling import (
    SCALING_COVERAGE,
    SCALING_DELTAS,
    SCALING_FRACTIONS,
    SCALING_N_RANDOM,
    SCALING_SEED,
    coverage_scaling,
    spread_by_share,
)

ARTICLE_DIR = ROOT / "docs" / "article"
DELTAS = (0.4, 1.0, 1.2, 2.0, 3.4, 5.0)
DELTA_ANCHORS = {
    0.4: "lower bound: two effects of the size of a television-advertising effect (0.2 pp each, § 3.4) for the remaining day",
    1.0: "presence effect of street posters per SD of density, Barcelona working paper (0.61–0.96), rounded up",
    1.2: "replacement as loss plus gain of presence, one SD each (Barcelona working paper)",
    2.0: "single-party exposure weighted to the last day (centre of the range reviewed in § 3.4)",
    3.4: "twice the pooled lawn-sign effect of the randomised field experiments (1.7 pp), a full campaign on both sides",
    5.0: "single-party exposure for a whole two-week campaign, Barcelona working paper (upper bound)",
}
#: The locality of the documented case (CEC code), reported on its own in § 1: what the transfer would move there.
CASE_LOCALITY_CODE = 240
CASE_LOCALITY_NAME = "Yokneam Illit"
Z95 = 1.6448536269514722
#: The distributions of δ carried through § 5.3 (log-normal, fixed by the 5th and 95th percentiles in pp). The first is
#: the main one (headline numbers); the second is the premise's own reading — the exclusivity effect discounted to one
#: night and one election day plus a dominance-signal premium (§ 4.5); the third is the sceptic's — the 95 % interval of
#: persuasion effects in general elections across 49 field experiments taken as the range of δ.
PRIORS: dict[str, dict[str, Any]] = {
    "literature": {"label": "literature range", "low": 0.4, "high": 5.0,
                   "formalises": "the ends of the δ grid: two television-sized effects for the remaining hours to single-party "
                                 "exposure for a whole campaign (Table 3)"},
    "premise": {"label": "the premise's own reading", "low": 0.5, "high": 4.5,
                "formalises": "the components of Table 3a: exclusivity discounted to one night and one day, a dominance-signal "
                              "premium, a deduction for backlash"},
    "minimal": {"label": "minimal persuasion effects", "low": 0.15, "high": 1.7,
                "formalises": "the 95 % interval of persuasion in general elections (49 field experiments) taken as the range of δ"},
}
MAIN_PRIOR = "literature"


def prior_params(low: float, high: float) -> tuple[float, float]:
    """Median and log-scale σ of the log-normal whose 5th and 95th percentiles are `low` and `high`."""
    return math.sqrt(low * high), math.log(high / low) / (2 * Z95)


MC_LOW, MC_HIGH = PRIORS[MAIN_PRIOR]["low"], PRIORS[MAIN_PRIOR]["high"]
MC_MEDIAN, MC_SIGMA = prior_params(MC_LOW, MC_HIGH)
CURVE_MAX, CURVE_STEP = 10.0, 0.01
MC_COVERAGES = ("A", "B", "B_haredi")
OFFICIAL_25 = {"מחל": 32, "פה": 24, "ט": 14, "כן": 12, "שס": 11, "ג": 7, "ל": 6, "עם": 5, "ום": 5, "אמת": 4}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def code_fingerprint() -> str:
    """SHA-256 over the code and reference tables that determine the estimate."""
    pkg = Path(__file__).resolve().parent.parent
    paths = [
        *sorted((pkg / "seats").glob("*.py")),
        pkg / "battery" / "t16_representation.py", pkg / "config.py", pkg / "model" / "features.py",
        pkg / "ingest" / "cec.py", pkg / "ingest" / "cbs.py",
        TAXONOMY / "blocs.yaml", TAXONOMY / "surplus_agreements.yaml", TAXONOMY / "parties.yaml",
        TAXONOMY / "druze_localities.yaml",
    ]
    entries = {str(p.relative_to(ROOT)).replace("\\", "/"): _sha(p) for p in paths if p.exists()}
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def _name(data: ElectionData) -> str:
    return data.source.name if data.source is not None else "synthetic"


def _external_valid(data: ElectionData) -> float:
    ext = data.stations[data.stations["is_external"]]
    return float(ext[[f"v_{p}" for p in data.parties]].sum().sum())


def _alloc_record(a: Allocation, blocs: dict[str, list[str]]) -> dict[str, Any]:
    valid = a.valid
    return {
        "seats": a.seats, "blocs": a.bloc_seats(blocs), "eligible": a.eligible, "valid": valid,
        "threshold": a.threshold, "threshold_votes": a.threshold_votes, "wasted_votes": a.wasted_votes,
        "wasted_share": a.wasted_votes / valid if valid else None,
        "represented_votes": valid - a.wasted_votes,
        "votes_per_seat_quota": (valid - a.wasted_votes) / 120 if valid else None,
        "last_awarded": a.last_awarded, "runner_up": a.runner_up, "margin_votes": a.margin_votes,
        "agreements": a.agreements, "notes": a.notes,
        "votes": a.votes, "shares": {p: v / valid for p, v in a.votes.items()} if valid else {},
    }


def _curve(engine: TransferEngine, variant: Variant, threshold: float, agreements: list[tuple[str, str]],
           factual: Allocation, blocs: dict[str, list[str]]) -> pd.DataFrame:
    rows = []
    b0 = factual.bloc_seats(blocs)
    for i in range(round(CURVE_MAX / CURVE_STEP) + 1):
        delta = round(i * CURVE_STEP, 4)
        t = engine.run(delta, variant)
        a = t.allocate(threshold, agreements)
        b = a.bloc_seats(blocs)
        rows.append({"delta": delta, "d_coalition": b["coalition"] - b0["coalition"], "d_change": b["change"] - b0["change"],
                     "d_arab": b["arab"] - b0["arab"], "d_likud": a.seats.get(LIKUD, 0) - factual.seats.get(LIKUD, 0),
                     "moved": t.moved})
    return pd.DataFrame(rows)


def _breakpoints(curve: pd.DataFrame, max_k: int = 6) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for k in range(1, max_k + 1):
        hit = curve[curve["d_coalition"] <= -k]
        out[str(k)] = float(hit["delta"].iloc[0]) if len(hit) else None
    return out


def _monte_carlo(curve: pd.DataFrame, n_draws: int, seed: int, median: float, sigma: float) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    draws = np.exp(rng.normal(math.log(median), sigma, size=n_draws))
    idx = np.clip(np.floor(draws / CURVE_STEP + 1e-9).astype(int), 0, len(curve) - 1)
    d_coal = curve["d_coalition"].to_numpy()[idx]
    d_likud = curve["d_likud"].to_numpy()[idx]
    lost = -d_coal
    ks, counts = np.unique(lost, return_counts=True)
    pmf = {str(int(k)): float(c) / n_draws for k, c in zip(ks, counts, strict=True)}
    return {
        "n_draws": n_draws, "seed": seed, "median": median, "sigma": sigma,
        "delta_q05": float(np.quantile(draws, 0.05)), "delta_q50": float(np.quantile(draws, 0.5)),
        "delta_q95": float(np.quantile(draws, 0.95)), "share_above_curve_max": float((draws > CURVE_MAX).mean()),
        "pmf_seats_lost": pmf, "p_ge": {str(k): float((lost >= k).mean()) for k in range(1, 7)},
        "mean_seats_lost": float(lost.mean()), "median_seats_lost": float(np.median(lost)),
        "q05_seats_lost": float(np.quantile(lost, 0.05)), "q95_seats_lost": float(np.quantile(lost, 0.95)),
        "mean_likud_seats_lost": float((-d_likud).mean()),
    }


def run_estimate(snapshot: Path | None = None, deltas: tuple[float, ...] = DELTAS, n_draws: int = 200_000, seed: int = 20261031,
                 mc_median: float = MC_MEDIAN, mc_sigma: float = MC_SIGMA, coverage_keys: tuple[str, ...] = ALL_KEYS,
                 prev: tuple[ElectionData, Features] | None = None, curr: tuple[ElectionData, Features] | None = None,
                 ) -> dict[str, Any]:
    """Compute everything. `prev`/`curr` allow the tests to pass synthetic elections."""
    cbs = load_cbs() if (prev is None or curr is None) else None
    if prev is None:
        d25 = load_election(25)
        prev = (d25, build_features(d25, cbs))
    if curr is None:
        d26 = load_election(26, snapshot)
        curr = (d26, build_features(d26, cbs))
    d25, f25 = prev
    d26, f26 = curr
    k = d26.knesset
    threshold = THRESHOLD_BY_KNESSET[k]
    agreements, verified = load_agreements(k)
    blocs = load_blocs(k)
    frames = load_frames(k)
    factual = election_allocation(k, d26.stations, d26.parties)
    ext_valid = _external_valid(d26)
    # is the file used the newest one the snapshot task has stored? (None for synthetic data)
    on_disk = snapshot_files("expb") if (d26.source is not None and k == 26) else []
    latest_name = on_disk[-1].name if on_disk else None
    is_latest = (latest_name == _name(d26)) if latest_name else None
    # the prime minister's campaign itinerary (Appendix G): the threshold of reading I follows the press-sourced table
    itinerary: dict[str, Any] | None = None
    if k == 26 and d25.knesset == 25 and ITINERARY_FILE.exists():
        itin_tbl = itinerary_table(load_itinerary(), f25, f26)
        itinerary = itinerary_summary(itin_tbl, f26, f25) | {"rows": itinerary_records(itin_tbl)}
    coverages = build_coverages(f25, f26, coverage_keys, external_valid=ext_valid,
                                itinerary_tau=itinerary["tau"] if itinerary else ITINERARY_TAU)
    present_change = [p for p in blocs["change"] if p in d26.parties]
    vars_ = variants(present_change)
    main = vars_[0]
    # the covered stations of the readings the article integrates over, for the reference implementation (Appendix E):
    # locality, station, valid votes as the sum of all lists' votes, Likud's votes and the change-bloc lists' votes
    units: dict[str, pd.DataFrame] = {}
    vcols_all = [f"v_{p}" for p in d26.parties]
    keep = [f"v_{p}" for p in (LIKUD, *present_change) if f"v_{p}" in f26.stations.columns]
    for key in MC_COVERAGES:
        if key in coverages:
            sel = f26.stations[coverages[key].station_mask]
            tbl = sel[[c for c in ("locality_code", "station") if c in sel.columns]].copy()
            tbl["valid"] = sel[vcols_all].sum(axis=1).astype(int)
            for c in keep:
                tbl[c] = sel[c].astype(int)
            units[key] = tbl.reset_index(drop=True)

    grid: list[dict[str, Any]] = []
    curves: dict[str, pd.DataFrame] = {}
    breakpoints: dict[str, dict[str, float | None]] = {}
    mc: dict[str, Any] = {}
    engines: dict[str, TransferEngine] = {}
    for key, cov in coverages.items():
        eng = TransferEngine(d26.stations, f26.stations, cov.station_mask, d26.parties, blocs["change"])
        engines[key] = eng
        for v in vars_:
            for delta in deltas:
                t = eng.run(delta, v)
                a = t.allocate(threshold, agreements)
                rec = describe(t, a, factual, blocs, frames)
                rec["coverage"] = key
                grid.append(rec)
        curve = _curve(eng, main, threshold, agreements, factual, blocs)
        curves[key] = curve
        breakpoints[key] = _breakpoints(curve)
        if key in MC_COVERAGES:
            mc[key] = _monte_carlo(curve, n_draws, seed, mc_median, mc_sigma)

    # the documented case alone: the town's valid votes and what the transfer would move there at δ = 1 and δ = 5, against
    # the votes the first seat needs under each reading (the votes moved at the first breakpoint of the step curve)
    case_mask = f26.stations["locality_code"] == CASE_LOCALITY_CODE
    case_valid = int(f26.stations.loc[case_mask, vcols_all].sum(axis=1).sum())
    first_seat_votes: dict[str, int | None] = {}
    for key, bp in breakpoints.items():
        b1 = bp.get("1")
        hit = curves[key].loc[curves[key]["delta"].round(2) == b1, "moved"] if b1 is not None else None
        first_seat_votes[key] = int(hit.iloc[0]) if hit is not None and len(hit) else None
    case_locality = {"code": CASE_LOCALITY_CODE, "name": CASE_LOCALITY_NAME, "stations": int(case_mask.sum()),
                     "valid_curr": case_valid, "moved_at": {f"{d:g}": round(d / 100 * case_valid) for d in (1.0, 5.0)},
                     "first_seat_votes": first_seat_votes}

    # the same integration under every distribution of δ of § 4.5 (Table 3b), for the robustness table of § 5.3
    priors: list[dict[str, Any]] = []
    mc_priors: dict[str, dict[str, Any]] = {}
    for pk, p in PRIORS.items():
        med, sig = prior_params(p["low"], p["high"])
        priors.append({"key": pk, "label": p["label"], "low": p["low"], "high": p["high"], "median": med, "sigma": sig,
                       "formalises": p["formalises"]})
        mc_priors[pk] = {key: _monte_carlo(curves[key], n_draws, seed, med, sig) for key in MC_COVERAGES if key in curves}

    # seats against the covered share of the B⁺ vote (§ 5.5): the premise's own ordering against random orders
    scaling: dict[str, Any] | None = None
    if SCALING_COVERAGE in coverages:
        sc_deltas = tuple(d for d in SCALING_DELTAS if d in deltas) or SCALING_DELTAS
        rows = coverage_scaling(engines[SCALING_COVERAGE], main, coverages[SCALING_COVERAGE], f26.stations, locality_table(f25, f26),
                                factual, blocs, threshold, agreements, deltas=sc_deltas)
        base_valid = coverages[SCALING_COVERAGE].valid
        marks: dict[str, Any] = {}
        for key in MAIN_KEYS:
            if key in coverages:
                cells = {f"{d:g}": next(-r["d_blocs"]["coalition"] for r in grid
                                        if r["coverage"] == key and r["variant"] == main.key and r["delta"] == d)
                         for d in sc_deltas}
                marks[key] = {"share_of_set": coverages[key].valid / base_valid if base_valid else None, "seats": cells}
        scaling = {"coverage": SCALING_COVERAGE, "deltas": list(sc_deltas), "fractions": list(SCALING_FRACTIONS),
                   "orders": list(dict.fromkeys(rows["order"])), "n_random": SCALING_N_RANDOM, "seed": SCALING_SEED,
                   "marks": marks, "max_spread": spread_by_share(rows), "rows": rows.to_dict("records")}

    # verification of the method on the previous election
    prev_alloc = election_allocation(d25.knesset, d25.stations, d25.parties) if d25.knesset == 25 else None
    verification = None
    if prev_alloc is not None:
        got = {p: s for p, s in prev_alloc.seats.items() if s > 0}
        verification = {"knesset": 25, "official": OFFICIAL_25, "computed": got, "match": got == OFFICIAL_25,
                        "votes": prev_alloc.votes, "agreements": prev_alloc.agreements}

    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "package_version": __version__,
        "code_sha256": code_fingerprint(),
        "data": {
            "current": {"knesset": k, "file": _name(d26), "snapshot_ts": d26.snapshot_ts, "sha256": d26.sha256,
                        "regular_stations": len(f26.stations), "valid_regular": int(f26.stations["valid"].sum()),
                        "valid_all": int(sum(totals_from_stations(d26.stations, d26.parties).values())),
                        "external_valid": int(ext_valid), "external_rows": int(d26.stations["is_external"].sum()),
                        "localities": int(f26.localities["locality_code"].nunique()),
                        "eligible_voters": int(f26.stations["eligible"].sum()) if "eligible" in f26.stations else None,
                        "voters_regular": int(f26.stations["voters"].sum()) if "voters" in f26.stations else None,
                        "latest_snapshot_on_disk": latest_name, "is_latest": is_latest},
            "previous": {"knesset": d25.knesset, "file": _name(d25), "sha256": d25.sha256,
                         "regular_stations": len(f25.stations), "valid_regular": int(f25.stations["valid"].sum())},
            "cbs": {"file": CBS_FILE.name, "sha256": _sha(CBS_FILE) if CBS_FILE.exists() else None},
            # relative on purpose: the results file is published and must not carry a machine path
            "raw_root": DATA_RAW.relative_to(ROOT).as_posix() if DATA_RAW.is_relative_to(ROOT) else DATA_RAW.name,
        },
        "agreements": {"pairs": agreements, "verified": verified},
        "blocs": blocs,
        "frames": frames,
        "factual": _alloc_record(factual, blocs) | {"frames": frame_seats(factual.seats, frames)},
        "verification_previous": verification,
        "coverage": {key: cov.summary() for key, cov in coverages.items()},
        "coverage_order": list(coverages),
        "itinerary": itinerary,
        "deltas": [{"delta": d, "anchor": DELTA_ANCHORS.get(d, "")} for d in deltas],
        "variants": [{"key": v.key, "unit": v.unit, "recipients": v.recipients, "label": v.label} for v in vars_],
        "grid": grid,
        "breakpoints": breakpoints,
        "case_locality": case_locality,
        "mc": mc,
        "mc_params": {"low": MC_LOW, "high": MC_HIGH, "median": mc_median, "sigma": mc_sigma, "n_draws": n_draws, "seed": seed,
                      "curve_step": CURVE_STEP, "curve_max": CURVE_MAX},
        "priors": priors,
        "main_prior": MAIN_PRIOR,
        "mc_priors": mc_priors,
        "no_agreements": _alloc_record(allocate_totals(factual.votes, threshold, []), blocs)["blocs"],
        "coverage_scaling": scaling,
    }
    results["_curves"] = curves
    results["_units"] = units
    return results


def _grid_frame(results: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for r in results["grid"]:
        rows.append({"coverage": r["coverage"], "delta": r["delta"], "variant": r["variant"],
                     "likud_seats": r["likud"], "d_likud": r["d_likud"],
                     **{f"{b}_seats": s for b, s in r["blocs"].items()}, **{f"d_{b}": s for b, s in r["d_blocs"].items()},
                     "votes_moved": r["moved"], "votes_capped": r["capped"], "share_moved_national": r["share_moved_national"],
                     "eligible_changed": r["eligible_changed"], "n_units": r["n_units"],
                     "margin_votes": r["margin_votes"], **{f"seats_{p}": s for p, s in r["seats"].items()},
                     # the alternative bloc frame of Appendix C, cell by cell (the article keeps only its summary sentence)
                     **{f"frame_{name}": s for name, s in r.get("frames", {}).items()}})
    return pd.DataFrame(rows)


def save_results(results: dict[str, Any], out: Path = ARTICLE_DIR) -> dict[str, Path]:
    """Write results.json and the CSV tables; returns the paths."""
    out.mkdir(parents=True, exist_ok=True)
    tables = out / "tables"
    tables.mkdir(exist_ok=True)
    curves = results.pop("_curves", {})
    units = results.pop("_units", {})
    paths: dict[str, Path] = {}
    p = out / "results.json"
    p.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=_json_default), encoding="utf-8", newline="\n")
    paths["results"] = p
    grid = _grid_frame(results)
    grid.to_csv(tables / "grid_all_variants.csv", index=False, encoding="utf-8-sig")
    grid[grid["variant"] == "main"].drop(columns=["variant"]).to_csv(tables / "grid_main.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{k: v for k, v in c.items() if k != "by_sector"} | {f"sector_{s}": sh for s, sh in c["by_sector"].items()}
                  for c in results["coverage"].values()]).to_csv(tables / "coverage.csv", index=False, encoding="utf-8-sig")
    f = results["factual"]
    pd.DataFrame([{"list": p, "votes": v, "share": f["shares"][p], "seats": f["seats"].get(p, 0),
                   "above_threshold": p in f["eligible"]} for p, v in sorted(f["votes"].items(), key=lambda x: -x[1])]
                 ).to_csv(tables / "factual_allocation.csv", index=False, encoding="utf-8-sig")
    if curves:
        pd.concat([c.assign(coverage=k) for k, c in curves.items()]).to_csv(tables / "curves_main.csv", index=False, encoding="utf-8-sig")
    for key, tbl in units.items():
        tbl.to_csv(tables / f"units_{key}.csv", index=False, encoding="utf-8-sig")
    ver = results.get("verification_previous") or {}
    if ver.get("votes"):
        valid = sum(ver["votes"].values())
        pd.DataFrame([{"list": p, "votes": v, "share": v / valid, "seats": ver["computed"].get(p, 0),
                       "above_threshold": p in ver["computed"]} for p, v in sorted(ver["votes"].items(), key=lambda x: -x[1])]
                     ).to_csv(tables / "factual_allocation_k25.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"coverage": k, **{f"delta_for_{n}_seats": v for n, v in bp.items()}} for k, bp in results["breakpoints"].items()]
                 ).to_csv(tables / "breakpoints.csv", index=False, encoding="utf-8-sig")
    if results["mc"]:
        pd.DataFrame([{"coverage": k, **{kk: vv for kk, vv in m.items() if not isinstance(vv, dict)},
                       **{f"p_ge_{n}": v for n, v in m["p_ge"].items()}} for k, m in results["mc"].items()]
                     ).to_csv(tables / "monte_carlo.csv", index=False, encoding="utf-8-sig")
    if results.get("coverage_scaling"):
        pd.DataFrame(results["coverage_scaling"]["rows"]).to_csv(tables / "coverage_scaling.csv", index=False, encoding="utf-8-sig")
    if results.get("mc_priors"):
        prior_meta = {p["key"]: p for p in results["priors"]}
        pd.DataFrame([{"prior": pk, "coverage": k, "low": prior_meta[pk]["low"], "high": prior_meta[pk]["high"],
                       "median_delta": prior_meta[pk]["median"], "sigma": prior_meta[pk]["sigma"],
                       **{kk: vv for kk, vv in m.items() if not isinstance(vv, dict) and kk not in ("median", "sigma")},
                       **{f"p_ge_{n}": v for n, v in m["p_ge"].items()}}
                      for pk, per in results["mc_priors"].items() for k, m in per.items()]
                     ).to_csv(tables / "monte_carlo_priors.csv", index=False, encoding="utf-8-sig")
    if results.get("itinerary"):
        # the hand-curated appearances joined with the shares the package computed (Appendix G); `urls` is the list form
        itin = pd.DataFrame([{k: v for k, v in r.items() if k != "urls"} for r in results["itinerary"]["rows"]])
        for c in ("locality_code", "valid_prev", "valid_curr"):  # integers with a gap (Tel Hai), not floats
            if c in itin:
                itin[c] = itin[c].astype("Int64")
        itin.to_csv(tables / "itinerary_k26.csv", index=False, encoding="utf-8-sig")
    paths["tables"] = tables
    results["_curves"] = curves
    results["_units"] = units
    return paths


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"not serialisable: {type(o)!r}")


def load_results(out: Path = ARTICLE_DIR) -> dict[str, Any]:
    r: dict[str, Any] = json.loads((out / "results.json").read_text(encoding="utf-8"))
    curves_path = out / "tables" / "curves_main.csv"
    if curves_path.exists():
        c = pd.read_csv(curves_path)
        r["_curves"] = {str(k): g.drop(columns=["coverage"]).reset_index(drop=True) for k, g in c.groupby("coverage")}
    return r
