"""T14 — dynamics of the count (2026 snapshots).

The CEC publishes the per-station file repeatedly while counting. Snapshots every 30 minutes give
three things no final file contains: (1) revisions — stations whose numbers changed after first
publication, with old and new values; (2) removals — stations that disappeared from the file;
(3) arrival order — which stations were counted late, and whether late stations differ
systematically (turnout, leader share) from early ones within the same sector. Revisions are
facts to be explained (corrections under §79(ה)–(ז) of the Elections Law — arithmetic, copying and
misattribution errors — are lawful and expected); the
test lists them so that every one can be checked against the published protocol scan.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.ingest.snapshots import Snapshot, arrival_table, diff, load_series
from electionforensics.model.features import Features


@register("T14")
def t14_count_dynamics(feat: Features, series: list[Snapshot] | None = None, as_of: str | None = None) -> TestResult:
    if feat.knesset != 26:
        return TestResult("T14", "Count dynamics", feat.knesset, "election", pd.DataFrame(), {"skipped": "no snapshots for this election"})
    skipped: list[str] = []
    series = load_series("expb", skipped) if series is None else series
    if as_of:
        series = [s for s in series if s.ts <= as_of]
    if not series:
        return TestResult("T14", "Count dynamics", feat.knesset, "revision", pd.DataFrame(), {"skipped": "no snapshots as of selected input"})
    parties = feat.parties
    revisions = []
    per_snapshot: list[dict[str, Any]] = []
    prev: Snapshot | None = None
    for s in series:
        t = s.table
        rec: dict[str, Any] = {"ts": s.ts, "stations": len(t), "voters": int(t["voters"].sum()),
                               "external_rows": int(t["is_external"].sum())}
        if prev is not None:
            d = diff(prev.table, t, parties)
            rec.update(added=len(d["added"]), removed=len(d["removed"]), changed_cells=len(d["changed"]),
                       changed_stations=int(d["changed"][["locality_code", "station"]].drop_duplicates().shape[0]) if len(d["changed"]) else 0)
            if len(d["changed"]):
                c = d["changed"].copy()
                c["ts_from"], c["ts_to"] = prev.ts, s.ts
                revisions.append(c)
            if len(d["removed"]):
                r = d["removed"][["locality_code", "locality_name", "station", "eligible", "voters", "valid"]].copy()
                r["field"], r["old"], r["new"] = "ROW_REMOVED", r["voters"], pd.NA
                r["ts_from"], r["ts_to"] = prev.ts, s.ts
                revisions.append(r[["locality_code", "station", "field", "old", "new", "locality_name", "ts_from", "ts_to"]])
        per_snapshot.append(rec)
        prev = s
    units = pd.concat(revisions, ignore_index=True) if revisions else pd.DataFrame(
        columns=["locality_code", "station", "field", "old", "new", "locality_name", "ts_from", "ts_to"])

    # arrival order vs. characteristics (within sector)
    arr = arrival_table(series)
    st = feat.stations[["locality_code", "station", "sector", "turnout", "leader_share"]]
    arr = arr.merge(st, on=["locality_code", "station"], how="left")
    arr["late"] = arr["first_seen_rank"] >= arr["first_seen_rank"].quantile(0.75)
    by_sector = (arr.groupby(["sector", "late"], observed=True)
                 .agg(n=("station", "size"), turnout=("turnout", "mean"), leader_share=("leader_share", "mean"))
                 .reset_index())
    summary = {
        "n_snapshots": len(series), "first": series[0].ts if series else None, "last": series[-1].ts if series else None,
        "per_snapshot": per_snapshot, "n_revised_cells": len(units),
        "n_revised_stations": int(units[["locality_code", "station"]].drop_duplicates().shape[0]) if len(units) else 0,
        "late_vs_early_by_sector": by_sector.replace({np.nan: None}).to_dict("records"),
    }
    summary["skipped_snapshots"] = "; ".join(skipped)
    return TestResult("T14", "Count dynamics", feat.knesset, "revision", units, summary,
                      notes=[f"skipped snapshot {s}" for s in skipped], extra={"arrival": arr})
