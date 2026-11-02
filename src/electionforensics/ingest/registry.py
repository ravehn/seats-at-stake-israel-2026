"""Registry of Knesset elections: dates, governments, raw-file locations.

Facts here are metadata the analysis needs and cannot read from the files themselves. Each entry
says where it comes from; `verified` marks entries confirmed against a primary source in this
project (see docs/research/). Anything unverified is used only for labelling, never for inference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from electionforensics.config import DATA_RAW


@dataclass(frozen=True)
class Election:
    knesset: int
    date: date
    #: Party (ballot letters) of the incumbent prime minister on election day.
    pm_party: str
    pm_name: str
    #: True when the incumbent prime minister was Benjamin Netanyahu (calibration split requested by the author).
    netanyahu_incumbent: bool
    #: Ballot letters of the list with the most valid votes (plurality) — used as "leader" where a
    #: single leader is required (Klimek/Shpilkin-type tests). Verified from the files at load time.
    plurality_party: str
    schema: str
    notes: str = ""
    verified: bool = False
    #: Raw station-level file (relative to data/raw). For 26 the snapshots directory.
    stations_file: str = ""
    #: Raw locality-level file (relative to data/raw), if any.
    localities_file: str = ""
    #: Locality codes / names used for externally cast ("double") envelopes in the station file.
    external_codes: tuple[int, ...] = field(default_factory=tuple)


ELECTIONS: dict[int, Election] = {
    16: Election(16, date(2003, 1, 28), "מחל", "Ariel Sharon", False, "מחל", "xls16",
                 "xls, sheet TOZAOT; eligible column 'בוחרים'; external envelopes under locality code 0",
                 stations_file="datagov/498b48e9-5af6-474d-b7a4-5ac1e21d3a08.xls", external_codes=(0,)),
    17: Election(17, date(2006, 3, 28), "כן", "Ehud Olmert (acting PM)", False, "כן", "xls17",
                 "xls, sheet kalfiyot; NO locality code, NO eligible count, NO invalid column (derived); has address",
                 stations_file="datagov/70f8bc93-8d98-4c20-ad7c-768af713f1c5.xls", external_codes=(0,)),
    18: Election(18, date(2009, 2, 10), "כן", "Ehud Olmert", False, "כן", "csv18",
                 "cp1255; columns 'סמל קלפי', \"בז''ב\"; valid before invalid; external envelopes aggregated in one row (code 0)",
                 stations_file="datagov/840edb33-90ac-4176-8ad9-4cdcb8e5caa5.csv", external_codes=(0,)),
    19: Election(19, date(2013, 1, 22), "מחל", "Benjamin Netanyahu", True, "מחל", "csv19",
                 "utf-8-sig; 7-field prefix with 'מספר קלפי'; external envelopes code 875",
                 stations_file="datagov/432d3185-545a-41d9-8c72-d10ee515919c.csv",
                 localities_file="datagov/c20cdcef-4d42-4241-a41b-6ca7ae51002d.csv", external_codes=(875,)),
    20: Election(20, date(2015, 3, 17), "מחל", "Benjamin Netanyahu", True, "מחל", "csv19",
                 "utf-8-sig stations / cp1255 localities; external envelopes code 875",
                 stations_file="datagov/c3db5581-f48d-45fc-b221-e7635e940c41.csv",
                 localities_file="datagov/929b50c6-f455-4be2-b438-ec6af01421f2.csv", external_codes=(875,)),
    21: Election(21, date(2019, 4, 9), "מחל", "Benjamin Netanyahu", True, "מחל", "csv19",
                 "cp1255; external envelopes code 99999",
                 stations_file="datagov/f79f9ba5-fe12-4b90-96cc-916f1b7c1c34.csv",
                 localities_file="datagov/1a1c7b2b-e819-4ba9-b159-d68e3566c58b.csv", external_codes=(99999,)),
    22: Election(22, date(2019, 9, 17), "מחל", "Benjamin Netanyahu", True, "פה", "csv22",
                 "cp1255; 11-field prefix incl. site key 'ריכוז'; external envelopes code 9999",
                 stations_file="datagov/22f3a195-3a79-436c-be23-cb606bc7b398.csv",
                 localities_file="datagov/bd22cd14-138c-4917-931a-ef628c2a5a30.csv", external_codes=(9999,)),
    23: Election(23, date(2020, 3, 2), "מחל", "Benjamin Netanyahu", True, "מחל", "csv22",
                 "cp1255",
                 stations_file="datagov/3b9e911a-2e90-4587-b209-84171664056b.csv",
                 localities_file="datagov/3dc36e20-25d6-4496-ba6a-71d9bc917349.csv", external_codes=(9999,)),
    24: Election(24, date(2021, 3, 23), "מחל", "Benjamin Netanyahu", True, "מחל", "csv22",
                 "cp1255; split boxes appear as decimals (1.1, 1.2)",
                 stations_file="datagov/419be3b0-fd30-455a-afc0-034ec36be990.csv",
                 localities_file="datagov/9921a347-8466-4ef4-81f9-22523c5c4632.csv", external_codes=(9999,)),
    25: Election(25, date(2022, 11, 1), "פה", "Yair Lapid (caretaker)", False, "מחל", "csv22",
                 "utf-8-sig",
                 stations_file="datagov/cc223336-07bc-485d-b160-62df92967c0a.csv",
                 localities_file="datagov/b392b8ee-ba45-4ea0-bfed-f03a1a36e99c.csv", external_codes=(9999,)),
    26: Election(26, date(2026, 10, 27), "מחל", "Benjamin Netanyahu", True, "מחל", "csv26",
                 "utf-8-sig; 13-field prefix (+ 'חיצוניות', 'קלפי אחרת'); partial snapshots during the count",
                 stations_file="knesset26/snapshots", localities_file="knesset26/snapshots", external_codes=(9999,)),
}

#: Government/incumbency facts above: dates and prime ministers are public record; plurality parties
#: are recomputed from the data and compared with this table by the ingest tests.


def stations_path(knesset: int) -> Path:
    e = ELECTIONS[knesset]
    p = DATA_RAW / e.stations_file
    if not p.exists():
        raise FileNotFoundError(f"raw station file for Knesset {knesset} not found: {p}")
    return p


def snapshot_files(kind: str = "expb") -> list[Path]:
    """Timestamped snapshots of the 2026 count, oldest first."""
    d = DATA_RAW / "knesset26" / "snapshots"
    return sorted(d.glob(f"{kind}_*.csv"))


def datagov_manifest() -> dict[str, object]:
    p = DATA_RAW / "datagov" / "MANIFEST.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
