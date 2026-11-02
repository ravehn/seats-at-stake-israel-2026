"""Load Central Elections Committee per-station result files into one harmonized table.

Five schema generations (see registry.py notes). Harmonized columns:

    knesset, locality_code, locality_name, station, station_num, station_sub, site, committee,
    box_serial, judge_flag, eligible, voters, invalid, valid, external_envelopes, other_station,
    address, is_external, v_<letters> ...

`is_external` marks rows of externally cast ("double") envelopes, which the CEC publishes under a
pseudo-locality (code 0 / 875 / 99999 / 9999 depending on the year) and which have no electorate.
Vote columns are prefixed `v_` and keep the Hebrew ballot letters.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from electionforensics.ingest.registry import ELECTIONS, snapshot_files, stations_path

PREFIX_COLS = [
    "knesset", "locality_code", "locality_name", "station", "station_num", "station_sub", "site",
    "committee", "box_serial", "judge_flag", "eligible", "voters", "invalid", "valid",
    "external_envelopes", "other_station", "address", "is_external",
]

#: Header spellings seen in the files → canonical field.
HEADER_MAP: dict[str, str] = {
    "סמל ועדה": "committee",
    "ברזל": "box_serial",
    "שם ישוב": "locality_name",
    "סמל ישוב": "locality_code",
    "קלפי": "station",
    "מספר קלפי": "station",
    "סמל קלפי": "station",
    "ריכוז": "site",
    "שופט": "judge_flag",
    "בזב": "eligible",
    "בז''ב": "eligible",
    'בז"ב': "eligible",
    "בוחרים": "eligible",
    "מצביעים": "voters",
    "פסולים": "invalid",
    "כשרים": "valid",
    "חיצוניות": "external_envelopes",
    "קלפי אחרת": "other_station",
    "כתובת": "address",
    "ת. עדכון": "_updated",
}

EXTERNAL_NAME_RE = re.compile(r"מעטפות")


@dataclass
class ElectionData:
    knesset: int
    stations: pd.DataFrame
    parties: list[str]
    source: Path
    sha256: str
    encoding: str
    snapshot_ts: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def regular(self) -> pd.DataFrame:
        """Stations with an electorate (excludes external-envelope pseudo-stations)."""
        return self.stations[~self.stations["is_external"]]


def _sniff_encoding(path: Path) -> str:
    head = path.read_bytes()[:4096]
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1255"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_raw(path: Path) -> tuple[pd.DataFrame, str]:
    if path.suffix.lower() == ".xls":
        df = pd.read_excel(path, sheet_name=0, dtype=str, engine="xlrd")
        enc = "xls"
    else:
        enc = _sniff_encoding(path)
        df = pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    # drop empty trailing columns produced by a trailing comma, and rows that carry nothing
    keep = [c for c in df.columns if c and not c.startswith("Unnamed")]
    df = df[keep]
    blank = df.apply(lambda s: s.isna() | (s.astype(str).str.strip() == "")).all(axis=1)
    return df[~blank].reset_index(drop=True), enc


def _station_parts(raw: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    s = raw.fillna("").astype(str).str.strip()
    # xls floats arrive as "1010.0"; genuine split boxes are "14.1", "14.2"
    s = s.where(~s.str.fullmatch(r"\d+\.0"), s.str.replace(r"\.0$", "", regex=True))
    num = pd.Series(pd.to_numeric(s.str.split(".").str[0], errors="coerce"), index=s.index).astype("Int64")
    sub = pd.Series(pd.to_numeric(s.str.split(".").str[1], errors="coerce"), index=s.index).fillna(0).astype("Int64")
    return s, num, sub


def _to_int(col: pd.Series) -> pd.Series:
    s = col.fillna("").astype(str).str.strip()
    s = s.mask(s == "", None)
    return pd.Series(pd.to_numeric(s, errors="coerce"), index=col.index).astype("Int64")


def _na_int(index: pd.Index) -> pd.Series:
    return pd.Series(pd.NA, index=index, dtype="Int64")


def _party_counts(col: pd.Series) -> pd.Series:
    text = col.fillna("").astype(str).str.strip().replace("", "0")
    numeric = pd.to_numeric(text, errors="coerce")
    bad = (~np.isfinite(numeric)) | (numeric < 0) | (numeric % 1 != 0) | (numeric >= 2**63)
    if bad.any():
        row = bad[bad].index[0]
        raise ValueError(f"invalid party count in column {col.name!r}, row index {row!r}: {text.loc[row]!r}")
    return numeric.astype("int64")


def harmonize(df_raw: pd.DataFrame, knesset: int) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Map a raw CEC table to the harmonized schema. Returns (table, party letters, warnings)."""
    warnings: list[str] = []
    renamed: dict[str, str] = {}
    parties: list[str] = []
    for c in df_raw.columns:
        key = c.strip()
        if key in HEADER_MAP:
            renamed[c] = HEADER_MAP[key]
        else:
            parties.append(key)
    df = df_raw.rename(columns=renamed)
    if "_updated" in df.columns:
        df = df.drop(columns=["_updated"])

    out = pd.DataFrame(index=df.index)
    out["knesset"] = knesset
    out["locality_code"] = _to_int(df["locality_code"]) if "locality_code" in df else _na_int(df.index)
    out["locality_name"] = df["locality_name"].fillna("").astype(str).str.strip() if "locality_name" in df else ""
    if "station" in df:
        station, num, sub = _station_parts(df["station"])
    else:  # locality-level files have no station column
        station = pd.Series([""] * len(df), index=df.index, dtype="object")
        num = _na_int(df.index)
        sub = _na_int(df.index)
    out["station"], out["station_num"], out["station_sub"] = station, num, sub
    for src, dst in [("site", "site"), ("committee", "committee"), ("box_serial", "box_serial"),
                     ("judge_flag", "judge_flag"), ("eligible", "eligible"), ("voters", "voters"),
                     ("invalid", "invalid"), ("valid", "valid"), ("external_envelopes", "external_envelopes"),
                     ("other_station", "other_station")]:
        out[dst] = _to_int(df[src]) if src in df else _na_int(df.index)
    out["address"] = df["address"].fillna("").astype(str).str.strip() if "address" in df else ""

    votes = df[parties].apply(_party_counts)
    if out["invalid"].isna().all() and out["voters"].notna().any():
        out["invalid"] = (out["voters"] - out["valid"]).astype("Int64")
        warnings.append("invalid votes derived as voters - valid (column absent)")
    if out["valid"].isna().all():
        out["valid"] = votes.sum(axis=1).astype("Int64")
        warnings.append("valid votes derived as sum of party votes (column absent)")

    codes = ELECTIONS[knesset].external_codes
    is_ext = out["locality_name"].str.contains(EXTERNAL_NAME_RE, regex=True, na=False)
    if out["locality_code"].notna().any():
        is_ext = is_ext | out["locality_code"].isin(list(codes)).fillna(False)
    out["is_external"] = is_ext.astype(bool)

    for p in parties:
        out[f"v_{p}"] = votes[p].to_numpy()
    # rows without any count are footers / blank lines, not data (a station row always has counts)
    no_data = out["voters"].isna() & out["valid"].isna()
    if no_data.any():
        warnings.append(f"dropped {int(no_data.sum())} row(s) without station id or counts")
        out = out[~no_data].reset_index(drop=True)
    return out, parties, warnings


