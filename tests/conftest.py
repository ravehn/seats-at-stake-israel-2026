"""Shared fixtures: synthetic elections with known answers, built through the real harmonize path."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from electionforensics.ingest.cec import ElectionData, harmonize
from electionforensics.model.features import Features, build_features

PARTIES = ["מחל", "פה", "ג", "ודעם"]


def make_election(rows: list[dict[str, object]], knesset: int = 25) -> ElectionData:
    """rows: dicts with locality_code, locality_name, station, site, eligible, voters, invalid and party votes."""
    raw = pd.DataFrame([{
        "סמל ועדה": "1", "ברזל": str(i), "שם ישוב": r["locality_name"], "סמל ישוב": str(r["locality_code"]),
        "קלפי": str(r["station"]), "ריכוז": str(r.get("site", 1)), "שופט": "0",
        "בזב": str(r["eligible"]), "מצביעים": str(r["voters"]), "פסולים": str(r.get("invalid", 0)),
        "כשרים": str(int(r["voters"]) - int(r.get("invalid", 0))),
        **{p: str(r.get(p, 0)) for p in PARTIES},
    } for i, r in enumerate(rows)])
    table, parties, warnings = harmonize(raw, knesset)
    return ElectionData(knesset, table, parties, source=None, sha256="synthetic", encoding="synthetic", warnings=warnings)  # type: ignore[arg-type]


def tiny_cbs(codes: list[int]) -> pd.DataFrame:
    return pd.DataFrame({
        "locality_code": pd.array(codes, dtype="Int64"), "cbs_name": [f"loc{c}" for c in codes],
        "district": "מרכז", "district_code": 4, "municipal_status": "עירייה", "population": 10000,
        "arab_share": 0.0, "religion": "jewish", "religion_code": 1, "pop_jews_others": 10000, "pop_arabs": 0,
    })


def random_rows(rng: np.random.Generator, n_localities: int = 20, stations_per: int = 8, turnout: float = 0.65) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for loc in range(n_localities):
        code = 1000 + loc
        shares = rng.dirichlet(np.ones(len(PARTIES)) * 4)
        for st in range(stations_per):
            eligible = int(rng.integers(300, 800))
            voters = int(rng.binomial(eligible, turnout))
            invalid = int(rng.binomial(voters, 0.01))
            valid = voters - invalid
            votes = rng.multinomial(valid, shares)
            rows.append({"locality_code": code, "locality_name": f"loc{code}", "station": st + 1, "site": st // 4 + 1,
                         "eligible": eligible, "voters": voters, "invalid": invalid, **dict(zip(PARTIES, votes.tolist(), strict=True))})
    return rows


def features_from_rows(rows: list[dict[str, object]]) -> Features:
    data = make_election(rows)
    codes = sorted({int(r["locality_code"]) for r in rows})  # type: ignore[call-overload]
    return build_features(data, tiny_cbs(codes))


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(12345)


@pytest.fixture
def clean_features(rng: np.random.Generator) -> Features:
    return features_from_rows(random_rows(rng))
