"""Power of the proposed experiments, simulated on the real panel of buildings.

One replicate of a scenario: draw the buildings (uniformly, or whole localities with up to a fixed number of
buildings each), form the strata, assign the arms by permuted blocks inside each stratum, draw each building's
dose and compliance, write the effect into the real 2026 counts (`design.effect`), recompute the swings and run
the pre-specified analysis (`design.estimators`). Thousands of replicates give the power (share of replicates
that reject the null at α), the minimum detectable effect, the coverage of the confidence intervals and the bias
of the estimators — all against the swings that actually happened between the two elections, with their real
dispersion, tails and locality structure, which the normal approximation of the planning note assumes away.

Designs: `two_arm` — two symmetric arms, every building has one side absent (difference between arms 2δ);
`one_arm` — one side absent against a control where both post (difference δ, four times the buildings).
Strata: `pairs` (matched on the previous coalition share across the sample), `bins` (quartiles of that share),
`locality` (blocks of buildings inside sampled localities), `locality_pairs` (pairs matched inside localities).
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from electionforensics.design.effect import MECHANISMS, StationArrays, inject_arrays
from electionforensics.design.estimators import INFERENCE, Estimate, fit_arrays
from electionforensics.design.panel import COVARIATES, OUTCOMES, Panel

DESIGNS = ("two_arm", "one_arm")
STRATA = ("pairs", "bins", "locality", "locality_pairs")
HETEROGENEITY = ("none", "lognormal", "gradient")
N_BINS = 4
LOGNORMAL_SIGMA = 0.5   # dose dispersion across buildings under `lognormal` (coefficient of variation ≈ 0.53)


@dataclass(frozen=True)
class Scenario:
    design: str = "two_arm"
    n: int = 96                     # buildings in the sample (even)
    delta: float = 1.0              # dose of a complier, pp of the building's valid votes
    compliance: float = 1.0         # probability that an assigned building's side actually stays absent
    strata: str = "locality_pairs"
    heterogeneity: str = "none"
    mechanism: str = "switch"
    outcome: str = "likud"
    per_locality: int = 4           # buildings taken per sampled locality at most (locality schemes)
    covariates: bool = True         # baseline covariates in the analysis
    min_distance: float = 0.0       # metres between sampled buildings of one locality (0 = no constraint)
    inference: str = "t"            # t | randomization

    def validate(self) -> None:
        if self.design not in DESIGNS:
            raise ValueError(f"design must be one of {DESIGNS}")
        if self.strata not in STRATA:
            raise ValueError(f"strata must be one of {STRATA}")
        if self.heterogeneity not in HETEROGENEITY:
            raise ValueError(f"heterogeneity must be one of {HETEROGENEITY}")
        if self.mechanism not in MECHANISMS:
            raise ValueError(f"mechanism must be one of {MECHANISMS}")
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        if self.inference not in INFERENCE:
            raise ValueError(f"inference must be one of {INFERENCE}")
        if self.n < 4 or self.n % 2:
            raise ValueError("n must be an even number of at least 4")
        if self.strata.startswith("locality") and (self.per_locality < 2 or self.per_locality % 2):
            raise ValueError("per_locality must be an even number of at least 2")
        if not 0 <= self.compliance <= 1 or self.delta < 0:
            raise ValueError("compliance in [0, 1], delta ≥ 0")

    def label(self) -> str:
        return (f"{self.design} n={self.n} δ={self.delta} c={self.compliance} {self.strata} {self.heterogeneity} "
                f"{self.mechanism} {self.outcome}")


def public_seed(material: str) -> int:
    """A seed anybody can recompute: SHA-256 of a public string fixed in the pre-registration (first 8 bytes)."""
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def _scenario_seed(scen: Scenario, base: int) -> int:
    return public_seed(f"{base}|{scen.label()}|{scen.per_locality}|{scen.covariates}|{scen.min_distance}|{scen.inference}")


# ------------------------------------------------------------------------------------------- precomputation

@dataclass
class Prepared:
    """What a replicate needs from the panel, as arrays, built once per panel."""
    index: np.ndarray                          # site keys in building order
    locality: np.ndarray                       # locality code per building
    pos_by_locality: dict[int, np.ndarray]     # building positions per locality (≥ 2 buildings only)
    station_rows: list[np.ndarray]             # station row indices per building
    arr: StationArrays
    cov: np.ndarray                            # n × len(COVARIATES)
    coalition_prev: np.ndarray                 # pp, for the strata
    likud_prev: np.ndarray
    change_prev: np.ndarray
    share_prev: dict[str, np.ndarray]          # pp per outcome, previous election
    base_swing: dict[str, np.ndarray]          # pp per outcome, observed swing
    xy: np.ndarray                             # n × 2 (NaN where unknown)


def prepare(panel: Panel) -> Prepared:
    cached = panel.cache.get("prepared")
    if isinstance(cached, Prepared):
        return cached
    b = panel.buildings
    st = panel.stations
    site_pos = {k: i for i, k in enumerate(b.index)}
    rows_per: list[list[int]] = [[] for _ in range(len(b))]
    for r, key in enumerate(st["site_key"].to_numpy()):
        p = site_pos.get(key)
        if p is not None:
            rows_per[p].append(r)
    loc = b["locality_code"].to_numpy(int)
    pos_by_loc: dict[int, np.ndarray] = {}
    for code in np.unique(loc):
        pos = np.flatnonzero(loc == code)
        if len(pos) >= 2:
            pos_by_loc[int(code)] = pos
    prep = Prepared(index=b.index.to_numpy(), locality=loc, pos_by_locality=pos_by_loc,
                    station_rows=[np.asarray(r, dtype=int) for r in rows_per],
                    arr=StationArrays.from_stations(st, panel.lists),
                    cov=b[list(COVARIATES)].to_numpy(float), coalition_prev=b["coalition_prev"].to_numpy(float),
                    likud_prev=b["likud_prev"].to_numpy(float), change_prev=b["change_prev"].to_numpy(float),
                    share_prev={n: b[f"share_{n}_prev"].to_numpy(float) for n in OUTCOMES},
                    base_swing={n: b[f"swing_{n}"].to_numpy(float) for n in OUTCOMES},
                    xy=b[["x", "y"]].to_numpy(float))
    panel.cache["prepared"] = prep
    return prep


# ------------------------------------------------------------------------------------------- sampling

def _spaced(prep: Prepared, cands: np.ndarray, k: int, min_distance: float, rng: np.random.Generator) -> np.ndarray:
    """Up to k of `cands` in random order, skipping any closer than min_distance to one already taken."""
    order = cands[rng.permutation(len(cands))]
    if min_distance <= 0 or np.isnan(prep.xy[order]).any():
        return order[:k]
    taken: list[int] = []
    for i in order:
        if all(math.hypot(prep.xy[i, 0] - prep.xy[j, 0], prep.xy[i, 1] - prep.xy[j, 1]) >= min_distance for j in taken):
            taken.append(int(i))
            if len(taken) == k:
                break
    return np.asarray(taken, dtype=int)


def _sort_pairs(prep: Prepared, pos: np.ndarray) -> np.ndarray:
    """Positions ordered by the previous coalition share, trimmed to an even count."""
    pos = pos[np.argsort(prep.coalition_prev[pos], kind="stable")]
    return pos[: len(pos) - len(pos) % 2]


def draw_positions(prep: Prepared, scen: Scenario, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Building positions of one replicate and their stratum codes (0..G−1)."""
    if scen.strata in ("pairs", "bins"):
        pos = rng.choice(len(prep.index), size=min(scen.n, len(prep.index)), replace=False)
        pos = _sort_pairs(prep, pos)
        codes = np.arange(len(pos)) // 2 if scen.strata == "pairs" else (np.arange(len(pos)) * N_BINS) // len(pos)
        return pos, codes
    # locality schemes: localities in random order, up to `per_locality` buildings (an even number) from each,
    # until the sample is full — a city with many buildings contributes a block, a small one a pair
    parts: list[np.ndarray] = []
    strata: list[np.ndarray] = []
    taken = 0
    next_code = 0
    localities = list(prep.pos_by_locality)
    for j in rng.permutation(len(localities)):
        want = min(scen.per_locality, scen.n - taken)
        if want < 2:
            break
        chosen = _spaced(prep, prep.pos_by_locality[localities[j]], want, scen.min_distance, rng)
        if len(chosen) < 2:
            continue
        part = _sort_pairs(prep, chosen)
        parts.append(part)
        if scen.strata == "locality_pairs":
            strata.append(next_code + np.arange(len(part)) // 2)
            next_code += len(part) // 2
        else:
            strata.append(np.full(len(part), next_code))
            next_code += 1
        taken += len(part)
        if taken >= scen.n:
            break
    if not parts:
        raise ValueError("no locality offers two buildings for this scenario")
    return np.concatenate(parts), np.concatenate(strata)


def draw_sample(panel: Panel, scen: Scenario, rng: np.random.Generator) -> pd.DataFrame:
    """Buildings of one replicate with their stratum ids (a frame; `draw_positions` is the array form)."""
    scen.validate()
    prep = prepare(panel)
    pos, codes = draw_positions(prep, scen, rng)
    s = panel.buildings.iloc[pos].copy()
    s["stratum"] = codes
    return s


def assign_arms(codes: np.ndarray, scen: Scenario, rng: np.random.Generator) -> np.ndarray:
    """Permuted blocks inside each stratum: half of its buildings to each arm (±1 or 0/1), the odd one at random."""
    z = np.zeros(len(codes), dtype=int)
    lo = -1 if scen.design == "two_arm" else 0
    for s in np.unique(codes):
        pos = np.flatnonzero(codes == s)
        pos = pos[rng.permutation(len(pos))]
        half = len(pos) // 2
        z[pos[:half]] = 1
        z[pos[half: 2 * half]] = lo
        if len(pos) % 2:
            z[pos[-1]] = 1 if rng.random() < 0.5 else lo
    return z


def draw_doses(prep: Prepared, pos: np.ndarray, z: np.ndarray, scen: Scenario, rng: np.random.Generator,
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-building dose (pp), compliance and the realised arm d."""
    n = len(pos)
    delta = np.full(n, float(scen.delta))
    if scen.heterogeneity == "lognormal" and scen.delta > 0:
        delta = scen.delta * np.exp(rng.normal(-LOGNORMAL_SIGMA**2 / 2, LOGNORMAL_SIGMA, size=n))
    elif scen.heterogeneity == "gradient" and scen.delta > 0:
        # the switchable pool is the other side's baseline share: change voters where change stays absent,
        # Likud voters where the coalition stays absent; each arm is normalised to the mean dose δ
        pool = np.where(z < 0, prep.likud_prev[pos], prep.change_prev[pos])
        for arm in np.unique(z[z != 0]):
            mask = z == arm
            delta[mask] = scen.delta * pool[mask] / max(pool[mask].mean(), 1e-9)
    comply = rng.random(n) < scen.compliance
    return delta, comply, np.where(comply, z, 0)


# ------------------------------------------------------------------------------------------- one replicate

def _truth(delta_real: np.ndarray, arm: np.ndarray, design: str) -> float:
    if design == "two_arm":
        hi, lo = delta_real[arm > 0], delta_real[arm < 0]
        return float(((hi.mean() if hi.size else 0.0) - (lo.mean() if lo.size else 0.0)) / 2)
    hi, lo = delta_real[arm > 0], delta_real[arm == 0]
    return float((hi.mean() if hi.size else 0.0) - (lo.mean() if lo.size else 0.0))


def outcomes_after(prep: Prepared, pos: np.ndarray, d: np.ndarray, delta: np.ndarray, mechanism: str,
                   ) -> tuple[dict[str, np.ndarray], int]:
    """Building swings (pp) per outcome after the injection, and the number of ballots moved."""
    lengths = np.array([len(prep.station_rows[p]) for p in pos])
    rows = np.concatenate([prep.station_rows[p] for p in pos])
    owner = np.repeat(np.arange(len(pos)), lengths)
    valid, _voters, likud, change, moved = inject_arrays(prep.arr, rows, d[owner], delta[owner], mechanism)
    n = len(pos)
    v = np.bincount(owner, weights=valid, minlength=n)
    tot = {"likud": np.bincount(owner, weights=likud, minlength=n),
           "change": np.bincount(owner, weights=change.sum(axis=1), minlength=n)}
    tot["coalition"] = tot["likud"] + np.bincount(owner, weights=prep.arr.coalition_other[rows], minlength=n)
    swings = {name: 100 * tot[name] / v - prep.share_prev[name][pos] for name in OUTCOMES}
    return swings, int(moved.sum())


def run_once(panel: Panel, scen: Scenario, rng: np.random.Generator, alpha: float = 0.05) -> dict[str, Any]:
    """Sample, assign, inject, estimate; returns the estimates, the realised estimands and the sample size."""
    scen.validate()
    prep = prepare(panel)
    pos, codes = draw_positions(prep, scen, rng)
    z = assign_arms(codes, scen, rng)
    delta, comply, d = draw_doses(prep, pos, z, scen, rng)
    swings, moved = outcomes_after(prep, pos, d, delta, scen.mechanism)
    y = swings[scen.outcome]
    realised = y - prep.base_swing[scen.outcome][pos]
    est = fit_arrays(y, z.astype(float), d.astype(float), codes, prep.cov[pos] if scen.covariates else None,
                     alpha, scen.inference, rng)
    truth_itt = _truth(realised, z, scen.design)
    truth_cace = _truth(realised, d, scen.design) if (d != 0).any() else float("nan")
    return {"itt": est["itt"], "cace": est["cace"], "truth_itt": truth_itt, "truth_cace": truth_cace,
            "n": len(pos), "n_strata": int(codes.max()) + 1, "compliance_realised": float(comply.mean()),
            "moved": moved, "positions": pos, "z": z, "d": d, "delta": delta}


# ------------------------------------------------------------------------------------------- many replicates

def _summarise(kind: str, ests: list[Estimate], truths: np.ndarray, alpha: float, power_target: float) -> dict[str, Any]:
    coef = np.array([e.coef for e in ests], float)
    se = np.array([e.se for e in ests], float)
    p = np.array([e.p for e in ests], float)
    ok = np.isfinite(coef) & np.isfinite(se)
    cover = np.array([e.covers(t) for e, t in zip(ests, truths, strict=True)], bool)
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power_target)
    sign_ok = np.sign(coef) == np.sign(np.where(truths == 0, 1.0, truths))
    return {f"power_{kind}": float(np.mean((p < alpha) & ok)),
            f"power_signed_{kind}": float(np.mean((p < alpha) & ok & sign_ok)),
            f"coverage_{kind}": float(np.mean(cover[ok])) if ok.any() else float("nan"),
            f"bias_{kind}": float(np.mean(coef[ok] - truths[ok])) if ok.any() else float("nan"),
            f"rmse_{kind}": float(np.sqrt(np.mean((coef[ok] - truths[ok]) ** 2))) if ok.any() else float("nan"),
            f"mean_se_{kind}": float(np.mean(se[ok])) if ok.any() else float("nan"),
            f"sd_coef_{kind}": float(np.std(coef[ok], ddof=1)) if ok.sum() > 1 else float("nan"),
            f"mde_{kind}": float(z * np.mean(se[ok])) if ok.any() else float("nan"),
            f"truth_{kind}": float(np.nanmean(truths))}


def simulate(panel: Panel, scen: Scenario, reps: int = 1000, seed: int = 0, alpha: float = 0.05,
             power_target: float = 0.8) -> dict[str, Any]:
    """Power, MDE, coverage, bias and RMSE of ITT and CACE over `reps` replicates of one scenario."""
    scen.validate()
    rng = np.random.default_rng(_scenario_seed(scen, seed))
    t0 = time.time()
    itt: list[Estimate] = []
    cace: list[Estimate] = []
    t_itt, t_cace, ns, dofs, comp = [], [], [], [], []
    for _ in range(reps):
        r = run_once(panel, scen, rng, alpha)
        itt.append(r["itt"])
        cace.append(r["cace"])
        t_itt.append(r["truth_itt"])
        t_cace.append(r["truth_cace"])
        ns.append(r["n"])
        dofs.append(r["itt"].dof)
        comp.append(r["compliance_realised"])
    out: dict[str, Any] = {**asdict(scen), "reps": reps, "alpha": alpha, "seed": seed,
                           "n_realised": float(np.mean(ns)), "dof": float(np.mean(dofs)),
                           "compliance_realised": float(np.mean(comp)), "seconds": round(time.time() - t0, 1)}
    out.update(_summarise("itt", itt, np.array(t_itt, float), alpha, power_target))
    tc = np.array(t_cace, float)
    if np.isfinite(tc).any():
        keep = np.isfinite(tc)
        out.update(_summarise("cace", [e for e, k in zip(cace, keep, strict=True) if k], tc[keep], alpha, power_target))
    return out


def simulate_grid(panel: Panel, scenarios: Iterable[Scenario], reps: int = 1000, seed: int = 0, alpha: float = 0.05,
                  progress: Callable[[str, dict[str, Any]], None] | None = None) -> pd.DataFrame:
    """One row per scenario; the seed of each scenario is derived from the base seed and its own label."""
    rows = []
    for scen in scenarios:
        row = simulate(panel, scen, reps, seed, alpha)
        rows.append(row)
        if progress is not None:
            progress(scen.label(), row)
    return pd.DataFrame(rows)


def grid(designs: Sequence[str] = DESIGNS, ns: Sequence[int] = (24, 48, 96, 144, 200, 300, 400),
         deltas: Sequence[float] = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0), compliances: Sequence[float] = (1.0, 0.8, 0.6),
         base: Scenario | None = None) -> list[Scenario]:
    """The planning grid: every design × n × δ × compliance over one base scenario."""
    base = base or Scenario()
    return [replace(base, design=d, n=n, delta=delta, compliance=c)
            for d in designs for n in ns for delta in deltas for c in compliances]


# ------------------------------------------------------------------------------------------- closed forms

def normal_approx_n(delta: float, sigma: float, design: str = "two_arm", power: float = 0.8, alpha: float = 0.05) -> int:
    """Buildings needed under the normal approximation of the planning note (σ = SD of building swings).

    Two symmetric arms: the arms differ by 2δ, n per arm = 2 z² σ² / (2δ)², total = 2 × that.
    One arm against control: total = 2 × 2 z² σ² / δ² (four times as many).
    """
    if delta <= 0 or sigma <= 0:
        raise ValueError("delta and sigma must be positive")
    z = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
    if design == "two_arm":
        return int(2 * math.ceil(2 * z**2 * sigma**2 / (2 * delta) ** 2))
    if design == "one_arm":
        total: int = math.ceil(2 * 2 * z**2 * sigma**2 / delta**2)
        return total
    raise ValueError(f"design must be one of {DESIGNS}")


def normal_approx_power(delta: float, sigma: float, n: int, design: str = "two_arm", alpha: float = 0.05) -> float:
    """Power of the same normal approximation at a given total n (two-sided test)."""
    se = sigma / math.sqrt(n) if design == "two_arm" else 2 * sigma / math.sqrt(n)
    z = stats.norm.ppf(1 - alpha / 2)
    return float(stats.norm.sf(z - delta / se) + stats.norm.cdf(-z - delta / se))


def normal_approx_mde(sigma: float, n: int, design: str = "two_arm", power: float = 0.8, alpha: float = 0.05) -> float:
    se = sigma / math.sqrt(n) if design == "two_arm" else 2 * sigma / math.sqrt(n)
    return float((stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)) * se)
