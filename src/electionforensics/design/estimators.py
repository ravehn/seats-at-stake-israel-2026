"""The pre-specified analysis of the experiment, written once and used by every simulated replicate.

Outcome: the building's swing of a bloc share between the two elections (percentage points). Model: ordinary
least squares of the swing on the assignment, with fixed effects for the randomisation strata (pairs, bins or
localities) and the baseline covariates of the previous election; heteroskedasticity-robust (HC1) standard
errors with the degrees of freedom of the within-stratum regression. The unit of randomisation and of analysis
is the building, so there is no further clustering to do.

- ITT (intention to treat): the coefficient on the assigned arm. With the two symmetric arms coded −1/+1 the
  coefficient is δ itself, half the difference between the arms; with one arm against control (0/1) it is δ.
- CACE (complier average causal effect): the assigned arm instruments the realised arm (assignment × compliance
  observed in the photo census), exactly identified two-stage least squares with the same fixed effects and
  covariates; the first stage is the compliance rate.
- Inference: `t` — the robust t-test; `randomization` — the p-value of the ITT coefficient under re-randomisation
  of the arms inside the strata (exact under the sharp null, insensitive to the heavy tails of real swings); the
  confidence interval then still comes from the t approximation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

INFERENCE = ("t", "randomization")
N_PERM = 500


@dataclass(frozen=True)
class Estimate:
    coef: float
    se: float
    t: float
    p: float
    ci_lo: float
    ci_hi: float
    dof: int
    n: int
    first_stage: float = float("nan")

    def covers(self, truth: float) -> bool:
        return bool(self.ci_lo <= truth <= self.ci_hi)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def demean(m: np.ndarray, codes: np.ndarray, n_groups: int) -> np.ndarray:
    """Subtract the group mean from every column (Frisch–Waugh–Lovell for the stratum fixed effects)."""
    counts = np.bincount(codes, minlength=n_groups).astype(float)
    sums = np.vstack([np.bincount(codes, weights=m[:, j], minlength=n_groups) for j in range(m.shape[1])]).T
    out: np.ndarray = m - (sums / counts[:, None])[codes]
    return out


def _solve(y: np.ndarray, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xz = z.T @ x
    beta = np.linalg.solve(xz, z.T @ y)
    return beta, y - x @ beta, xz


def _estimate(y: np.ndarray, x: np.ndarray, z: np.ndarray, dof: int, alpha: float) -> Estimate:
    """Exactly identified IV (x = z gives OLS) for the first column with HC1 errors and t-based inference."""
    n = len(y)
    if dof < 1:
        raise ValueError(f"not enough degrees of freedom ({dof}) for the analysis")
    beta, e, xz = _solve(y, x, z)
    a_inv = np.linalg.inv(xz)
    meat = (z * (e**2)[:, None]).T @ z
    cov = (n / dof) * (a_inv @ meat @ a_inv.T)
    se = float(np.sqrt(max(cov[0, 0], 0.0)))
    coef = float(beta[0])
    t = coef / se if se > 0 else float("inf") * np.sign(coef)
    p = float(2 * stats.t.sf(abs(t), dof)) if np.isfinite(t) else 0.0
    q = float(stats.t.ppf(1 - alpha / 2, dof))
    return Estimate(coef, se, float(t), p, coef - q * se, coef + q * se, int(dof), int(n))


def permute_within(z: np.ndarray, codes: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A fresh assignment with the same arm counts inside every stratum (the randomisation the design used)."""
    out = z.copy()
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    bounds = np.flatnonzero(np.diff(sorted_codes)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(z)]])
    sizes = ends - starts
    if (sizes == sizes[0]).all():                       # equal blocks (pairs): one vectorised shuffle
        block = order.reshape(len(starts), sizes[0])
        out[block] = rng.permuted(z[block], axis=1)
        return out
    for s, e in zip(starts, ends, strict=True):
        idx = order[s:e]
        out[idx] = z[rng.permutation(idx)]
    return out


def fit_arrays(y: np.ndarray, z: np.ndarray, d: np.ndarray, codes: np.ndarray, x: np.ndarray | None,
               alpha: float = 0.05, inference: str = "t", rng: np.random.Generator | None = None,
               n_perm: int = N_PERM) -> dict[str, Estimate]:
    """ITT and CACE from arrays: outcome, assigned arm, realised arm, stratum codes (0..G−1) and covariates."""
    if inference not in INFERENCE:
        raise ValueError(f"inference must be one of {INFERENCE}")
    n_groups = int(codes.max()) + 1
    if (np.bincount(codes, minlength=n_groups) < 2).any():
        raise ValueError("every stratum needs at least two buildings")
    xc = np.empty((len(y), 0)) if x is None else np.asarray(x, dtype=float)
    m = demean(np.column_stack([y, z, d, xc]).astype(float), codes, n_groups)
    yc, zc, dc, xc = m[:, 0], m[:, 1], m[:, 2], m[:, 3:]
    k = 1 + xc.shape[1]
    dof = len(y) - n_groups - k
    z_mat = np.column_stack([zc, xc])
    itt = _estimate(yc, z_mat, z_mat, dof, alpha)
    fs = _estimate(dc, z_mat, z_mat, dof, alpha)
    if abs(fs.coef) < 1e-9:
        nan = float("nan")
        cace = Estimate(nan, nan, nan, 1.0, nan, nan, dof, len(y), fs.coef)
    else:
        c = _estimate(yc, np.column_stack([dc, xc]), z_mat, dof, alpha)
        cace = Estimate(c.coef, c.se, c.t, c.p, c.ci_lo, c.ci_hi, c.dof, c.n, fs.coef)
    if inference == "randomization":
        rng = rng or np.random.default_rng(0)
        count = 0
        for _ in range(n_perm):
            zp = demean(permute_within(z, codes, rng)[:, None].astype(float), codes, n_groups)[:, 0]
            beta, _e, _xz = _solve(yc, np.column_stack([zp, xc]), np.column_stack([zp, xc]))
            count += abs(beta[0]) >= abs(itt.coef) - 1e-12
        p = (1 + count) / (n_perm + 1)
        itt = Estimate(itt.coef, itt.se, itt.t, float(p), itt.ci_lo, itt.ci_hi, itt.dof, itt.n)
        cace = Estimate(cace.coef, cace.se, cace.t, float(p), cace.ci_lo, cace.ci_hi, cace.dof, cace.n, cace.first_stage)
    return {"itt": itt, "cace": cace}


def fit(sample: pd.DataFrame, outcome: str, covariates: Sequence[str] = (), strata: str = "stratum",
        alpha: float = 0.05, inference: str = "t", rng: np.random.Generator | None = None) -> dict[str, Estimate]:
    """ITT and CACE for one replicate held in a frame: the outcome, `z`, `d`, the stratum id and the covariates."""
    cov = [c for c in covariates if c in sample.columns]
    codes, _ = pd.factorize(sample[strata].to_numpy(), sort=True)
    x = sample[cov].to_numpy(dtype=float) if cov else None
    return fit_arrays(sample[outcome].to_numpy(dtype=float), sample["z"].to_numpy(dtype=float),
                      sample["d"].to_numpy(dtype=float), codes.astype(int), x, alpha, inference, rng)
