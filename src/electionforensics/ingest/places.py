"""Polling-place register: where each ballot box stood — place name, address, site — per election.

The CEC publishes the register before election day as part of the voter guide ("רשימת מקומות
הקלפי"). It is the first thing to consult when a station differs from its building peers: the most
frequent lawful explanation of a flag is that the station serves a different public than the boxes
next to it (a seminary, a retirement home, a separate school), and the register says what the
building is. Files live in `data/raw` with a MANIFEST next to them:

    26: knesset26/places/Polling_Station.xlsx — one row per ballot box (split boxes as 576.1, 576.2 …),
        site key `סמל רכוז` (= `ריכוז` of the result file), address, place name, accessibility, Arabic
        printing, eligible voters; state of 25.10.2026 (boxes merged or moved on election day are not
        reflected). knesset26/places/Rikuzim.xlsx — one row per site with the place type (`סווג מקום`)
        and ITM coordinates.
    25: knesset25/kalpiplaces_25.xlsx — the CEC file of 2022 from a public mirror (see its manifest).

Result tables are joined on (locality_code, station_num): the split boxes of one station number share
one place, and the result file carries the box ("14.2") while the editor asks about the station (14).
Downloaded files are untrusted data: parsed, never executed.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from electionforensics.config import DATA_RAW
from electionforensics.ingest.cec import _station_parts, _to_int

#: Columns every register carries, in this order (more may follow).
PLACE_COLS = ["locality_code", "station_num", "place_name", "address", "locality_name"]
#: Columns `attach_places` adds to a result table.
ATTACH_COLS = ["place_name", "address", "place_type"]

PLACES_FILES: dict[int, str] = {
    26: "knesset26/places/Polling_Station.xlsx",
    25: "knesset25/kalpiplaces_25.xlsx",
}
SITES_FILES: dict[int, str] = {26: "knesset26/places/Rikuzim.xlsx"}

#: Header spellings seen in the CEC workbooks → canonical field.
HEADER_MAP: dict[str, str] = {
    "מס' ברזל": "box_serial",
    "סמל ועדה": "committee",
    "שם ועדה": "committee_name",
    "סמל ישוב בחירות": "locality_code",
    "שם ישוב בחירות": "locality_name",
    "סמל קלפי": "station",
    "סמל רכוז": "site",
    "כתובת קלפי": "address",
    "מקום קלפי": "place_name",
    "סווג מקום": "place_type",
    "נגישה": "accessible",
    "נגישה מיוחדת": "accessible_special",
    "הדפסה בערבית": "arabic_print",
    "בוחרי כנסת": "eligible",
    "בוחרי כנסת בפועל": "eligible",
    "מס' קלפיות נטו": "n_boxes",
    "קלפיות נגישות": "n_accessible",
    "קלפיות נגישה מיוחדת": "n_accessible_special",
    "סמל גוש ישובים": "locality_group",
    "שם גוש ישובים": "locality_group_name",
    "סמל גוש ריכוזים": "site_group",
    "שם גוש ריכוזים": "site_group_name",
    "X": "x",
    "Y": "y",
}
FLAG_COLS = ("accessible", "accessible_special", "arabic_print")


def _read_sheet(path: Path) -> pd.DataFrame:
    """First sheet of a CEC workbook, headers mapped, the trailing 'end of table' column and blank rows dropped."""
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    keep = {c: HEADER_MAP[c] for c in df.columns if c in HEADER_MAP}
    df = df[list(keep)].rename(columns=keep)
    df = df.apply(lambda s: s.fillna("").astype(str).str.strip())
    blank = (df == "").all(axis=1)
    cleaned: pd.DataFrame = df[~blank].reset_index(drop=True)
    return cleaned


def _flag(s: pd.Series) -> pd.Series:
    """The CEC marks yes with the letter כ (כן) and no with a blank."""
    return s.fillna("").astype(str).str.strip().eq("כ")


def read_boxes(path: Path) -> pd.DataFrame:
    """One row per ballot box as the workbook lists it, with station parts split as in the result files."""
    df = _read_sheet(path)
    for c in ("locality_code", "station", "address", "place_name"):
        if c not in df.columns:
            raise ValueError(f"{path.name}: column for '{c}' not found; header is {list(df.columns)}")
    out = pd.DataFrame(index=df.index)
    out["locality_code"] = _to_int(df["locality_code"])
    out["locality_name"] = df.get("locality_name", "")
    out["station"], out["station_num"], out["station_sub"] = _station_parts(df["station"])
    out["site"] = _to_int(df["site"]) if "site" in df else pd.Series(pd.NA, index=df.index, dtype="Int64")
    out["place_name"] = df["place_name"]
    out["address"] = df["address"]
    for c in FLAG_COLS:
        out[c] = _flag(df[c]) if c in df else False
    out["eligible"] = _to_int(df["eligible"]) if "eligible" in df else pd.Series(pd.NA, index=df.index, dtype="Int64")
    out["committee"] = _to_int(df["committee"]) if "committee" in df else pd.Series(pd.NA, index=df.index, dtype="Int64")
    # a row without locality or station is a footer, not a box
    good = out["locality_code"].notna() & out["station_num"].notna()
    return out[good].reset_index(drop=True)


def boxes_to_places(boxes: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Collapse boxes to one row per (locality_code, station_num). Returns (register, warnings).

    Split boxes of one station share the place; when the workbook disagrees (different address for
    14.1 and 14.2) the first row wins and the conflict is reported.
    """
    key = ["locality_code", "station_num"]
    g = boxes.groupby(key, sort=True)
    first = g.first()
    conflicts = g.agg(n_addr=("address", "nunique"), n_place=("place_name", "nunique"))
    bad = conflicts[(conflicts["n_addr"] > 1) | (conflicts["n_place"] > 1)]
    warnings = [f"{int(loc)}/{int(num)}: boxes list different places" for loc, num in bad.index]
    out = first[["place_name", "address", "locality_name", "site", "committee"]].copy()
    out["n_boxes"] = g.size()
    out["eligible"] = g["eligible"].sum(min_count=1).astype("Int64")
    for c in FLAG_COLS:
        out[c] = g[c].any()
    out = out.reset_index()
    out["locality_code"] = out["locality_code"].astype("Int64")
    out["station_num"] = out["station_num"].astype("Int64")
    cols = PLACE_COLS + [c for c in out.columns if c not in PLACE_COLS]
    return out[cols], warnings


