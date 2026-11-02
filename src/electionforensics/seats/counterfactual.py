"""The counterfactual transfer: remove a posited effect δ from Likud in the covered units and re-allocate the seats.

Main variant (the author's specification, 31.10.2026): in every covered unit — a locality, or the covered stations of a
locality — Likud's votes are reduced by δ percentage points of the unit's valid votes, and the removed votes are
returned to the lists of the change bloc in proportion to their actual shares in the same unit. Sensitivity variants:
the transfer is done station by station; the removed votes all return to a single list (each of the three lists whose
posters were taken down in the documented case in turn); and the turnout channel, in which the removed votes vanish
instead of moving. In every variant the removal is capped at the Likud votes present in the unit, and all other rows of
the station table — the other lists, the uncovered units and the externally cast envelopes — are untouched. The
threshold channel is deliberately not modelled (DECISIONS.md, 31.10, round 1): the recipients are lists that already sit
above the threshold, so the set of eligible lists is the factual one; the code checks this and records any deviation.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from electionforensics.seats.allocation import Allocation, allocate_totals, totals_from_stations
from electionforensics.seats.coverage import LIKUD


@dataclass(frozen=True)
class Variant:
    key: str
    unit: str            # "locality" | "station"
    recipients: str      # "proportional" | ballot letters of a single list | "none"
    label: str


def variants(change_lists: list[str], victims: tuple[str, ...] = ("דרך", "אמת", "רק")) -> list[Variant]:
    out = [
        Variant("main", "locality", "proportional", "locality units, removed votes return to the change-bloc lists pro rata"),
        Variant("station", "station", "proportional", "station units, pro rata return"),
    ]
    out += [Variant(f"victim_{v}", "locality", v, f"all removed votes return to one list ({v})") for v in victims if v in change_lists]
    out.append(Variant("turnout", "locality", "none", "turnout channel: removed votes vanish"))
    return out


@dataclass
class Transfer:
    delta_pp: float
    variant: Variant
    totals: dict[str, int]
    moved: int            # votes removed from Likud
    capped: int           # votes the rule asked to remove but Likud did not have
    gains: dict[str, int]
    n_units: int
    n_units_without_recipients: int

    def allocate(self, threshold: float, agreements: list[tuple[str, str]]) -> Allocation:
        return allocate_totals(self.totals, threshold, agreements)


def _largest_remainder(total: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Split each row's integer `total` across columns proportionally to `weights` (rows × k), integers, exact sums."""
    w = weights.astype(float)
    row_sum = w.sum(axis=1, keepdims=True)
    ok = row_sum[:, 0] > 0
    share = np.zeros_like(w)
    share[ok] = w[ok] / row_sum[ok]
    quota = share * total[:, None]
    base = np.floor(quota)
    rem = quota - base
    need = (total - base.sum(axis=1)).astype(int)
    order = np.argsort(-rem, axis=1, kind="stable")
    rank = np.empty_like(order)
    rows = np.arange(w.shape[0])[:, None]
    rank[rows, order] = np.arange(w.shape[1])[None, :]
    base += (rank < need[:, None]).astype(float)
    base[~ok] = 0.0
    result: np.ndarray = np.asarray(base, dtype=np.int64)
    return result


class TransferEngine:
    """Pre-aggregates the covered units once so that many δ values (the grid and the Monte-Carlo) are cheap."""

    def __init__(self, stations_all: pd.DataFrame, regular: pd.DataFrame, mask: pd.Series, parties: list[str],
                 change_lists: list[str], likud: str = LIKUD) -> None:
        self.parties = parties
        self.likud = likud
        self.change = [p for p in change_lists if f"v_{p}" in regular.columns]
        self.base_totals = totals_from_stations(stations_all, parties)
        sel = regular[mask]
        vcols = [f"v_{p}" for p in parties]
        self._by_station = sel[["locality_code", *vcols]].reset_index(drop=True)
        self._by_locality = sel.groupby("locality_code")[vcols].sum().reset_index()
        self.n_covered_stations = len(sel)

    def _units(self, unit: str) -> pd.DataFrame:
        return self._by_station if unit == "station" else self._by_locality

    def run(self, delta_pp: float, variant: Variant, localities: Collection[int] | None = None) -> Transfer:
        """The transfer at `delta_pp`; `localities` restricts it to a subset of the covered localities (coverage scaling)."""
        units = self._units(variant.unit)
        if localities is not None:
            units = units[units["locality_code"].isin(list(localities))]
        vcols = [f"v_{p}" for p in self.parties]
        valid = units[vcols].to_numpy(dtype=np.int64).sum(axis=1)
        likud = units[f"v_{self.likud}"].to_numpy(dtype=np.int64)
        asked = np.rint(delta_pp / 100.0 * valid).astype(np.int64)
        removed = np.minimum(asked, likud)
        capped = int((asked - removed).sum())
        gains: dict[str, int] = {}
        n_without = 0
        if variant.recipients == "proportional":
            w = units[[f"v_{p}" for p in self.change]].to_numpy(dtype=np.int64)
            has = w.sum(axis=1) > 0
            n_without = int((~has & (removed > 0)).sum())
            removed = np.where(has, removed, 0)
            split = _largest_remainder(removed, w)
            gains = {p: int(split[:, j].sum()) for j, p in enumerate(self.change)}
        elif variant.recipients != "none":
            gains = {variant.recipients: int(removed.sum())}
        totals = dict(self.base_totals)
        moved = int(removed.sum())
        totals[self.likud] = totals[self.likud] - moved
        for p, g in gains.items():
            totals[p] = totals.get(p, 0) + g
        return Transfer(delta_pp, variant, totals, moved, capped, gains, len(units), n_without)


def describe(t: Transfer, alloc: Allocation, factual: Allocation, blocs: dict[str, list[str]],
             frames: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """One grid cell as a flat record; `frames` are the alternative bloc frames of the appendix (name → ballot letters)."""
    b, b0 = alloc.bloc_seats(blocs), factual.bloc_seats(blocs)
    return {
        "delta": t.delta_pp, "variant": t.variant.key, "seats": alloc.seats, "blocs": b,
        "d_blocs": {k: b[k] - b0.get(k, 0) for k in b},
        "frames": {name: sum(alloc.seats.get(p, 0) for p in members) for name, members in (frames or {}).items()},
        "likud": alloc.seats.get(LIKUD, 0), "d_likud": alloc.seats.get(LIKUD, 0) - factual.seats.get(LIKUD, 0),
        "moved": t.moved, "capped": t.capped, "gains": t.gains,
        "share_moved_national": t.moved / alloc.valid if alloc.valid else float("nan"),
        "eligible_changed": sorted(alloc.eligible) != sorted(factual.eligible),
        "n_units": t.n_units, "n_units_without_recipients": t.n_units_without_recipients,
        "margin_votes": alloc.margin_votes,
    }
