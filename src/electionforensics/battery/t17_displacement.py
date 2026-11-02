"""T17 — one-cell displacement: a fringe list holding the count that a zeroed major list should have.

Found while explaining the flags of the 26th Knesset count (docs/flags/k26-column-displacements.md).
When a station's protocol is copied into the results file, a count can land one row off: the Joint
List's 475 votes under ה (the column before ודם), Bennett's רק under רץ. The signature is a
conjunction that is rare in honest data: (a) a major list at exactly zero where it was expected to
score, and (b) a list below the major threshold holding a count of about that size at the same
station. Either half alone is common — mixed sites produce zeros, local lists produce fringe spikes —
which is why T10 missed most of these cases; together they are not.

Two expectations for the zeroed donor. *Peers*: the leave-one-out share of the other stations of the
same site (else of the locality) — the natural reading ("the building gave it 79 %"). *Profile*: the
pooled donor share of the `k_profile` stations of the same sector whose vote profile over the other
major lists is nearest to this station's — this separates a protocol slip from a station that simply
houses a different public (a secular box in a haredi school, a Hardal settlement where a right-wing
list scores zero), and it replaces the peers where a station has none (`ratio_basis`). Among the zeroed
majors the donor is the one with the strongest evidence inside the ratio band; a case needs the fringe
count to match the reference expectation within `ratio_band`, the profile expectation to be substantial
(`min_expected`), and both tails — the donor's zero under the profile expectation and the fringe count
under its national share — to be below 10^-`min_score`.

Units: one row per (station, fringe list with >= `min_count` votes) that has a zeroed major with a
usable expectation. Summary carries the number of cases and the displaced votes per million valid —
election-level statistics for the calibration layer, whose baseline over the final files 2003–2022 is
the historical rate of such slips left uncorrected.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features

COLUMNS = ["knesset", "locality_code", "locality_name", "station", "sector", "fringe", "votes", "fringe_national_share",
           "donor", "expected", "peer_level", "n_peers", "expected_profile", "ratio_basis", "ratio", "adjacent",
           "p_zero", "p_fringe", "score", "case"]
SCORE_CAP = 300.0


def _loo(x: np.ndarray, n: np.ndarray, key: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Leave-one-out share of x in n within key groups, and the number of other members."""
    df = pd.DataFrame({"x": x, "n": n, "g": key})
    sx = df.groupby("g")["x"].transform("sum").to_numpy(dtype=float) - x
    sn = df.groupby("g")["n"].transform("sum").to_numpy(dtype=float) - n
    cnt = df.groupby("g")["x"].transform("size").to_numpy() - 1
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where(sn > 0, sx / sn, np.nan)
    return q, cnt


def _profile_expectation(r: int, j: int, shares: np.ndarray, votes: np.ndarray, valid: np.ndarray, sector: np.ndarray,
                         donor_idx: list[int], k: int, min_valid: float) -> float:
    """Expected donor-j votes at station r from the k nearest stations (same sector) by profile over the other majors."""
    cols = [c for c in donor_idx if c != j]
    idx = np.arange(len(valid))
    pool = np.where((sector == sector[r]) & (valid >= min_valid) & (idx != r))[0]
    if len(pool) < 3:
        pool = np.where((valid >= min_valid) & (idx != r))[0]
        if len(pool) < 3:
            return float("nan")
    p = shares[pool][:, cols]
    p = p / np.maximum(p.sum(axis=1, keepdims=True), 1e-9)
    me = shares[r, cols]
    me = me / max(me.sum(), 1e-9)
    d = np.sqrt(((p - me) ** 2).sum(axis=1))
    near = pool[np.argsort(d)[:k]]
    return float(valid[r] * votes[near, j].sum() / max(valid[near].sum(), 1.0))