def places_path(knesset: int) -> Path:
    if knesset not in PLACES_FILES:
        raise FileNotFoundError(f"no polling-place register registered for Knesset {knesset}")
    p = DATA_RAW / PLACES_FILES[knesset]
    if not p.exists():
        raise FileNotFoundError(f"polling-place register for Knesset {knesset} not found: {p}")
    return p


def load_places(knesset: int, path: Path | None = None) -> pd.DataFrame:
    """The register for one election: one row per (locality_code, station_num), PLACE_COLS first.

    Further columns: site, committee, n_boxes (split boxes under that number), eligible (sum over
    them), accessible / accessible_special / arabic_print (any box).
    """
    boxes = read_boxes(path or places_path(knesset))
    places, _warnings = boxes_to_places(boxes)
    return places


def load_sites(knesset: int = 26, path: Path | None = None) -> pd.DataFrame:
    """One row per polling site (ריכוז): address, place name and type, eligible voters, box counts, ITM x/y."""
    if path is None:
        if knesset not in SITES_FILES:
            raise FileNotFoundError(f"no site list registered for Knesset {knesset}")
        path = DATA_RAW / SITES_FILES[knesset]
    df = _read_sheet(path)
    out = pd.DataFrame(index=df.index)
    out["locality_code"] = _to_int(df["locality_code"])
    out["locality_name"] = df.get("locality_name", "")
    out["site"] = _to_int(df["site"])
    for c in ("address", "place_name", "place_type", "site_group_name"):
        out[c] = df.get(c, "")
    for c in ("eligible", "n_boxes", "n_accessible", "n_accessible_special"):
        out[c] = _to_int(df[c]) if c in df else pd.Series(pd.NA, index=df.index, dtype="Int64")
    for c in ("x", "y"):
        out[c] = pd.to_numeric(df[c], errors="coerce") if c in df else float("nan")
    good = out["locality_code"].notna() & out["site"].notna()
    return out[good].reset_index(drop=True)


def attach_places(table: pd.DataFrame, knesset: int, places: pd.DataFrame | None = None,
                  sites: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add `place_name`, `address`, `place_type` to a table that has `locality_code` and `station`
    (or `station_num`). Unknown stations get empty strings; the table's own columns are untouched.

    `places` / `sites` may be passed to avoid re-reading the workbooks; when the register for the
    election does not exist the columns are added empty, so callers need no special case.
    """
    out = table.copy()
    if out.empty:
        for c in ATTACH_COLS:
            out[c] = pd.Series(dtype="object")
        return out
    if places is None:
        try:
            places = load_places(knesset)
        except FileNotFoundError:
            places = pd.DataFrame(columns=[*PLACE_COLS, "site"])
    if sites is None:
        try:
            sites = load_sites(knesset)
        except FileNotFoundError:
            sites = pd.DataFrame(columns=["locality_code", "site", "place_type"])
    key_num = out["station_num"] if "station_num" in out else _station_parts(out["station"])[1]
    key = pd.DataFrame({"locality_code": _to_int(out["locality_code"]), "station_num": pd.Series(key_num, index=out.index).astype("Int64")})
    reg = places.drop_duplicates(["locality_code", "station_num"])[["locality_code", "station_num", "place_name", "address", "site"]]
    if not sites.empty:
        types = sites.drop_duplicates(["locality_code", "site"])[["locality_code", "site", "place_type"]].copy()
        types["locality_code"] = _to_int(types["locality_code"])
        types["site"] = _to_int(types["site"])
        reg = reg.merge(types, on=["locality_code", "site"], how="left")
    else:
        reg = reg.assign(place_type="")
    merged = key.merge(reg, on=["locality_code", "station_num"], how="left")
    merged.index = out.index
    for c in ATTACH_COLS:
        out[c] = merged[c].fillna("").astype(str) if c in merged else ""
    return out


def _text(v: object) -> str:
    return v.strip() if isinstance(v, str) else ""


def place_label(row: pd.Series | dict[str, object]) -> str:
    """Human-readable 'place, address (type)' for one row of an attached table ('' when unknown)."""
    name, addr, kind = (_text(row.get(c, "")) for c in ATTACH_COLS)
    label = ", ".join(s for s in (name, addr) if s)
    return f"{label} ({kind})" if label and kind else label
