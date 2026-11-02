"""Coverage sets: where the article's premise is applied.

The premise of the article is a condition, not a finding: *if* the replacement of one bloc's outdoor advertising by
another's was applied wherever Likud has many voters, how many seats could it have moved? "Many voters" is read in two
ways chosen by the author (DECISIONS.md, 31.10.2026, rounds 2–3) and operationalised on the 2022 results, the last
election before the campaign, so that the set does not depend on the 2026 outcome it is used to analyse:

* **A** — localities where Likud was the first list in 2022 or trailed the first list by at most 5 percentage points.
* **B** — the non-haredi Jewish sector: localities classified Jewish by the Central Bureau of Statistics that are not
  haredi by voting history, plus the stations of the nine mixed cities whose Arab-list share in 2026 is below 20 %.
* **B⁺** — B with the haredi localities (appendix).

Further readings are kept for the appendix: the A rule with gaps of 3, 7 and 10 points, and reading I, calibrated on
the prime minister's campaign itinerary: Likud share 2022 ≥ τ, where τ is the lowest 2022 share among the localities of
his ordinary campaign stops (`seats.itinerary`, the press-sourced table `docs/article/itinerary_sources_k26.csv`,
Appendix G of the article; `ITINERARY_TAU` is the default when the table is not used). Sector labels are those of the
2022 features (`model.features.build_features`), i.e. the CBS file with haredi localities assigned from the 2021 vote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from electionforensics.model.features import Features
from electionforensics.seats.allocation import load_blocs

LIKUD = "מחל"
MIXED_ARAB_SHARE_MAX = 0.20
ITINERARY_TAU = 0.285


@dataclass
class Coverage:
    key: str
    label: str
    rule: str
    #: boolean over the index of the 2026 regular-station table
    station_mask: pd.Series
    localities: set[int]
    n_stations: int
    valid: int
    share_regular: float
    share_all: float
    by_sector: dict[str, float] = field(default_factory=dict)

    @property
    def n_localities(self) -> int:
        return len(self.localities)

    def summary(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "rule": self.rule, "n_localities": self.n_localities,
                "n_stations": self.n_stations, "valid": self.valid, "share_regular": self.share_regular,
                "share_all": self.share_all, "by_sector": self.by_sector}


MAIN_KEYS = ("A", "B")
APPENDIX_KEYS = ("B_haredi", "A3", "A7", "A10", "I")
ALL_KEYS = MAIN_KEYS + APPENDIX_KEYS


def locality_table(prev: Features, curr: Features) -> pd.DataFrame:
    """One row per locality present in both elections: 2022 shares, leader, gap to the leader, sector (2022 features)."""
    loc25 = prev.localities.copy()
    share_cols = [f"s_{p}" for p in prev.parties]
    sh = loc25[share_cols].astype(float)
    loc25["likud_prev"] = loc25[f"s_{LIKUD}"].astype(float)
    loc25["leader_prev"] = sh.idxmax(axis=1).str[2:]
    loc25["leader_share_prev"] = sh.max(axis=1)
    loc26 = curr.localities[["locality_code", "valid", "n_stations", "sector"]].rename(
        columns={"valid": "valid_curr", "n_stations": "n_stations_curr", "sector": "sector_curr"})
    out = loc25[["locality_code", "locality_name", "sector", "valid", "likud_prev", "leader_prev", "leader_share_prev"]].merge(
        loc26, on="locality_code", how="inner")
    out["gap_prev"] = out["leader_share_prev"] - out["likud_prev"]
    return out.rename(columns={"valid": "valid_prev"})


def _mask_from_localities(curr: Features, codes: set[int]) -> pd.Series:
    return curr.stations["locality_code"].isin(list(codes)).fillna(False).astype(bool)


def _mixed_jewish_stations(curr: Features, loc: pd.DataFrame, blocs: dict[str, list[str]]) -> pd.Series:
    mixed_codes = {int(c) for c in loc.loc[loc["sector"] == "mixed", "locality_code"]}
    st = curr.stations
    in_mixed = st["locality_code"].isin(list(mixed_codes)).fillna(False).astype(bool)
    cols = [f"s_{p}" for p in blocs["arab"] if f"s_{p}" in st.columns]
    arab = st[cols].astype(float).sum(axis=1) if cols else pd.Series(0.0, index=st.index)
    return in_mixed & (arab < MIXED_ARAB_SHARE_MAX)


def build_coverages(prev: Features, curr: Features, keys: tuple[str, ...] | list[str] = ALL_KEYS,
                    external_valid: float = 0.0, itinerary_tau: float = ITINERARY_TAU) -> dict[str, Coverage]:
    """Coverage sets over the 2026 regular stations. `prev` is the 2022 election, `curr` the 2026 snapshot;
    `external_valid` are the valid votes of the externally cast envelopes, which belong to no locality;
    `itinerary_tau` is the threshold of reading I (derived from the itinerary table by `seats.itinerary`)."""
    loc = locality_table(prev, curr)
    blocs = load_blocs(curr.knesset)
    st = curr.stations
    valid_regular = float(st["valid"].sum())
    valid_all = valid_regular + float(external_valid)
    mixed = _mixed_jewish_stations(curr, loc, blocs)

    def gap_rule(pp: int) -> tuple[set[int], str]:
        codes = {int(c) for c in loc.loc[loc["gap_prev"] <= pp / 100, "locality_code"]}
        return codes, f"Likud first in the locality in 2022 or within {pp} percentage points of the first list"

    specs: dict[str, tuple[set[int], pd.Series | None, str, str]] = {}
    for key, pp in (("A", 5), ("A3", 3), ("A7", 7), ("A10", 10)):
        codes, rule = gap_rule(pp)
        label = "A — Likud first or within 5 pp (2022)" if key == "A" else f"A with a {pp}-point gap"
        specs[key] = (codes, None, label, rule)
    jewish = {int(c) for c in loc.loc[loc["sector"] == "jewish", "locality_code"]}
    haredi = {int(c) for c in loc.loc[loc["sector"] == "haredi", "locality_code"]}
    specs["B"] = (jewish, mixed, "B — non-haredi Jewish sector",
                  "CBS Jewish localities that are not haredi by voting history, plus the stations of the mixed cities "
                  f"whose Arab-list share in 2026 is below {int(MIXED_ARAB_SHARE_MAX * 100)} %")
    specs["B_haredi"] = (jewish | haredi, mixed, "B⁺ — Jewish sector including haredi localities",
                         "B plus the haredi localities")
    itin = {int(c) for c in loc.loc[loc["likud_prev"] >= itinerary_tau, "locality_code"]}
    specs["I"] = (itin, None, f"I — Likud share 2022 ≥ {100 * itinerary_tau:.1f} % (campaign itinerary)",
                  f"Likud's 2022 share in the locality at least {100 * itinerary_tau:.1f} %, the lowest among the localities of "
                  "the prime minister's ordinary campaign stops (Appendix G)")

    out: dict[str, Coverage] = {}
    for key in keys:
        codes, extra, label, rule = specs[key]
        mask = _mask_from_localities(curr, codes)
        if extra is not None:
            mask = mask | extra
        sel = st[mask]
        valid = float(sel["valid"].sum())
        by_sector = {str(s): float(v) / valid_regular for s, v in sel.groupby("sector")["valid"].sum().items()}
        out[key] = Coverage(key, label, rule, mask, {int(c) for c in sel["locality_code"].dropna().unique()},
                            int(mask.sum()), int(valid), valid / valid_regular, valid / valid_all if valid_all else float("nan"),
                            by_sector)
    return out
