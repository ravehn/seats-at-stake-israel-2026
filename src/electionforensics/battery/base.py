"""Common interface of the battery.

A test takes the Features of one election and returns a TestResult: a per-unit table (station,
locality, or station × party) with the statistic, a score or p-value, an effect size with its
uncertainty where the method defines one, and an election-level summary. Tests do not decide what
is "anomalous": thresholds are set by the calibration layer from the empirical distribution of the
same statistic across elections 16–25, and the presentation layer applies the two tiers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from electionforensics.config import DATA_PROCESSED
from electionforensics.model.features import Features

UNIT_KEYS = ["knesset", "locality_code", "locality_name", "station", "sector"]


@dataclass
class TestResult:
    test_id: str
    name: str
    knesset: int
    level: str  # "station" | "station_party" | "site" | "locality" | "election"
    units: pd.DataFrame
    summary: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Secondary tables (e.g. site-level aggregates), saved as <test_id>_<name>.parquet.
    extra: dict[str, pd.DataFrame] = field(default_factory=dict)

    def save(self, root: Path = DATA_PROCESSED) -> Path:
        d = root / f"knesset{self.knesset}"
        d.mkdir(parents=True, exist_ok=True)
        self.units.to_parquet(d / f"{self.test_id}.parquet", index=False)
        for name, df in self.extra.items():
            df.to_parquet(d / f"{self.test_id}_{name}.parquet", index=False)
        meta = {"test_id": self.test_id, "name": self.name, "knesset": self.knesset, "level": self.level,
                "summary": self.summary, "params": self.params, "notes": self.notes, "n_units": len(self.units)}
        (d / f"{self.test_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        return d / f"{self.test_id}.parquet"


def _json_default(o: Any) -> Any:
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return str(o)


TestFn = Callable[..., TestResult]
BATTERY: dict[str, TestFn] = {}


def register(test_id: str) -> Callable[[TestFn], TestFn]:
    def deco(fn: TestFn) -> TestFn:
        BATTERY[test_id] = fn
        return fn
    return deco


def run_battery(feat: Features, tests: list[str] | None = None, **kwargs: Any) -> dict[str, TestResult]:
    # import the modules so that they register themselves
    from electionforensics.battery import (  # noqa: F401
        t01_validity,
        t02_fingerprint,
        t03_klimek,
        t04_shpilkin,
        t05_integer,
        t06_coarse_fractions,
        t07_last_digit,
        t08_hierarchical,
        t09_siblings,
        t10_zero_peers,
        t11_invalid,
        t12_history_shift,
        t13_small_stations,
        t14_count_dynamics,
        t15_double_envelopes,
        t16_representation,
        t17_displacement,
    )

    ids = tests or sorted(BATTERY)
    out: dict[str, TestResult] = {}
    for tid in ids:
        fn = BATTERY[tid]
        out[tid] = fn(feat, **kwargs.get(tid, {}))
    return out
