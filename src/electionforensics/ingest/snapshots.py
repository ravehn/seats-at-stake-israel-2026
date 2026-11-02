"""The 2026 count as a time series: every timestamped expb snapshot, and the differences between them.

A snapshot file is trusted only after it parses: the CEC host answers some requests with an HTML
"page not found" document that the collector once saved under a `.csv` name (EF-003). Such files stay
in `data/raw` as evidence of the host's state at that minute but are skipped here and reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from electionforensics.ingest.cec import ElectionData, load_election
from electionforensics.ingest.registry import snapshot_files

KEY = ["locality_code", "station"]
MIN_ROWS = 10


def _reject_reason(p: Path) -> str | None:
    """Why a snapshot file cannot be used as data, or None when it looks like a CEC result file."""
    head = p.read_bytes()[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if not head:
        return "empty file"
    if head.startswith(b"<"):
        return "HTML document, not a CSV"
    return None


def load_snapshot(p: Path) -> tuple[Snapshot | None, str | None]:
    """Parse one snapshot; returns (snapshot, None) or (None, reason) when the file is not usable."""
    reason = _reject_reason(p)
    if reason:
        return None, reason
    try:
        d = load_election(26, p)
    except (pd.errors.ParserError, UnicodeDecodeError, KeyError, ValueError) as exc:
        return None, f"unparseable: {type(exc).__name__}"
    if len(d.stations) < MIN_ROWS or d.stations["locality_code"].isna().all():
        return None, f"no station rows ({len(d.stations)})"
    return Snapshot(d.snapshot_ts or p.stem, d), None


@dataclass
class Snapshot:
    ts: str
    data: ElectionData

    @property
    def table(self) -> pd.DataFrame:
        return self.data.stations


def load_series(kind: str = "expb", skipped: list[str] | None = None) -> list[Snapshot]:
    """Every usable snapshot, oldest first; names and reasons of rejected files go to `skipped` when given."""
    out = []
    for p in snapshot_files(kind):
        snap, reason = load_snapshot(p)
        if snap is None:
            if skipped is not None:
                skipped.append(f"{p.name}: {reason}")
            continue
        out.append(snap)
    return out


def diff(a: pd.DataFrame, b: pd.DataFrame, parties: list[str]) -> dict[str, pd.DataFrame]:
    """Rows added, removed and changed between two station tables (same election)."""
    cols = ["eligible", "voters", "invalid", "valid", *[f"v_{p}" for p in parties if f"v_{p}" in a.columns and f"v_{p}" in b.columns]]
    ia = a.set_index(KEY)
    ib = b.set_index(KEY)
    added = ib.loc[~ib.index.isin(ia.index)].reset_index()
    removed = ia.loc[~ia.index.isin(ib.index)].reset_index()
    common = ia.index.intersection(ib.index)
    xa = ia.loc[common, cols].astype("Int64")
    xb = ib.loc[common, cols].astype("Int64")
    changed_mask = (xa != xb).fillna(True)
    changed_rows = changed_mask.any(axis=1)
    recs = []
    for key, row in changed_mask[changed_rows].iterrows():
        loc, station = key[0], key[1]  # type: ignore[index]
        for c in cols:
            if bool(row[c]):
                recs.append({"locality_code": loc, "station": station, "field": c,
                             "old": xa.at[key, c], "new": xb.at[key, c]})
    changed = pd.DataFrame(recs, columns=["locality_code", "station", "field", "old", "new"])
    if len(changed):
        names = ib.loc[common, ["locality_name"]].reset_index().drop_duplicates(KEY)
        changed = changed.merge(names, on=KEY, how="left")
    return {"added": added, "removed": removed, "changed": changed}


def arrival_table(series: list[Snapshot]) -> pd.DataFrame:
    """First snapshot in which each station appears, plus its final values."""
    first: dict[tuple[object, object], str] = {}
    for s in series:
        for key in zip(s.table["locality_code"], s.table["station"], strict=True):
            first.setdefault(key, s.ts)
    last = series[-1].table.set_index(KEY)
    arr = pd.DataFrame({"first_seen": pd.Series(first)})
    arr.index = pd.MultiIndex.from_tuples(arr.index, names=KEY)
    out = last.join(arr, how="left").reset_index()
    order = {ts: i for i, ts in enumerate(sorted({s.ts for s in series}))}
    out["first_seen_rank"] = out["first_seen"].map(order)
    return out