def load_election(knesset: int, path: Path | None = None) -> ElectionData:
    """Load one election's station table. For 26 the latest snapshot unless `path` is given."""
    snapshot_ts: str | None = None
    if path is None:
        if knesset == 26:
            files = snapshot_files("expb")
            if not files:
                raise FileNotFoundError("no 2026 snapshots under data/raw/knesset26/snapshots")
            path = files[-1]
        else:
            path = stations_path(knesset)
    if knesset == 26:
        m = re.search(r"expb_(\d{8}T\d{6}Z)", path.name)
        snapshot_ts = m.group(1) if m else None
    raw, enc = _read_raw(path)
    table, parties, warnings = harmonize(raw, knesset)
    if knesset == 17:
        table = _fill_locality_codes_by_name(table, warnings)
    return ElectionData(knesset, table, parties, path, _sha256(path), enc, snapshot_ts, warnings)


def _fill_locality_codes_by_name(table: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    """The 17th Knesset file has no locality codes: map names via the neighbouring elections' files."""
    names: dict[str, int] = {}
    for k in (16, 18, 19, 20):
        try:
            other = load_election(k).stations
        except FileNotFoundError:
            continue
        for name, code in zip(other["locality_name"], other["locality_code"], strict=True):
            if pd.notna(code) and name and name not in names:
                names[name] = int(code)
    mapped = table["locality_name"].map(names)
    table = table.copy()
    table["locality_code"] = mapped.astype("Int64")
    missing = table.loc[~table["is_external"] & table["locality_code"].isna(), "locality_name"].nunique()
    if missing:
        warnings.append(f"17th Knesset: {missing} locality names without a code after name matching")
    return table


def load_all(knessets: list[int] | None = None) -> dict[int, ElectionData]:
    ks = knessets or sorted(ELECTIONS)
    return {k: load_election(k) for k in ks}


def load_localities(knesset: int, path: Path | None = None) -> pd.DataFrame:
    """Per-locality CEC file (expc), harmonized to locality_code, locality_name, eligible, voters, invalid, valid, v_*."""
    e = ELECTIONS[knesset]
    if path is None:
        if knesset == 26:
            files = snapshot_files("expc")
            if not files:
                raise FileNotFoundError("no 2026 expc snapshots")
            path = files[-1]
        else:
            if not e.localities_file:
                raise FileNotFoundError(f"no locality file registered for Knesset {knesset}")
            path = stations_path(knesset).parent / Path(e.localities_file).name
    raw, _ = _read_raw(path)
    table, _parties, _w = harmonize(raw, knesset)
    return table.drop(columns=["station", "station_num", "station_sub", "site", "box_serial", "judge_flag", "address"])
