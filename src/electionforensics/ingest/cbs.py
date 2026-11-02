"""Central Bureau of Statistics localities file (קובץ היישובים) → locality attributes.

Source: CBS "bycode" 2024 (mirror fetched 2026-10-28, see data/raw/cbs/). Keyed by the same
locality code the CEC uses (סמל יישוב). Columns kept: district, sub-district, municipal status,
religion code, population (total, Jews and others, Arabs), coordinates, local-authority code/name.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from electionforensics.config import DATA_RAW

CBS_FILE = DATA_RAW / "cbs" / "bycode2024_idi_mirror.xlsx"

COLS = {
    "שם יישוב": "cbs_name",
    "סמל יישוב": "locality_code",
    "סמל מחוז": "district_code",
    "שם מחוז": "district",
    "סמל נפה": "subdistrict_code",
    "שם נפה": "subdistrict",
    "סמל מעמד מונציפאלי": "municipal_status_code",
    "שם מעמד מונציפאלי": "municipal_status",
    "דת יישוב": "religion_code",
    "סך הכל אוכלוסייה 2024": "population",
    "יהודים ואחרים": "pop_jews_others",
    "ערבים": "pop_arabs",
    "קואורדינטות": "coordinates",
    "סמל רשות מקומית": "authority_code",
    "שם רשות מקומית": "authority_name",
    "שם יישוב באנגלית": "name_en",
}

#: CBS "דת יישוב" codes as they behave in the 2024 file (checked 2026-10-28 against names):
#: 1 Jewish localities; 2 Arab localities of any religion (Abu Ghosh, Nazareth, Yirka and Daliyat
#: al-Karmel all carry 2); 3 Bedouin tribes (all 28 names end in "(שבט)"); 4 mixed cities (Haifa,
#: Jerusalem, Lod, Ramla, Acre, Tel Aviv-Yafo, Nof HaGalil, Ma'alot-Tarshiha, Neve Shalom);
#: empty = non-residential statistical areas. Religion within the Arab sector (Druze, Christian)
#: therefore needs a separate table.
RELIGION = {1: "jewish", 2: "arab", 3: "bedouin", 4: "mixed"}


def load_cbs(path: Path = CBS_FILE) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns=COLS)[list(COLS.values())]
    for c in ("locality_code", "district_code", "subdistrict_code", "municipal_status_code", "religion_code",
              "population", "pop_jews_others", "pop_arabs", "authority_code"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["religion"] = df["religion_code"].map(RELIGION).fillna("unknown")
    df["arab_share"] = (df["pop_arabs"] / df["population"]).astype("Float64")
    df = df.dropna(subset=["locality_code"]).drop_duplicates("locality_code")
    return df.reset_index(drop=True)


def sector_from_cbs(cbs: pd.DataFrame) -> pd.Series:
    """Coarse sector label per locality from CBS attributes alone.

    jewish / arab / bedouin / mixed. Haredi and Druze are assigned later (voting history and a
    curated list respectively) because the CBS file does not carry them.
    """
    s = pd.Series("jewish", index=cbs.index, dtype="object")
    s[cbs["religion"] == "arab"] = "arab"
    s[cbs["religion"] == "bedouin"] = "bedouin"
    s[cbs["religion"] == "mixed"] = "mixed"
    s[(cbs["religion"] == "unknown") & (cbs["arab_share"] >= 0.8)] = "arab"
    s[(cbs["religion"] == "jewish") & (cbs["arab_share"] >= 0.5)] = "mixed"
    return s