@register("T17")
def t17_displacement(feat: Features, min_count: int = 15, min_expected: float = 10.0, max_donor_votes: int = 0,
                     ratio_band: tuple[float, float] = (0.5, 2.0), min_peers_site: int = 2, k_profile: int = 10,
                     min_score: float = 6.0, profile_min_valid: float = 100.0) -> TestResult:
    st = feat.stations
    s = st[(st["valid"] > 0) & st["locality_code"].notna()]
    n = len(s)
    valid = s["valid"].to_numpy(dtype=float)
    order = list(feat.parties)  # file column order (alphabetical letters)
    cols = [f"v_{p}" for p in order]
    votes_mat = s[cols].to_numpy(dtype=float)
    tot = votes_mat.sum(axis=0)
    nat = tot / max(tot.sum(), 1.0)
    shares = votes_mat / np.maximum(valid[:, None], 1.0)
    major = set(feat.major)
    fringe_idx = [i for i, p in enumerate(order) if p not in major]
    donor_idx = [i for i, p in enumerate(order) if p in major]
    sector = s["sector"].astype(str).to_numpy()

    loc_key = s["locality_code"].astype(str).to_numpy()
    has_site = s["site_key"].notna().any()
    site_key = (s["site_key"].astype(str).where(s["site_key"].notna(), "none:" + pd.Series(loc_key, index=s.index))).to_numpy()

    # peers' expectation per donor: site LOO share, else locality LOO share, else national share
    exp = np.zeros((n, len(order)))
    level = np.full((n, len(order)), "national", dtype=object)
    npeers = np.zeros((n, len(order)), dtype=int)
    for j in donor_idx:
        x = votes_mat[:, j]
        q_loc, n_loc = _loo(x, valid, loc_key)
        ok = np.isfinite(q_loc)
        q, lvl, npr = np.where(ok, q_loc, nat[j]), np.where(ok, "locality", "national"), np.where(ok, n_loc, 0)
        if has_site:
            q_site, n_site = _loo(x, valid, site_key)
            use = (n_site >= min_peers_site) & np.isfinite(q_site)
            q, lvl, npr = np.where(use, q_site, q), np.where(use, "site", lvl), np.where(use, n_site, npr)
        exp[:, j] = valid * q
        level[:, j] = lvl
        npeers[:, j] = npr

    rows: list[dict[str, object]] = []
    for i_f in fringe_idx:
        for r in (int(i) for i in np.where(votes_mat[:, i_f] >= min_count)[0]):
            v_f = votes_mat[r, i_f]
            best_case: tuple[float, dict[str, object]] | None = None
            best_any: tuple[float, dict[str, object]] | None = None
            for j in donor_idx:
                if votes_mat[r, j] > max_donor_votes:
                    continue
                e_p, lvl_p = float(exp[r, j]), str(level[r, j])
                e_prof = _profile_expectation(r, j, shares, votes_mat, valid, sector, donor_idx, k_profile, profile_min_valid)
                prof_ok = bool(np.isfinite(e_prof))
                if lvl_p in ("site", "locality"):
                    e_ref, basis = e_p, "peers"
                elif prof_ok:
                    e_ref, basis = e_prof, "profile"
                else:
                    e_ref, basis = e_p, "national"
                if e_ref < min_expected:
                    continue
                e_zero = e_prof if prof_ok else e_p
                q0 = min(e_zero / valid[r], 1 - 1e-12)
                p_zero = float(stats.binom.cdf(votes_mat[r, j], int(valid[r]), q0))
                p_fr = float(stats.poisson.sf(v_f - 1, max(nat[i_f] * valid[r], 0.5)))
                with np.errstate(divide="ignore"):
                    lz = -np.log10(p_zero) if p_zero > 0 else SCORE_CAP
                    lf = -np.log10(p_fr) if p_fr > 0 else SCORE_CAP
                score = float(min(lz, lf, SCORE_CAP))
                ratio = v_f / e_ref
                in_band = ratio_band[0] <= ratio <= ratio_band[1]
                case = bool(in_band and score >= min_score and (not prof_ok or e_prof >= min_expected))
                rec: dict[str, object] = {
                    "knesset": int(s["knesset"].iloc[r]), "locality_code": s["locality_code"].iloc[r], "locality_name": s["locality_name"].iloc[r],
                    "station": s["station"].iloc[r], "sector": sector[r], "fringe": order[i_f], "votes": int(v_f),
                    "fringe_national_share": float(nat[i_f]), "donor": order[j], "expected": e_p, "peer_level": lvl_p,
                    "n_peers": int(npeers[r, j]), "expected_profile": float(e_prof), "ratio_basis": basis, "ratio": float(ratio),
                    "adjacent": abs(i_f - j) == 1, "p_zero": p_zero, "p_fringe": p_fr, "score": score, "case": case,
                }
                if case and (best_case is None or score > best_case[0]):
                    best_case = (score, rec)
                dist = abs(float(np.log(ratio)))
                if best_any is None or dist < best_any[0]:
                    best_any = (dist, rec)
            chosen = best_case or best_any
            if chosen is not None:
                rows.append(chosen[1])
    units = pd.DataFrame(rows, columns=COLUMNS).sort_values(["case", "votes"], ascending=[False, False]).reset_index(drop=True)
    cases = units[units["case"]]
    total_valid = float(valid.sum())
    displaced = int(cases["votes"].sum()) if len(cases) else 0
    summary = {
        "n_candidates": len(units), "n_cases": len(cases), "displaced_votes": displaced,
        "displaced_per_million": displaced / total_valid * 1e6 if total_valid else 0.0,
        "n_cases_adjacent": int(cases["adjacent"].sum()) if len(cases) else 0,
        "n_cases_site_or_locality": int(cases["peer_level"].isin(["site", "locality"]).sum()) if len(cases) else 0,
        "by_donor": {str(k): int(v) for k, v in cases.groupby("donor")["votes"].sum().items()} if len(cases) else {},
        "max_votes": int(cases["votes"].max()) if len(cases) else 0,
        "peer_level": "site" if has_site else "locality",
    }
    return TestResult("T17", "One-cell displacement into a fringe list", feat.knesset, "station_party", units, summary,
                      {"min_count": min_count, "min_expected": min_expected, "max_donor_votes": max_donor_votes,
                       "ratio_band": list(ratio_band), "min_peers_site": min_peers_site, "k_profile": k_profile,
                       "min_score": min_score, "profile_min_valid": profile_min_valid})
