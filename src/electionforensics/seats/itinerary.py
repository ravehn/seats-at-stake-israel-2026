"""The prime minister's campaign itinerary: the press-sourced table behind reading I (Appendix G of the article).

`docs/article/itinerary_sources_k26.csv` is hand-curated evidence: every in-person appearance of the prime minister
between the dissolution of the Knesset (17.07.2026) and election night (28.10.2026) that at least one dated press report
or dated agency photograph confirms, with the report's URL. The column `in_rule` marks the ordinary campaign stops:
the appearances in Jerusalem (home, office and party headquarters), Tel Aviv-Yafo (a staged launch and a market in a
Likud neighbourhood of a non-Likud city), Eli and Beit Shemesh (visited for their religious sector) and the state
ceremony at Tel Hai are listed with the reason for their exclusion. The rule of reading I is «Likud share 2022 ≥ τ»
with τ the lowest 2022 share among the ordinary stops; this module joins the table with the 2022 and 2026 results and
derives τ, so that the number in the article follows the table and not a constant.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import pandas as pd

from electionforensics.config import ROOT
from electionforensics.model.features import Features
from electionforensics.seats.coverage import LIKUD

ITINERARY_FILE = ROOT / "docs" / "article" / "itinerary_sources_k26.csv"
URL_COLUMNS = ("url_1", "url_2", "url_3")
#: the campaign window: the Knesset's dissolution (law approved 16.07, formal dissolution 17.07.2026) to election night
WINDOW = ("2026-07-17", "2026-10-28")


def load_itinerary(path: Path = ITINERARY_FILE) -> pd.DataFrame:
    """The hand-curated table as written (strings; `locality_code` nullable integer; `in_rule` boolean)."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    df = pd.DataFrame(rows)
    df["locality_code"] = pd.to_numeric(df["locality_code"].replace("", pd.NA), errors="raise").astype("Int64")
    df["in_rule"] = df["in_rule"].str.strip().str.lower().eq("yes")
    return df


def itinerary_table(stops: pd.DataFrame, prev: Features, curr: Features) -> pd.DataFrame:
    """One row per appearance with the locality's 2022 and 2026 Likud shares and 2026 valid votes (regular stations).

    `prev` is the election before the campaign (2022), `curr` the election the article analyses (2026). An appearance
    whose locality is not in the Committee's files (Tel Hai) keeps empty shares.
    """
    p = prev.localities[["locality_code", "locality_name", "valid", f"s_{LIKUD}"]].rename(
        columns={"locality_name": "cec_name", "valid": "valid_prev", f"s_{LIKUD}": "likud_share_prev"})
    c = curr.localities[["locality_code", "valid", f"s_{LIKUD}"]].rename(
        columns={"valid": "valid_curr", f"s_{LIKUD}": "likud_share_curr"})
    out = stops.merge(p, on="locality_code", how="left").merge(c, on="locality_code", how="left")
    for col in ("likud_share_prev", "likud_share_curr"):
        out[col] = out[col].astype(float)
    return out


def _floor_tenth(share: float) -> float:
    """A share rounded down to a tenth of a percentage point (0.2852944 → 0.285)."""
    return math.floor(1000 * share + 1e-9) / 1000


def itinerary_summary(table: pd.DataFrame, curr: Features, prev: Features) -> dict[str, Any]:
    """τ and the numbers the article states about the ordinary stops, plus the alternative with the sectoral stops.

    `share_at(tau)`: the share of the 2026 regular valid vote in localities whose 2022 Likud share is at least `tau`
    (the rule of reading I evaluated on the same tables, so that the appendix can show the alternative threshold).
    """
    ordinary = table[table["in_rule"] & table["locality_code"].notna()].drop_duplicates("locality_code")
    shares = ordinary["likud_share_prev"].astype(float)
    # τ is the lowest share rounded DOWN to a tenth of a percentage point: the lowest stop is in the set by construction
    # and the threshold the article prints is the threshold applied
    tau = _floor_tenth(float(shares.min()))
    low = ordinary.iloc[int(shares.to_numpy().argmin())]
    high = ordinary.iloc[int(shares.to_numpy().argmax())]
    # the alternative with the sectoral stops the editor excluded: the lowest share among every confirmed appearance
    with_code = table[table["locality_code"].notna()].drop_duplicates("locality_code")
    tau_all = _floor_tenth(float(with_code["likud_share_prev"].astype(float).min()))
    yokneam = ordinary.loc[ordinary["locality_en"].str.startswith("Yokneam")]
    rank = int((shares > float(yokneam["likud_share_prev"].iloc[0])).sum()) + 1 if len(yokneam) else None

    def share_at(t: float) -> dict[str, Any]:
        codes = set(prev.localities.loc[prev.localities[f"s_{LIKUD}"].astype(float) >= t, "locality_code"].astype(int))
        st = curr.stations
        sel = st[st["locality_code"].isin(list(codes)).fillna(False).astype(bool)]
        return {"tau": t, "n_localities": int(sel["locality_code"].nunique()), "n_stations": len(sel),
                "share_regular": float(sel["valid"].sum()) / float(st["valid"].sum())}

    return {
        "file": ITINERARY_FILE.name,
        "window": list(WINDOW),
        "n_appearances": len(table),
        "n_ordinary_appearances": int(table["in_rule"].sum()),
        "n_ordinary_localities": len(ordinary),
        "n_excluded_appearances": int((~table["in_rule"]).sum()),
        "tau": tau,
        "min": {"locality_en": str(low["locality_en"]), "share_prev": float(low["likud_share_prev"])},
        "max": {"locality_en": str(high["locality_en"]), "share_prev": float(high["likud_share_prev"])},
        "median_share_prev": float(shares.median()),
        "yokneam_rank": rank,
        "yokneam_share_prev": float(yokneam["likud_share_prev"].iloc[0]) if len(yokneam) else None,
        "rule_at_tau": share_at(tau),
        "tau_with_sectoral_stops": tau_all,
        "rule_at_tau_with_sectoral_stops": share_at(tau_all),
        "visited_share_regular": float(curr.stations[curr.stations["locality_code"].isin(
            list(with_code["locality_code"].astype(int)))]["valid"].sum()) / float(curr.stations["valid"].sum()),
        "ordinary_share_regular": float(curr.stations[curr.stations["locality_code"].isin(
            list(ordinary["locality_code"].astype(int)))]["valid"].sum()) / float(curr.stations["valid"].sum()),
    }


def itinerary_records(table: pd.DataFrame) -> list[dict[str, Any]]:
    """The table as JSON-ready rows for results.json and the template (shares as floats, missing as None)."""
    recs: list[dict[str, Any]] = []
    for _, r in table.iterrows():
        rec: dict[str, Any] = {}
        for key, v in r.items():
            k = str(key)
            if not isinstance(v, (list, tuple)) and pd.isna(v):
                rec[k] = None
            elif k == "locality_code":
                rec[k] = int(v)
            elif k in ("likud_share_prev", "likud_share_curr"):
                rec[k] = float(v)
            elif k in ("valid_prev", "valid_curr"):
                rec[k] = int(v)
            elif k == "in_rule":
                rec[k] = bool(v)
            else:
                rec[k] = str(v)
        rec["urls"] = [rec[c] for c in URL_COLUMNS if rec.get(c)]
        recs.append(rec)
    return recs
