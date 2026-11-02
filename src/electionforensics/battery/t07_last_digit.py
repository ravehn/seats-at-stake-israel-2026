"""T07 — last digits of vote counts (Beber & Scacco 2012; Medzihorsky 2015).

When a count is large enough (≥ `min_count`), its last digit carries no information about the
station and should be uniform over 0–9. People who invent numbers do not produce uniform last
digits: they over-use 0 and 5 and avoid repeats and extremes. The test pools every list's count
at every regular station — overall, by sector, by list and by locality — and reports (a) the
chi-square statistic against uniformity, (b) the share of digits 0 and 5 against the expected
0.2 with a binomial z, (c) the *spike* of the values ending in 0 or 5 above their immediate
neighbours in value space (v − 1 and v + 1), with a z on the Poisson scale, and (d) an effect
size: the total-variation distance of the digit distribution from uniform, the smallest share of
counts that would have to come from a non-uniform ("hand-made") process to produce it (the
minimal-mixture bound behind Medzihorsky's latent-class formulation).

Why the spike statistic: in Israel most counts ≥ 100 lie in the 100–200 band, where the number of
counts falls steeply with the value; the digit 0 (start of a decade) is then a little more frequent
than the digit 9 (end of it) for purely arithmetic reasons, and the chi-square against uniformity
rejects in every election since 2003 (checked on the files). The spike compares each round value
with the two values next to it — including 99 next to 100, which a digit-only statistic would
drop — so a smooth gradient of any shape cancels and only genuine excess at round numbers remains.
Stations have too few large counts to be judged alone, so the unit table is per locality; the
election-level numbers are what the calibration compares with 2003–2022.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from electionforensics.battery.base import TestResult, register
from electionforensics.model.features import Features

DIGITS = np.arange(10)
_EMPTY = {"n": 0, "chi2": float("nan"), "p_chi2": float("nan"), "tv": float("nan"), "share_05": float("nan"),
          "z_05": float("nan"), "p_05": float("nan"), "spike_05": float("nan"), "z_spike05": float("nan"), "n_neighbours": 0}


def digit_stats(counts: np.ndarray, min_count: int = 100) -> dict[str, float]:
    """Last-digit statistics of a vector of counts; counts below `min_count` only serve as neighbours of round values."""
    c = np.asarray(counts, dtype=np.int64)
    c = c[c >= 0]
    big = c[c >= min_count]
    n = int(big.size)
    if n == 0:
        return dict(_EMPTY)
    d = big % 10
    cnt = np.bincount(d, minlength=10).astype(float)
    freq = cnt / n
    chi2 = float(((cnt - n / 10) ** 2 / (n / 10)).sum())
    p = float(stats.chi2.sf(chi2, 9))
    tv = float(np.abs(freq - 0.1).sum() / 2)
    k05 = int(cnt[0] + cnt[5])
    share = k05 / n
    z = (k05 - 0.2 * n) / np.sqrt(n * 0.2 * 0.8)
    p05 = float(stats.binom.sf(k05 - 1, n, 0.2))  # one-sided: excess of 0/5
    # spike: values ending in 0/5 (≥ min_count) against their neighbours v ± 1 in value space
    nb = (((c + 1) % 5 == 0) & (c + 1 >= min_count)) | (((c - 1) % 5 == 0) & (c - 1 >= min_count))
    n_nb = int(nb.sum())
    excess = k05 - n_nb / 2
    var = k05 + n_nb / 4
    z_spike = float(excess / np.sqrt(var)) if var > 0 else float("nan")
    return {"n": n, "chi2": chi2, "p_chi2": p, "tv": tv, "share_05": float(share), "z_05": float(z), "p_05": p05,
            "spike_05": float(excess / n), "z_spike05": z_spike, "n_neighbours": n_nb,
            **{f"f{i}": float(freq[i]) for i in DIGITS}}


@register("T07")
def t07_last_digit(feat: Features, min_count: int = 100, min_digits_locality: int = 50) -> TestResult:
    st = feat.stations
    vcols = [f"v_{p}" for p in feat.parties]
    long = st[["locality_code", "locality_name", "sector", "station", *vcols]].melt(
        id_vars=["locality_code", "locality_name", "sector", "station"], var_name="party", value_name="count")
    long["party"] = long["party"].str[2:]
    long = long[long["count"].notna()].copy()
    long["count"] = long["count"].astype("int64")
    all_counts = long["count"].to_numpy(dtype=np.int64)

    summary: dict[str, Any] = {"min_count": min_count, "n_stations": int(st.shape[0]), **digit_stats(all_counts, min_count)}
    summary["by_sector"] = {str(sec): digit_stats(g["count"].to_numpy(dtype=np.int64), min_count) for sec, g in long.groupby("sector")}
    parties = pd.DataFrame([{"party": p, **digit_stats(g["count"].to_numpy(dtype=np.int64), min_count)} for p, g in long.groupby("party")])
    if not parties.empty:
        parties = parties[parties["n"] > 0].sort_values("n", ascending=False).reset_index(drop=True)
    # voters' last digit as a separate series: the one number a committee writes before any list
    v = st["voters"].dropna().astype("int64").to_numpy(dtype=np.int64)
    summary["voters_last_digit"] = digit_stats(v, min_count)

    rows = []
    for (code, name, sec), g in long.groupby(["locality_code", "locality_name", "sector"]):
        ds = digit_stats(g["count"].to_numpy(dtype=np.int64), min_count)
        if ds["n"] < min_digits_locality:
            continue
        rows.append({"knesset": feat.knesset, "locality_code": code, "locality_name": name, "sector": sec, **ds})
    units = pd.DataFrame(rows)
    if not units.empty:
        # the locality score is the spike z, floored at 0: only an excess of round counts is of interest
        units["score"] = units["z_spike05"].clip(lower=0)
        units = units.sort_values("score", ascending=False).reset_index(drop=True)
        summary["n_localities"] = len(units)
        summary["localities_p_below_1e-3"] = int((units["p_chi2"] < 1e-3).sum())
        summary["localities_p_below_1e-6"] = int((units["p_chi2"] < 1e-6).sum())
        summary["localities_spike_z_above_4"] = int((units["z_spike05"] > 4).sum())
        summary["expected_localities_p_below_1e-3"] = float(len(units) * 1e-3)
    else:
        summary["n_localities"] = 0
    return TestResult("T07", "Last digits of counts", feat.knesset, "locality", units, summary,
                      {"min_count": min_count, "min_digits_locality": min_digits_locality}, extra={"parties": parties})
