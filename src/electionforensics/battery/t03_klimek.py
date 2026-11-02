"""T03 — Klimek's parametric fit: how much incremental and extreme fraud would reproduce the fingerprint.

Klimek, Yegorov, Hanel and Thurner (PNAS 2012) model each unit's turnout a and winner share v as
draws from the clean distributions (normal, with the observed centre and spread) and let two
mechanisms act on them: with probability f_i a fraction x ~ |N(0, θ)| of the non-voters is added as
winner ballots ("incremental" fraud: turnout and winner share both rise), and with probability f_e a
fraction x ~ |N(1, 0.075)| ("extreme" fraud: the unit reports almost everyone voting, almost all for
the winner). The estimates are the (f_i, f_e, θ) whose simulated 2D histogram is closest, in least
squares, to the observed turnout × winner-share histogram.

Known weaknesses (Mebane 2013; Klimek et al.'s reply): the clean distributions are not normal and
multimodal electorates look like fraud. Here the fit therefore runs per sector, with the sector's own
leading list and a *left-sided* estimate of the clean centre and spread (the mode and the spread of
the values below it — the side that stuffing cannot touch), on 20 × 20 panels (the 40 × 40 of T02
coarsened to halve the Monte-Carlo noise of the objective); the objective
of the clean model (f_i = f_e = 0) is reported next to the best fit, so that the reader sees how much
the fraud parameters actually improve it; and a crude support range (all grid points within 5 % of
the best objective) replaces a confidence interval. Estimates are read only against the same
sector's values in previous elections (calibration), never as a count of fraudulent stations.

Identifiability (checked on synthetic elections): the model sees stuffing only when the added mass
moves stations by several clean standard deviations — a 10-point rise of turnout at a third of the
stations, inside a sector whose turnout spreads by 7 points, is absorbed by the clean centre and
spread and returns f_i ≈ 0. Subtle local stuffing is the business of T09 and T12; T03 answers the
question of gross, country- or sector-wide manipulation of the kind documented in Russia 2011.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.battery.t02_fingerprint import BINS
from electionforensics.model.features import Features

THETAS = (0.15, 0.3, 0.5)
EXTREME_MEAN, EXTREME_SD = 1.0, 0.075
MIN_SPREAD = 0.01
#: Pseudo-sectors that are mixtures of electorates by construction: the model reads the mixture as fraud
#: (f_i at the grid edge in every election since 2009), so their estimates are kept for transparency only.
MIXTURE_SECTORS = ("all", "mixed")
MIN_IMPROVEMENT = 0.10   # below this the objective is flat and the estimates are not identified
MAX_SUPPORT_WIDTH = 0.30
FIT_BINS = BINS[::2]  # 5-point bins: the objective is compared on a coarser grid than the T02 panels


def _left_sided(x: np.ndarray) -> tuple[float, float]:
    """Centre = mode of a smoothed 1-point histogram; spread from the values below the mode (fraud only adds mass above)."""
    x = x[np.isfinite(x)]
    if x.size < 20:
        return float(np.median(x)) if x.size else float("nan"), MIN_SPREAD
    edges = np.linspace(0, 1, 401)  # 0.25-point bins, smoothed over ±2 points
    h, _ = np.histogram(np.clip(x, 0, 1), bins=edges)
    kernel = np.ones(17) / 17
    hs = np.convolve(h, kernel, mode="same")
    mode = float((edges[:-1] + edges[1:])[int(hs.argmax())] / 2)
    left = x[x <= mode]
    if left.size < 10:
        return mode, max(float(np.std(x)), MIN_SPREAD)
    # for a normal half: P(x <= mode - sigma | x <= mode) = 0.16 / 0.5
    sigma = mode - float(np.quantile(left, 0.32))
    return mode, max(sigma, MIN_SPREAD)


def _hist(a: np.ndarray, v: np.ndarray, bins: np.ndarray = FIT_BINS) -> np.ndarray:
    h, _, _ = np.histogram2d(np.clip(a, 0, 1), np.clip(v, 0, 1), bins=[bins, bins])
    return np.asarray(h / max(h.sum(), 1.0))


def fit_klimek(a: np.ndarray, v: np.ndarray, f_i_grid: np.ndarray, f_e_grid: np.ndarray, thetas: tuple[float, ...] = THETAS,
               n_sim: int = 40_000, n_rep: int = 2, seed: int = 0, support: float = 0.05) -> dict[str, Any]:
    """Least-squares fit of (f_i, f_e, θ) to the 2D histogram of turnout a and winner share v."""
    rng = np.random.default_rng(seed)
    target = _hist(a, v)
    mu_a, sd_a = _left_sided(a)
    mu_v, sd_v = _left_sided(v)
    best: dict[str, Any] = {"obj": np.inf}
    grid_rows = []
    obj_clean = np.nan
    for theta in thetas:
        # one set of clean draws per replication, shared by every (f_i, f_e): only the masks change
        reps = []
        for _ in range(n_rep):
            ca = np.clip(rng.normal(mu_a, sd_a, n_sim), 0, 1)
            cv = np.clip(rng.normal(mu_v, sd_v, n_sim), 0, 1)
            u = rng.random(n_sim)
            x_inc = np.clip(np.abs(rng.normal(0, theta, n_sim)), 0, 1)
            x_ext = np.clip(np.abs(rng.normal(EXTREME_MEAN, EXTREME_SD, n_sim)), 0, 1)
            reps.append((ca, cv, u, x_inc, x_ext))
        for f_i in f_i_grid:
            for f_e in f_e_grid:
                obj = 0.0
                added = 0.0
                for ca, cv, u, x_inc, x_ext in reps:
                    x = np.where(u < f_i, x_inc, np.where(u < f_i + f_e, x_ext, 0.0))
                    a2 = ca + x * (1 - ca)
                    with np.errstate(divide="ignore", invalid="ignore"):
                        v2 = np.where(a2 > 0, (cv * ca + x * (1 - ca)) / a2, cv)
                    obj += float(((_hist(a2, v2) - target) ** 2).sum())
                    added += float((x * (1 - ca)).sum() / max(a2.sum(), 1e-9))
                obj /= n_rep
                added /= n_rep
                grid_rows.append({"theta": theta, "f_i": float(f_i), "f_e": float(f_e), "objective": obj, "added_share": added})
                if f_i == 0 and f_e == 0 and np.isnan(obj_clean):
                    obj_clean = obj
                if obj < best["obj"]:
                    best = {"obj": obj, "f_i": float(f_i), "f_e": float(f_e), "theta": theta, "added_share": added}
    grid = pd.DataFrame(grid_rows)
    near = grid[grid["objective"] <= best["obj"] * (1 + support)]
    return {"mu_a": mu_a, "sd_a": sd_a, "mu_v": mu_v, "sd_v": sd_v, "f_i": best["f_i"], "f_e": best["f_e"], "theta": best["theta"],
            "f_i_lo": float(near["f_i"].min()), "f_i_hi": float(near["f_i"].max()), "f_e_lo": float(near["f_e"].min()), "f_e_hi": float(near["f_e"].max()),
            "obj_best": best["obj"], "obj_clean": float(obj_clean),
            "improvement": 1 - best["obj"] / obj_clean if obj_clean and obj_clean > 0 else float("nan"),
            "added_share": best["added_share"], "grid": grid}


@register("T03")
def t03_klimek(feat: Features, min_eligible: int = 100, min_units: int = 200, f_i_step: float = 0.02, f_i_max: float = 0.5,
               f_e_step: float = 0.01, f_e_max: float = 0.1, thetas: tuple[float, ...] = THETAS, n_sim: int = 40_000,
               n_rep: int = 2, seed: int = 0) -> TestResult:
    st = feat.stations
    ok = (st["eligible"].fillna(0) >= min_eligible) & st["turnout"].notna() & (st["valid"] > 0) & (st["turnout"].fillna(2) <= 1)
    s = st[ok]
    if s.empty:
        return TestResult("T03", "Klimek parametric fit", feat.knesset, "sector", pd.DataFrame(), {"skipped": "no turnout available"})
    t = s["turnout"].astype(float).to_numpy()
    sec = s["sector"].to_numpy()
    f_i_grid = np.round(np.arange(0, f_i_max + 1e-9, f_i_step), 4)
    f_e_grid = np.round(np.arange(0, f_e_max + 1e-9, f_e_step), 4)
    rows = []
    grids = []
    for name in ["all", *sorted(set(sec))]:
        m = np.ones_like(t, dtype=bool) if name == "all" else (sec == name)
        if m.sum() < min_units:
            continue
        vsum = s.loc[m, [f"v_{p}" for p in feat.parties]].sum()
        leader = str(vsum.idxmax())[2:]
        y = s[f"s_{leader}"].astype(float).to_numpy()
        r = fit_klimek(t[m], y[m], f_i_grid, f_e_grid, thetas, n_sim, n_rep, seed)
        grid = r.pop("grid")
        grid.insert(0, "sector", name)
        grids.append(grid)
        r["mixture"] = name in MIXTURE_SECTORS
        r["identified"] = bool(not r["mixture"] and r["improvement"] >= MIN_IMPROVEMENT and (r["f_i_hi"] - r["f_i_lo"]) <= MAX_SUPPORT_WIDTH)
        rows.append({"knesset": feat.knesset, "sector": name, "n": int(m.sum()), "leader": leader, **r})
    units = pd.DataFrame(rows)
    by_sector = {str(r["sector"]): {k: r[k] for k in ("n", "leader", "f_i", "f_e", "theta", "f_i_lo", "f_i_hi", "f_e_lo", "f_e_hi",
                                                      "improvement", "added_share", "obj_best", "obj_clean", "mixture", "identified")} for r in rows}
    summary = {"leader": feat.leader, "by_sector": by_sector,
               "f_i_all": by_sector.get("all", {}).get("f_i"), "f_e_all": by_sector.get("all", {}).get("f_e")}
    params = {"min_eligible": min_eligible, "min_units": min_units, "f_i_grid": [float(f_i_grid[0]), float(f_i_grid[-1]), f_i_step],
              "f_e_grid": [float(f_e_grid[0]), float(f_e_grid[-1]), f_e_step], "thetas": list(thetas), "n_sim": n_sim, "n_rep": n_rep, "seed": seed}
    return TestResult("T03", "Klimek parametric fit", feat.knesset, "sector", units, summary, params,
                      extra={"grid": pd.concat(grids, ignore_index=True) if grids else pd.DataFrame()})
