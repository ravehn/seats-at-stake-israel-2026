"""Seats against the covered share of the vote (§ 5.5 of the article).

The premise does not fix how much of the Jewish sector the replacement covered; readings A and B are two points on a
range. This module applies the main-variant transfer to the top x % of the B⁺ vote — the covered localities taken in
the order of Likud's 2022 share, which is the premise's own ordering («where Likud has many voters»), and in random
orders for comparison — and records the coalition seats moved at every x for δ = 2 and δ = 5. Where the ordered and
the random curves coincide, the seat count is a property of the volume of votes covered and not of which localities
are chosen (`docs/research/arab-channel-seats-2026-11-01.md` § 2). Nothing here changes the grid; it reads the same
engine with a subset of its localities.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from electionforensics.seats.allocation import Allocation
from electionforensics.seats.counterfactual import TransferEngine, Variant
from electionforensics.seats.coverage import LIKUD, Coverage

#: the coverage set whose vote is the 100 % of the curve, the δ values, the covered shares, the random comparison orders
SCALING_COVERAGE = "B_haredi"
SCALING_DELTAS: tuple[float, ...] = (2.0, 5.0)
SCALING_FRACTIONS: tuple[float, ...] = tuple(round(0.1 * i, 1) for i in range(1, 11))
SCALING_N_RANDOM = 3
SCALING_SEED = 20261101
ORDER_LIKUD = "by Likud share 2022"


def scaling_orders(cov: Coverage, stations: pd.DataFrame, loc_table: pd.DataFrame, n_random: int = SCALING_N_RANDOM,
                   seed: int = SCALING_SEED) -> tuple[pd.DataFrame, dict[str, list[int]]]:
    """The covered localities with their 2026 valid votes at covered stations and Likud's 2022 share, and the orders.

    A locality absent from the 2022 file (no `likud_prev`) sorts last in the Likud order and is never dropped, so that
    the 100 % point of every order is the whole coverage set.
    """
    covered = stations[cov.station_mask]
    valid = covered.groupby("locality_code")["valid"].sum()
    tbl = pd.DataFrame({"locality_code": valid.index.astype(int), "valid_curr": valid.to_numpy(dtype=np.int64)})
    tbl = tbl.merge(loc_table[["locality_code", "likud_prev"]].drop_duplicates("locality_code"), on="locality_code", how="left")
    by_likud = tbl.sort_values(["likud_prev", "valid_curr"], ascending=[False, False], na_position="last")["locality_code"]
    orders: dict[str, list[int]] = {ORDER_LIKUD: [int(c) for c in by_likud]}
    rng = np.random.default_rng(seed)
    codes = tbl["locality_code"].to_numpy()
    for i in range(n_random):
        orders[f"random order {i + 1}"] = [int(c) for c in rng.permutation(codes)]
    return tbl, orders


def coverage_scaling(engine: TransferEngine, variant: Variant, cov: Coverage, stations: pd.DataFrame, loc_table: pd.DataFrame,
                     factual: Allocation, blocs: dict[str, list[str]], threshold: float, agreements: list[tuple[str, str]],
                     deltas: tuple[float, ...] = SCALING_DELTAS, fractions: tuple[float, ...] = SCALING_FRACTIONS,
                     n_random: int = SCALING_N_RANDOM, seed: int = SCALING_SEED) -> pd.DataFrame:
    """One row per order × covered share × δ: votes moved, Likud seats lost, coalition seats moved.

    For a target share x the covered localities are the longest prefix of the order whose cumulative share of the
    set's vote does not exceed x (at least one locality); the share actually covered is recorded next to the target.
    """
    tbl, orders = scaling_orders(cov, stations, loc_table, n_random, seed)
    valid_of = dict(zip(tbl["locality_code"].tolist(), tbl["valid_curr"].tolist(), strict=True))
    total = float(tbl["valid_curr"].sum())
    b0 = factual.bloc_seats(blocs)["coalition"]
    l0 = factual.seats.get(LIKUD, 0)
    rows: list[dict[str, Any]] = []
    for name, order in orders.items():
        cum = np.cumsum([valid_of[c] for c in order]) / total
        for frac in fractions:
            n = int(np.searchsorted(cum, frac + 1e-12, side="right"))
            chosen = order[: max(n, 1)]
            share = float(sum(valid_of[c] for c in chosen) / total)
            for delta in deltas:
                t = engine.run(delta, variant, localities=chosen)
                a = t.allocate(threshold, agreements)
                rows.append({"order": name, "target_share": frac, "covered_share_of_set": share, "n_localities": len(chosen),
                             "delta": delta, "votes_moved": t.moved, "likud_seats_lost": l0 - a.seats.get(LIKUD, 0),
                             "coalition_seats_moved": b0 - a.bloc_seats(blocs)["coalition"]})
    return pd.DataFrame(rows)


def spread_by_share(rows: pd.DataFrame) -> dict[str, int]:
    """Per δ, the largest difference in coalition seats moved between orders at the same target share."""
    out: dict[str, int] = {}
    for delta in sorted(float(d) for d in rows["delta"].unique()):
        by = rows[rows["delta"] == delta].groupby("target_share")["coalition_seats_moved"]
        out[f"{delta:g}"] = int((by.max() - by.min()).max())
    return out
